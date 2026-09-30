import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from connector import coalesce_client  # noqa: E402
from silver.coalesce_plan_builder import (  # noqa: E402
    build_plan, build_plan_from_metadata, UnsupportedNodeShapeError,
)
from silver.silver_sql_emitter import emit_query_set, resolve_ref_macro  # noqa: E402

UPSTREAM_NODE_ID = "323522c8-a433-4859-bb89-4ddcb48298f7"
SILVER_NODE_ID = "int-facilities-node-id"

UPSTREAM_NODE = {
    "database": "BRONZE_EDGE",
    "name": "FACILITIES",
    "metadata": {
        "columns": [
            {"columnID": "col-facility-id", "name": "ID"},
            {"columnID": "col-updated-at", "name": "UPDATED_AT"},
            {"columnID": "col-fivetran-start", "name": "_FIVETRAN_START"},
        ]
    },
}

SILVER_NODE = {
    "database": "SILVER_EDGE",
    "schema": "CONFORMED_RRADHAKR",
    "name": "INT_FACILITIES",
    "metadata": {
        "isMultisource": False,
        "overrideSQL": False,
        "customSQL": False,
        "sourceMapping": [
            {
                "aliases": {"FACILITIES": UPSTREAM_NODE_ID},
                "dependencies": [
                    {"nodeID": UPSTREAM_NODE_ID, "nodeName": "FACILITIES", "locationName": "BRONZE_EDGE"}
                ],
            }
        ],
        "columns": [
            {
                "name": "FACILITY_ID", "dataType": "NUMBER", "isBusinessKey": False,
                "sources": [{"transform": "", "columnReferences": [
                    {"nodeID": UPSTREAM_NODE_ID, "columnID": "col-facility-id"}
                ]}],
            },
            {
                "name": "SYS_VERSION", "dataType": "NUMBER", "isBusinessKey": False,
                "sources": [{
                    "transform": 'ROW_NUMBER() OVER (PARTITION BY "FACILITIES"."ID" ORDER BY "FACILITIES"."UPDATED_AT")',
                    "columnReferences": [{"nodeID": UPSTREAM_NODE_ID, "columnID": "col-updated-at"}],
                }],
            },
            {
                "name": "FACILITY_KEY", "dataType": "VARCHAR", "isBusinessKey": True,
                "sources": [{
                    "transform": "{{ ids_to_surrogate_key('EDGE', ['\"FACILITIES\".\"ID\"']) }}",
                    "columnReferences": [{"nodeID": UPSTREAM_NODE_ID, "columnID": "col-facility-id"}],
                }],
            },
            {
                "name": "SYS_CREATE_DATE", "dataType": "TIMESTAMP_NTZ", "isBusinessKey": False,
                "sources": [{"transform": "CAST(CURRENT_TIMESTAMP() AS TIMESTAMP)", "columnReferences": []}],
            },
        ],
    },
}

UPSTREAM_NODE_ID_2 = "companies-node-id"
MULTI_NODE_ID = "int-multi-node-id"

UPSTREAM_NODE_2 = {
    "database": "BRONZE_EDGE",
    "name": "COMPANIES",
    "metadata": {
        "columns": [
            {"columnID": "col-company-name", "name": "NAME"},
        ]
    },
}

# Multisource node -- two sourceMapping entries, each with its own alias and
# joinCondition, per ADR 0019 (the isMultisource / multi-dependency shape).
MULTI_NODE = {
    "database": "SILVER_EDGE",
    "schema": "CONFORMED_RRADHAKR",
    "name": "INT_MULTI",
    "metadata": {
        "isMultisource": True,
        "overrideSQL": False,
        "customSQL": False,
        "sourceMapping": [
            {
                "aliases": {"FACILITIES": UPSTREAM_NODE_ID},
                "dependencies": [
                    {"nodeID": UPSTREAM_NODE_ID, "nodeName": "FACILITIES", "locationName": "BRONZE_EDGE"}
                ],
                "join": {"joinCondition": "FROM {{ ref('BRONZE_EDGE', 'FACILITIES') }} \"FACILITIES\""},
            },
            {
                "aliases": {"COMPANIES": UPSTREAM_NODE_ID_2},
                "dependencies": [
                    {"nodeID": UPSTREAM_NODE_ID_2, "nodeName": "COMPANIES", "locationName": "BRONZE_EDGE"}
                ],
                "join": {
                    "joinCondition": (
                        "JOIN {{ ref('BRONZE_EDGE', 'COMPANIES') }} \"COMPANIES\" "
                        "ON \"FACILITIES\".\"COMPANY_ID\" = \"COMPANIES\".\"ID\""
                    )
                },
            },
        ],
        "columns": [
            {
                "name": "FACILITY_ID", "dataType": "NUMBER", "isBusinessKey": False,
                "sources": [{"transform": "", "columnReferences": [
                    {"nodeID": UPSTREAM_NODE_ID, "columnID": "col-facility-id"}
                ]}],
            },
            {
                "name": "COMPANY_NAME", "dataType": "VARCHAR", "isBusinessKey": False,
                "sources": [{"transform": "", "columnReferences": [
                    {"nodeID": UPSTREAM_NODE_ID_2, "columnID": "col-company-name"}
                ]}],
            },
        ],
    },
}

