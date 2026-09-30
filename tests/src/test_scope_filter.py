"""Scope filters from the filter workbooks -> per-layer SQL (ADR 0045)."""
from pathlib import Path

import scope_filter as sf
from core.validation_plan import CanonicalValidationPlan, ColumnMappingEntry
from silver.silver_sql_emitter import emit_query_set

_DOCS = Path(__file__).resolve().parents[2] / "docs" / "Excel-Files"


def _entry(source, target):
    return ColumnMappingEntry(source_column=source, source_type="", source_normalized=target.lower(),
                              target_column=target, target_type="", target_normalized=target.lower(),
                              match_method="configured")


def _discount_lines_plan():
    t = '"DISCOUNT_LINES"'
    return CanonicalValidationPlan(
        source_database="BRONZE_EDGE", source_schema="S", source_table="DISCOUNT_LINES",
        target_database="DEV_EDGE_SILVER", target_schema="S", target_table="INT_DISCOUNT_LINES",
        mappings=[
            _entry(f'{t}."ID"', "DISCOUNT_LINE_ID"),
            _entry(f'{t}."FACILITY_ID"', "FACILITY_ID"),
            _entry(f'{t}."CREATED_AT"', "CREATED_AT"),
            _entry(f'ROW_NUMBER() OVER (PARTITION BY {t}."ID" ORDER BY {t}."UPDATED_AT")', "SYS_VERSION"),
            _entry(f'{t}."_FIVETRAN_ACTIVE"', "IS_CURRENT"),
        ],
    )


def test_real_workbooks_parse_and_only_sheet_defects_block():
    pg = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")
    sl = sf.load_workbook(_DOCS / "Sitelink_bronze_filters.xlsx")
    assert len(pg) == 47 and len(sl) == 28
    # Exactly the two defective filter cells from ADR 0041 are blocked, not "fixed".
    assert sorted(k for k, s in pg.items() if s.blocked) == ["discount_plan_ledger_instances", "transactions"]
    assert not any(s.blocked for s in sl.values())
    assert pg["facilities"].mode == "full" and sl["accttypes"].mode == "full"
    # SiteLink prose -> 2-hop path, corp codes read from the Codes sheet.
    units = sl["units"]
    assert [(h.fk, h.parent, h.pk) for h in units.hops] == [("site_id", "sites", "site_id"), ("ownerid", "owners", "ownerid")]
    assert units.hops[-1].conditions[0].value == "('SLQA', 'STP2DEV', 'BIGSSO', 'CVIJAY')"


def test_bronze_render_source_and_snowflake_sides():
    """Bronze: the sheet's JOIN chain inside one subquery (the Bronze SELECT is
    AI-written with unqualified columns, so no JOIN in its outer FROM)."""
    spec = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")["contacts"]
    src, tgt, missing = sf.render_bronze(spec, "public", "DEV_EDGE_BRONZE", "STOREDGE_FMS_PUBLIC")
    assert missing == []
    assert src == ("tenant_id IN (SELECT t.id FROM public.tenants t JOIN public.facilities f "
                   "ON t.facility_id = f.id WHERE f.company_id IN (137,9088,501))")
    # Every joined Bronze table carries _FIVETRAN_ACTIVE = TRUE.
    assert tgt == ("TENANT_ID IN (SELECT t.ID FROM DEV_EDGE_BRONZE.STOREDGE_FMS_PUBLIC.TENANTS t "
                   "JOIN DEV_EDGE_BRONZE.STOREDGE_FMS_PUBLIC.FACILITIES f ON t.FACILITY_ID = f.ID "
                   "WHERE t._FIVETRAN_ACTIVE = TRUE AND f._FIVETRAN_ACTIVE = TRUE AND f.COMPANY_ID IN (137,9088,501))")


def test_to_sql_reads_like_the_sheet():
    spec = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")["contacts"]
    assert sf.to_sql(spec) == ("SELECT COUNT(*) FROM contacts\nJOIN tenants t ON contacts.tenant_id = t.id"
                               "\nJOIN facilities f ON t.facility_id = f.id\nWHERE f.company_id IN (137,9088,501)")


def test_live_columns_correct_names_and_flag_missing():
    spec = sf.load_workbook(_DOCS / "Sitelink_bronze_filters.xlsx")["sites"]
    _, tgt, missing = sf.render_bronze(spec, "dbo", "DB", "SL", live_target={"": ["OWNER_ID"], "owners": ["OWNER_ID", "S_CORP_CODE"]})
    assert missing == [] and "OWNER_ID IN (SELECT p1.OWNER_ID" in tgt and "p1.S_CORP_CODE IN" in tgt
    addresses = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")["addresses"]
    _, _, missing = sf.render_bronze(addresses, "public", "", "S", live_source={"": ["id", "addressable_id", "addressable_type"]})
    assert missing == ["base table.facility_id not found"]  # ADR 0041 defect #4 surfaces


