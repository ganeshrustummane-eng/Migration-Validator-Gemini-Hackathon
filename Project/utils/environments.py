"""Run environments and the `{env}` database-name placeholder.

See docs/decisions/0038-environment-database-placeholders-in-generated-yaml.md.
Generation writes `{env}` in place of a database name's environment prefix
(DEV_EDGE_SILVER -> {env}_EDGE_SILVER); a run fills it back in for the chosen
--environment. Dependency-free on purpose: imported by Project/ (main.py,
db/factory.py), src/ (yaml_config_writer.py) and webapp/app.py.
"""

ENV_PLACEHOLDER = "{env}"

ENVIRONMENTS = {
    # name:  (env file,    {env})
    "local": (".env",      "DEV"),
    "dev":   (".env.dev",  "DEV"),
    "stg":   (".env.stg",  "STG"),
    "qat":   (".env.qat",  "QAT"),
    "prod":  (".env.prod", "PRD"),
}

_ENV_PREFIXES = tuple(sorted({f"{token}_" for _, token in ENVIRONMENTS.values()}))


def tokenize_database(database: str) -> str:
    """DEV_EDGE_SILVER -> {env}_EDGE_SILVER; names without a known prefix
    (BRONZE_EDGE) or already tokenized come back unchanged."""
    upper = (database or "").upper()
    for prefix in _ENV_PREFIXES:
        if upper.startswith(prefix):
            return ENV_PLACEHOLDER + database[len(prefix) - 1:]
    return database


def resolve_env_placeholders(node, environment: str):
    """Copy of a loaded YAML document with every `{env}` in every string
    replaced by this environment's token. str.replace, not str.format, so
    literal braces in SQL (PARSE_JSON('{}')) are left alone."""
    token = ENVIRONMENTS[environment][1]
    if isinstance(node, str):
        return node.replace(ENV_PLACEHOLDER, token)
    if isinstance(node, dict):
        return {k: resolve_env_placeholders(v, environment) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_env_placeholders(v, environment) for v in node]
    return node


if __name__ == "__main__":
    assert tokenize_database("DEV_EDGE_SILVER") == "{env}_EDGE_SILVER"
    assert tokenize_database("prd_bronze_edge") == "{env}_bronze_edge"
    assert tokenize_database("BRONZE_EDGE") == "BRONZE_EDGE"
    assert tokenize_database("DEVICES") == "DEVICES"
    assert tokenize_database("{env}_EDGE_SILVER") == "{env}_EDGE_SILVER"
    assert tokenize_database("") == ""
    doc = {"tables": {"t": {"validations": {"v": {
        "target_database": "{env}_EDGE_SILVER",
        "targetquery": "SELECT PARSE_JSON('{}') FROM \"{env}_EDGE_SILVER\".S.T",
        "cols": ["{env}_X", 3],
    }}}}}
    out = resolve_env_placeholders(doc, "prod")["tables"]["t"]["validations"]["v"]
    assert out["target_database"] == "PRD_EDGE_SILVER"
    assert out["targetquery"] == "SELECT PARSE_JSON('{}') FROM \"PRD_EDGE_SILVER\".S.T"
    assert out["cols"] == ["PRD_X", 3]
    assert doc["tables"]["t"]["validations"]["v"]["target_database"] == "{env}_EDGE_SILVER"
    print("ok")