BAD_OVERRIDE_SQL_NODE = {
    "database": "SILVER_EDGE", "schema": "S", "name": "T",
    "metadata": {"isMultisource": False, "overrideSQL": True, "sourceMapping": []},
}

# ADR 0049: two Bronze sources landing in one Silver table. Each sourceMapping
# has its own FROM; each column has one source per mapping (same order).
UNION_NODE_ID = "int-union-node-id"
UNION_NODE = {
    "database": "SILVER_EDGE", "schema": "S", "name": "INT_SITES_ALL",
    "config": {"insertStrategy": "UNION ALL"},
    "metadata": {
        "isMultisource": True, "overrideSQL": False, "customSQL": False,
        "sourceMapping": [
            {"name": "FROM_PG", "aliases": {"FACILITIES": UPSTREAM_NODE_ID},
             "dependencies": [{"nodeID": UPSTREAM_NODE_ID, "nodeName": "FACILITIES", "locationName": "BRONZE_EDGE"}],
             "join": {"joinCondition": "FROM {{ ref('BRONZE_EDGE', 'FACILITIES') }} \"FACILITIES\""}},
            {"name": "FROM_SL", "aliases": {"COMPANIES": UPSTREAM_NODE_ID_2},
             "dependencies": [{"nodeID": UPSTREAM_NODE_ID_2, "nodeName": "COMPANIES", "locationName": "BRONZE_SL"}],
             "join": {"joinCondition": "FROM {{ ref('BRONZE_SL', 'COMPANIES') }} \"COMPANIES\""}},
        ],
        "columns": [
            {"name": "SITE_NAME", "dataType": "VARCHAR", "sources": [
                {"transform": "", "columnReferences": [{"nodeID": UPSTREAM_NODE_ID, "columnID": "col-facility-id"}]},
                {"transform": "", "columnReferences": [{"nodeID": UPSTREAM_NODE_ID_2, "columnID": "col-company-name"}]},
            ]},
            {"name": "SOURCE_SYSTEM", "dataType": "VARCHAR", "sources": [
                {"transform": "'PG'", "columnReferences": []},
                {"transform": "'SL'", "columnReferences": []},
            ]},
            # Only the first source declares it -> can't be compared, not guessed.
            {"name": "PG_ONLY", "dataType": "VARCHAR", "sources": [
                {"transform": "", "columnReferences": [{"nodeID": UPSTREAM_NODE_ID, "columnID": "col-facility-id"}]},
            ]},
        ],
    },
}

NODES_BY_ID = {
    UPSTREAM_NODE_ID: UPSTREAM_NODE,
    SILVER_NODE_ID: SILVER_NODE,
    UPSTREAM_NODE_ID_2: UPSTREAM_NODE_2,
    MULTI_NODE_ID: MULTI_NODE,
    UNION_NODE_ID: UNION_NODE,
}


class _FakeExtractor:
    def extract_columns(self, schema, table, database=None):
        return []


def _patch_env(monkeypatch=None):
    coalesce_client.COALESCE_API_TOKEN = "fake-token"
    coalesce_client.COALESCE_WORKSPACE_ID = "fake-ws"


def _run_build_plan(node_id=SILVER_NODE_ID):
    _patch_env()
    orig_get_node = coalesce_client.get_node
    orig_create = __import__("sql_extractor.extractors", fromlist=["ExtractorFactory"]).ExtractorFactory.create

    def fake_get_node(workspace_id, node_id):
        return NODES_BY_ID[node_id]

    def fake_create(db_type, **kwargs):
        return _FakeExtractor()

    import sql_extractor.extractors as extractors_mod

    coalesce_client.get_node = fake_get_node
    extractors_mod.ExtractorFactory.create = staticmethod(fake_create)
    try:
        return build_plan(node_id, workspace_id="fake-ws")
    finally:
        coalesce_client.get_node = orig_get_node
        extractors_mod.ExtractorFactory.create = staticmethod(orig_create)