def test_silver_real_joins_and_emitted_sql():
    plan = _discount_lines_plan()
    spec = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")["discount_lines"]
    r = sf.render_silver(spec, plan)
    assert r["missing"] == []
    assert r["bronze_joins"] == '\nJOIN "BRONZE_EDGE"."S"."FACILITIES" f ON "DISCOUNT_LINES"."FACILITY_ID" = f."ID"'
    assert r["bronze_where"] == ('"DISCOUNT_LINES"."CREATED_AT" >= ' "'2024-08-01'" ' AND f."_FIVETRAN_ACTIVE" = TRUE '
                                 'AND f."COMPANY_ID" IN (137,9088,501)')
    assert r["silver_joins"] == ('\nJOIN "DEV_EDGE_SILVER"."S"."INT_FACILITIES" f '
                                 'ON "INT_DISCOUNT_LINES"."FACILITY_ID" = f."FACILITY_ID"')
    assert r["silver_where"] == ('"INT_DISCOUNT_LINES"."CREATED_AT" >= ' "'2024-08-01'" ' AND f."IS_CURRENT" = TRUE '
                                 'AND f."COMPANY_ID" IN (137,9088,501)')
    plan.population_scope["scope_joins"] = {"bronze": r["bronze_joins"], "silver": r["silver_joins"]}
    plan.source_filter, plan.target_filter = r["bronze_where"], r["silver_where"]
    qs = emit_query_set(plan)
    assert qs.main_validation_source.endswith(
        f'FROM "BRONZE_EDGE"."S"."DISCOUNT_LINES"{r["bronze_joins"]}\nWHERE {r["bronze_where"]}'
        '\nQUALIFY "DISCOUNT_LINES"."_FIVETRAN_ACTIVE" = TRUE'
    )
    # Silver columns are qualified once another table is joined in.
    assert 'COALESCE(CAST("INT_DISCOUNT_LINES"."FACILITY_ID" AS STRING)' in qs.main_validation_target
    assert qs.main_validation_target.endswith(
        f'"INT_DISCOUNT_LINES"{r["silver_joins"]}\nWHERE "INT_DISCOUNT_LINES"."IS_CURRENT" = TRUE\n  AND {r["silver_where"]}'
    )
    assert qs.row_count_source.endswith(
        f'{r["bronze_joins"]}\nWHERE "DISCOUNT_LINES"."_FIVETRAN_ACTIVE" = TRUE\n  AND {r["bronze_where"]}')
    assert qs.row_count_target.endswith(
        f'{r["silver_joins"]}\nWHERE "INT_DISCOUNT_LINES"."IS_CURRENT" = TRUE\n  AND {r["silver_where"]}')


def test_silver_gate_when_fk_not_passed_through():
    plan = _discount_lines_plan()
    plan.mappings = [m for m in plan.mappings if m.target_column != "FACILITY_ID"]
    spec = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")["discount_lines"]
    assert sf.render_silver(spec, plan)["missing"] == ["base table: no column for facility_id"]


def test_ai_rewrite_goes_through_the_parser():
    spec = sf.load_workbook(_DOCS / "Sitelink_bronze_filters.xlsx")["units"]
    good = sf.from_ai_sql(spec, "SELECT COUNT(*) FROM Units j JOIN Sites s ON j.SiteID = s.SiteID "
                                "JOIN Owners o ON s.OwnerID = o.OwnerID WHERE o.sCorpCode IN ('SLQA')", "m")
    assert not good.blocked and [h.alias for h in good.hops] == ["s", "o"]
    assert good.notes[0].startswith("interpreted by AI (m)") and good.raw_text == spec.raw_text
    bad = sf.from_ai_sql(spec, "SELECT COUNT(*) FROM Sites s WHERE s.x = 1", "m")
    assert bad.blocked  # AI answered about the wrong table -> blocked, not trusted