def test_four_bucket_classification():
    plan, diff = _run_build_plan()
    by_name = {m.target_column: m for m in plan.mappings}

    passthrough = by_name["FACILITY_ID"]
    assert not passthrough.skip_validation
    assert passthrough.source_column == '"FACILITIES"."ID"'

    recomputable = by_name["SYS_VERSION"]
    assert not recomputable.skip_validation
    assert "ROW_NUMBER()" in recomputable.source_column

    macro = by_name["FACILITY_KEY"]
    assert macro.skip_validation
    assert "macro-expanded" in macro.skip_reason

    nondeterministic = by_name["SYS_CREATE_DATE"]
    assert nondeterministic.skip_validation
    assert "null_check" in nondeterministic.validation_rules


def test_macro_business_key_defers_to_review_with_natural_key_candidates():
    plan, diff = _run_build_plan()
    assert plan.requires_review
    assert any("macro-computed" in r for r in plan.review_reasons)
    assert plan.source_primary_keys == []
    assert plan.target_primary_keys == []
    assert "FACILITY_ID" in plan.population_scope["natural_key_candidates"]


def test_unsupported_node_shape_raises():
    """Empty sourceMapping ([]) is still a hard-stop -- there's nothing to
    build a Bronze reference from. isMultisource=True alone (ADR 0019) no
    longer causes this by itself; see test_multisource_node_builds_plan."""
    _patch_env()
    bad_node = {
        "database": "SILVER_EDGE", "schema": "S", "name": "T",
        "metadata": {"isMultisource": True, "sourceMapping": []},
    }
    orig_get_node = coalesce_client.get_node
    coalesce_client.get_node = lambda workspace_id, node_id: bad_node
    try:
        raised = False
        try:
            build_plan("bad-node", workspace_id="fake-ws")
        except UnsupportedNodeShapeError:
            raised = True
        assert raised
    finally:
        coalesce_client.get_node = orig_get_node


def test_overridesql_still_hardstops():
    _patch_env()
    orig_get_node = coalesce_client.get_node
    coalesce_client.get_node = lambda workspace_id, node_id: BAD_OVERRIDE_SQL_NODE
    try:
        raised = False
        try:
            build_plan("bad-override-node", workspace_id="fake-ws")
        except UnsupportedNodeShapeError:
            raised = True
        assert raised
    finally:
        coalesce_client.get_node = orig_get_node


def test_multisource_node_builds_plan_and_join_sql():
    """ADR 0019: isMultisource / multiple sourceMapping entries no longer
    hard-stop. Alias resolution must work across every sourceMapping entry
    (not just entry 0), and the Bronze join SQL must be the concatenation of
    each entry's joinCondition, verbatim, with {{ ref(...) }} resolved."""
    plan, diff = _run_build_plan(MULTI_NODE_ID)
    by_name = {m.target_column: m for m in plan.mappings}

    assert by_name["FACILITY_ID"].source_column == '"FACILITIES"."ID"'
    assert by_name["COMPANY_NAME"].source_column == '"COMPANIES"."NAME"'

    join_sql = plan.population_scope["bronze_join_sql"]
    assert 'FROM "BRONZE_EDGE"."CONFORMED_RRADHAKR"."FACILITIES" "FACILITIES"' in join_sql
    assert (
        'JOIN "BRONZE_EDGE"."CONFORMED_RRADHAKR"."COMPANIES" "COMPANIES" '
        'ON "FACILITIES"."COMPANY_ID" = "COMPANIES"."ID"'
    ) in join_sql


def test_multisource_emit_query_set_wraps_and_resolves_join():
    """ADR 0027 + ADR 0019 together: the multisource Bronze recompute SELECT
    must have every selected column wrapped in the NULL-placeholder AND the
    FROM/JOIN clause must still be the macro-resolved join SQL verbatim --
    the wrapper and the ref() resolution are independent and must not
    interfere with each other."""
    plan, diff = _run_build_plan(MULTI_NODE_ID)
    query_set = emit_query_set(plan)

    assert (
        'COALESCE(CAST("FACILITIES"."ID" AS STRING), \'<<NULL>>\') AS "FACILITY_ID"'
        in query_set.main_validation_source
    )
    assert (
        'COALESCE(CAST("COMPANIES"."NAME" AS STRING), \'<<NULL>>\') AS "COMPANY_NAME"'
        in query_set.main_validation_source
    )
    assert 'FROM "BRONZE_EDGE"."CONFORMED_RRADHAKR"."FACILITIES" "FACILITIES"' in query_set.main_validation_source
    assert (
        'JOIN "BRONZE_EDGE"."CONFORMED_RRADHAKR"."COMPANIES" "COMPANIES" '
        'ON "FACILITIES"."COMPANY_ID" = "COMPANIES"."ID"'
    ) in query_set.main_validation_source
    # Row-count queries stay unwrapped -- no column to wrap, just COUNT(*).
    # ADR 0045: every Bronze table read is filtered to active rows; the joined
    # one tolerates an unmatched (NULL) join row.
    assert "COALESCE(CAST" not in query_set.row_count_source
    assert 'WHERE "FACILITIES"."_FIVETRAN_ACTIVE" = TRUE' in query_set.row_count_source
    assert 'COALESCE("COMPANIES"."_FIVETRAN_ACTIVE", TRUE) = TRUE' in query_set.row_count_source
    assert "COALESCE" not in query_set.row_count_target