def test_edit_fixes_blocked_row():
    spec = sf.load_workbook(_DOCS / "postgres Table filters.xlsx")["transactions"]
    unchanged = sf.from_edit(spec, spec.mode, sf.conditions_text(spec.conditions),
                             [{"fk": h.fk, "parent": h.parent, "pk": h.pk, "alias": h.alias,
                               "conditions": sf.conditions_text(h.conditions)} for h in spec.hops])
    assert unchanged.blocked
    fixed = sf.from_edit(spec, "scoped", "created_at >= '2025-08-01'",
                         [{"fk": "facility_id", "parent": "facilities", "pk": "id", "alias": "f",
                           "conditions": "company_id IN (137,9088,501)"},
                          {"fk": "company_id", "parent": "companies", "pk": "id", "alias": "c", "conditions": ""}])
    assert not fixed.blocked
    assert sf.render_joins(fixed, lambda p: p, base_ref="j")[0] == (
        "\nJOIN facilities f ON j.facility_id = f.id\nJOIN companies c ON f.company_id = c.id")
    dup = sf.from_edit(spec, "scoped", "", [{"fk": "a", "parent": "x", "pk": "id", "alias": "f"},
                                            {"fk": "b", "parent": "y", "pk": "id", "alias": "f"}])
    assert dup.blocked
    assert sf.silver_parent_column("facilities", "id") == "FACILITY_ID"
    assert sf.silver_parent_column("addresses", "id") == "ADDRESS_ID"


def test_silver_config_is_named_int_table(tmp_path):
    """Silver YAML file, its tables: key and its count key are all INT_<table>,
    so the runner (file stem -> tables key) finds it and it can't pass for Bronze."""
    import yaml
    from generated_queries.yaml_config_writer import YAMLConfigWriter, config_table_name

    plan = _discount_lines_plan()
    plan.source_db_type = "snowflake"
    assert config_table_name(plan, "silver") == "INT_DISCOUNT_LINES"
    assert config_table_name(plan, "bronze") == "INT_DISCOUNT_LINES"  # ADR 0048: target table name
    plan.target_table = "DISCOUNT_LINES"  # no INT_ on the node name -> still prefixed
    assert config_table_name(plan, "silver") == "INT_DISCOUNT_LINES"
    assert config_table_name(plan, "bronze") == "DISCOUNT_LINES"

    qs = emit_query_set(plan)
    writer = YAMLConfigWriter()
    data = writer.write_from_plan(plan, qs, output_dir=tmp_path, layer="silver")
    count = writer.write_count_yaml_from_plan(plan, qs, output_dir=tmp_path, layer="silver")
    assert data.name == "INT_DISCOUNT_LINES.yaml"
    block = yaml.safe_load(data.read_text(encoding="utf-8"))["tables"]["INT_DISCOUNT_LINES"]
    assert block["validations"]["data_validation"]["source_table_name"] == "DISCOUNT_LINES"
    assert list(yaml.safe_load(count.read_text(encoding="utf-8"))["tables"]) == ["INT_DISCOUNT_LINES"]


def test_generated_yaml_is_minimal_and_still_valid(tmp_path):
    """ADR 0048: data_validation keeps exactly the fields the engine reads,
    row_hash_validation stays, and plan metadata / placeholder blocks are gone
    unless the engine needs them (hybrid_v1)."""
    import yaml
    from generated_queries.yaml_config_writer import YAMLConfigWriter
    from validation.config_schema import validate_config_file

    plan = _discount_lines_plan()
    plan.source_db_type = "snowflake"
    plan.source_primary_keys = plan.target_primary_keys = ["DISCOUNT_LINE_ID", "FACILITY_ID"]
    plan.review_reasons = ["something to review"]
    path = YAMLConfigWriter().write_from_plan(plan, emit_query_set(plan), output_dir=tmp_path, layer="silver")
    validations = yaml.safe_load(path.read_text(encoding="utf-8"))["tables"]["INT_DISCOUNT_LINES"]["validations"]

    assert list(validations) == ["data_validation", "row_hash_validation"]
    assert list(validations["data_validation"]) == [
        "source_table_name", "source", "source_database", "source_schema", "sourcecolumn",
        "source_audit_column", "sourcequery", "target_table_name", "target", "target_database",
        "target_schema", "targetcolumn", "target_audit_column", "targetquery",
    ]
    assert validations["data_validation"]["sourcecolumn"] == ["DISCOUNT_LINE_ID", "FACILITY_ID"]  # composite PK
    assert "review" not in path.read_text(encoding="utf-8").split("tables:")[1]
    document, errors = validate_config_file(path, check_credentials=False)
    assert document is not None and errors == [], errors

    plan.execution_strategy = "hybrid_v1"
    path = YAMLConfigWriter().write_from_plan(plan, emit_query_set(plan), output_dir=tmp_path, layer="silver")
    vp = yaml.safe_load(path.read_text(encoding="utf-8"))["tables"]["INT_DISCOUNT_LINES"]["validations"]["validation_plan"]
    assert vp == {"execution_strategy": "hybrid_v1", "identity": {
        "type": plan.identity_type, "source_primary_keys": ["DISCOUNT_LINE_ID", "FACILITY_ID"],
        "target_primary_keys": ["DISCOUNT_LINE_ID", "FACILITY_ID"]}}