def test_build_plan_from_pasted_metadata_no_api_fetch():
    """ADR 0021: pasted node JSON builds the same plan as build_plan(), with
    no coalesce_client.get_node() call for the Silver node itself (only used,
    if at all, to resolve a cross-node columnReference)."""
    _patch_env()
    orig_get_node = coalesce_client.get_node
    coalesce_client.get_node = lambda workspace_id, node_id: NODES_BY_ID[node_id]
    try:
        plan, diff = build_plan_from_metadata(SILVER_NODE, workspace_id="fake-ws")
    finally:
        coalesce_client.get_node = orig_get_node
    by_name = {m.target_column: m for m in plan.mappings}
    assert by_name["FACILITY_ID"].source_column == '"FACILITIES"."ID"'


def test_schema_diff_unavailable_does_not_abort_plan():
    """ADR 0021: a live Snowflake schema-diff failure (e.g. no grant on the
    Bronze database) degrades to SchemaDiff.unavailable_reason instead of
    raising -- plan generation still succeeds."""
    _patch_env()
    orig_create = __import__("sql_extractor.extractors", fromlist=["ExtractorFactory"]).ExtractorFactory.create

    class _BoomExtractor:
        def extract_columns(self, schema, table, database=None):
            raise Exception("002003 (02000): Database 'BRONZE_EDGE' does not exist or not authorized.")

    import sql_extractor.extractors as extractors_mod
    orig_get_node = coalesce_client.get_node
    extractors_mod.ExtractorFactory.create = staticmethod(lambda db_type, **kwargs: _BoomExtractor())
    coalesce_client.get_node = lambda workspace_id, node_id: NODES_BY_ID[node_id]
    try:
        plan, diff = build_plan_from_metadata(SILVER_NODE, workspace_id="fake-ws")
    finally:
        extractors_mod.ExtractorFactory.create = staticmethod(orig_create)
        coalesce_client.get_node = orig_get_node

    assert diff.unavailable_reason is not None
    assert "BRONZE_EDGE" in diff.unavailable_reason
    assert diff.only_in_metadata == []
    assert diff.only_in_bronze_live == []
    assert diff.only_in_silver_live == []
    # Plan itself still built successfully despite the diff failure.
    assert plan.mappings


def test_resolve_ref_macro():
    resolved = resolve_ref_macro(
        "FROM {{ ref('BRONZE_EDGE', 'FACILITIES') }} \"FACILITIES\"", "CONFORMED_RRADHAKR"
    )
    assert resolved == 'FROM "BRONZE_EDGE"."CONFORMED_RRADHAKR"."FACILITIES" "FACILITIES"'


def test_sql_emitter_literal_text():
    """ADR 0018 + ADR 0022 + ADR 0027: literal generated-SQL assertion, not
    just plan-object shape -- passthrough/recomputable columns selected
    verbatim (but wrapped in the blanket NULL-placeholder wrapper per ADR
    0027); macro-computed and non-deterministic columns both dropped
    entirely (no inline comment -- their exclusion is recorded in
    review_reasons / skip_reason instead, not in the SQL text)."""
    plan, diff = _run_build_plan()
    query_set = emit_query_set(plan)

    expected_source = (
        'SELECT\n'
        '    COALESCE(CAST("FACILITIES"."ID" AS STRING), \'<<NULL>>\') AS "FACILITY_ID",\n'
        '    COALESCE(CAST(ROW_NUMBER() OVER (PARTITION BY "FACILITIES"."ID" ORDER BY "FACILITIES"."UPDATED_AT") AS STRING), \'<<NULL>>\') AS "SYS_VERSION"\n'
        'FROM "BRONZE_EDGE"."CONFORMED_RRADHAKR"."FACILITIES"\n'
        # ADR 0045: active-row filter after the SYS_VERSION window, not before it.
        'QUALIFY "FACILITIES"."_FIVETRAN_ACTIVE" = TRUE'
    )
    expected_target = (
        'SELECT\n'
        '    COALESCE(CAST("FACILITY_ID" AS STRING), \'<<NULL>>\') AS "FACILITY_ID",\n'
        '    COALESCE(CAST("SYS_VERSION" AS STRING), \'<<NULL>>\') AS "SYS_VERSION"\n'
        'FROM "SILVER_EDGE"."CONFORMED_RRADHAKR"."INT_FACILITIES"'
    )

    assert query_set.main_validation_source == expected_source
    assert query_set.main_validation_target == expected_target
    # Blanket COALESCE/CAST/NULL-placeholder wrapper now present everywhere (ADR 0027).
    assert query_set.main_validation_source.count("COALESCE(CAST(") == 2
    assert "<<NULL>>" in query_set.main_validation_source
    assert "SYS_CREATE_DATE" not in query_set.main_validation_source
    # Row-count queries are COUNT(*) with no column to wrap -- unwrapped (ADR 0027 scope).
    assert query_set.row_count_source == (
        'SELECT COUNT(*) AS count\nFROM "BRONZE_EDGE"."CONFORMED_RRADHAKR"."FACILITIES"\n'
        'WHERE "FACILITIES"."_FIVETRAN_ACTIVE" = TRUE'
    )
    # Fixture has no IS_CURRENT column -> Silver side unfiltered, and flagged.
    assert any("no IS_CURRENT" in r for r in plan.review_reasons)
    assert query_set.row_count_target == (
        'SELECT COUNT(*) AS count FROM "SILVER_EDGE"."CONFORMED_RRADHAKR"."INT_FACILITIES"'
    )


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS  %s" % name)
            except AssertionError as exc:
                failures += 1
                print("FAIL  %s: %s" % (name, exc))
    print("\n%d failure(s)" % failures)
    sys.exit(1 if failures else 0)


def test_union_node_two_bronze_sources_into_one_silver_table():
    """ADR 0049: each source mapping becomes its own SELECT with its own
    expressions and _FIVETRAN_ACTIVE filter, combined with the node's UNION ALL;
    the count query counts the combined rows."""
    plan, _ = _run_build_plan(UNION_NODE_ID)
    by_name = {m.target_column: m for m in plan.mappings}
    assert by_name["PG_ONLY"].skip_validation and "union branch 2" in by_name["PG_ONLY"].skip_reason
    assert plan.population_scope["union_strategy"] == "UNION ALL"
    assert "bronze_join_sql" not in plan.population_scope
    assert any("tells the sources apart" in r for r in plan.review_reasons)

    qs = emit_query_set(plan)
    branch_1, branch_2 = qs.main_validation_source.split("\nUNION ALL\n")
    assert 'COALESCE(CAST("FACILITIES"."ID" AS STRING), \'<<NULL>>\') AS "SITE_NAME"' in branch_1
    assert "COALESCE(CAST('PG' AS STRING), '<<NULL>>') AS \"SOURCE_SYSTEM\"" in branch_1
    assert branch_1.endswith('FROM "BRONZE_EDGE"."S"."FACILITIES" "FACILITIES"\nWHERE "FACILITIES"."_FIVETRAN_ACTIVE" = TRUE')
    assert 'COALESCE(CAST("COMPANIES"."NAME" AS STRING), \'<<NULL>>\') AS "SITE_NAME"' in branch_2
    assert branch_2.endswith('FROM "BRONZE_SL"."S"."COMPANIES" "COMPANIES"\nWHERE "COMPANIES"."_FIVETRAN_ACTIVE" = TRUE')
    assert "PG_ONLY" not in qs.main_validation_source
    assert qs.row_count_source == f"SELECT COUNT(*) AS count FROM (\n{qs.main_validation_source}\n)"
    assert qs.main_validation_target.endswith('FROM "SILVER_EDGE"."S"."INT_SITES_ALL"')


def test_union_strategy_unknown_is_flagged_not_guessed_silently():
    from silver.coalesce_plan_builder import _union_strategy
    assert _union_strategy({"config": {"insertStrategy": "UNION"}}) == ("UNION", None)
    assert _union_strategy({"config": {"insertStrategy": "INSERT"}}) == ("UNION ALL", None)
    op, note = _union_strategy({})
    assert op == "UNION ALL" and "confirm in Coalesce" in note
