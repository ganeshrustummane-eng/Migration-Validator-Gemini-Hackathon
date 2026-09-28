# 0038. Environment database placeholders in generated YAML (`{env}`), resolved at run time

**Status:** Accepted — implemented
**Date:** 2026-09-28

## Context

Generated YAML hardcodes the environment into database names. Example from
`Project/config/silver/count_validation/snowflake.yaml`:

```yaml
target_database: DEV_EDGE_SILVER
targetquery: SELECT COUNT(*) AS count FROM "DEV_EDGE_SILVER"."CONFORMED_RRADHAKR"."INT_DISCOUNT_LINES"
```

`DEV_EDGE_SILVER` comes straight from the Coalesce node's `database` field
(`src/silver/coalesce_plan_builder.py:299`). The `DEV_` part is the
environment. Later we will also run against staging, QA and prod. A YAML
generated in dev therefore only validates dev. A `--environment prod` run
today swaps credentials (`Project/db/factory.py` picks `.env.prod`), but every
query still reads `DEV_EDGE_SILVER`. Because the data comes from the wrong
database, a prod run can **pass** while comparing dev data. Under the
CLAUDE.md data-quality bar that is a Consistency failure, and the worst kind,
because it looks like a success.

Management has asked us to follow the colleague's `main.py` contract:

- `--environment` choices: `dev, stg, qat, prod, local`. Ours today is
  `dev, uat, prod, local`, and that list is hardcoded in 4 places:
  `Project/main.py:70`, `Project/db/factory.py:18`, `webapp/app.py:3500`, and
  `webapp/app.py:4424`.
- Queries carry an `{env}` placeholder that is filled in at run time:
  dev→`DEV`, stg→`STG`, qat→`QAT`, prod→`PRD`, local→`DEV`.
- The YAML keywords (`source`, `sourcequery`, `sourcecolumn`,
  `source_table_name`, and the `target*` equivalents) are the shared contract
  with the colleague's repo.

The colleague's version has two problems we should not copy:
1. It uses `query.format(...)`. Any literal brace in the SQL (for example
   `PARSE_JSON('{}')` or an `OBJECT_CONSTRUCT` literal) would then raise, or
   be silently rewritten. None of today's configs contain braces (checked
   with a grep of `Project/config/`), but generated SQL is not guaranteed to
   stay that way.
2. Only `targetquery` is formatted. In Silver, the **source** is also
   Snowflake (Bronze). Our YAML also carries `source_database` /
   `target_database`, which `get_database()` uses as connection overrides
   (`Project/main.py:235-238`), so those need the same substitution.

## Decision

**Only the environment prefix of database names changes. Generation writes
`{env}` in place of the prefix, and the run fills it in with `str.replace`,
not `str.format`. Schemas, YAML keywords, and everything else stay as they
are.**

1. **One environment table** in the new dependency-free
   `Project/utils/environments.py`. It replaces `factory.py`'s old
   `_ENV_FILE_BY_ENVIRONMENT`. It lives in its own module, not in
   `factory.py`, because `src/`'s YAML writer and the webapp also import it,
   and importing `factory.py` would pull in every DB driver. `src/` and the
   webapp import it as `Project.utils.environments`, because `src/utils/`
   also exists and a bare `utils` import would be ambiguous:

   ```python
   ENVIRONMENTS = {
       # name:  (env file,    {env})
       "local": (".env",      "DEV"),
       "dev":   (".env.dev",  "DEV"),
       "stg":   (".env.stg",  "STG"),
       "qat":   (".env.qat",  "QAT"),
       "prod":  (".env.prod", "PRD"),
   }
   ```
   `main.py`'s argparse `choices` reads `list(ENVIRONMENTS)`. The two webapp
   selectboxes show the same list. `uat` is dropped.

2. **Run time.** `resolve_env_placeholders(document, environment)` in
   `environments.py` is applied once to each whole YAML document, right at
   `yaml.safe_load` in `main.py`. It uses `str.replace` to substitute `{env}`
   in every string. Resolving the whole document at load time means one call
   site covers every block (data, count, integrity_check,
   `row_hash_validation` for hybrid) and every field (queries,
   `*_database`, plan JSON such as `population_scope`), with nothing to keep
   in sync. YAML without `{env}` passes through unchanged.

3. **Generation time.** When the backend writer
   (`src/generated_queries/yaml_config_writer.py`, reached through
   `QueryOutputManager.generate_from_plan()`, which both Bronze and Silver
   already use) writes a block, it tokenizes a database name only if it
   starts with a known `{env}` value followed by `_`:
   `DEV_EDGE_SILVER` → `{env}_EDGE_SILVER`. The replacement applies both to
   the `*_database` field and to that name inside the queries. Names without
   a known prefix, such as today's `BRONZE_EDGE`, are left alone. If Bronze
   later becomes `DEV_BRONZE_EDGE`, the same rule tokenizes it with no code
   change. The plan JSON and the live Snowflake lookups done during
   generation (schema diff, column fetch) keep the real name. Only the YAML
   written to disk is tokenized. This happens in `_tokenize_env()` in
   `write()` (the data YAML text) and `write_count_yaml()` (the count
   block's `*_database` and `*query` values). In the data YAML text,
   `*_database: {env}_...` is written quoted (`"{env}_EDGE_SILVER"`),
   because an unquoted scalar starting with `{` is a YAML flow mapping and
   made the file unparseable. That was found while testing with the mock
   data in `C:\DB\env_mock`. The count YAML goes through `yaml.dump`, which
   quotes on its own.

4. **YAML keywords stay compatible.** Every keyword the colleague's
   `main.py` reads keeps the same name and meaning. Our additional keys
   (`source_database`, `target_database`, `source_schema`, `target_schema`,
   `validation_plan`, ...) remain as extras on top of that contract. Their
   engine ignores these keys, so the two repos can still read the same YAML
   shape.

## Alternatives considered

- **Copy the colleague's `.format()` as-is.** Rejected for problems 1–2 above.
- **A separate folder or YAML per environment.** Rejected because it
  multiplies every config by 5 and the copies drift apart. Placeholders keep
  one file.
- **Put the database name only in `.env` (`SNOWFLAKE_DATABASE`).** Rejected
  because Silver YAML holds two Snowflake databases (Bronze source and Silver
  target), and the name is also embedded in the SQL text, not just the
  connection.
- **Rewrite database names in `main.py` with a regex (`DEV_` → `PRD_`),
  without placeholders.** Rejected because it silently rewrites anything
  starting with `DEV_`, including a column or literal. An explicit
  placeholder can be audited in the YAML.
- **A `{schema_env}` placeholder, as in the colleague's code.** Not adopted.
  Schemas stay exactly as generated for now.

## Consequences

- One generated YAML runs against any environment. The environment is chosen
  only at run time (the Run Validation selectbox or `--environment`), which
  matches ADR 0034's rule that per-run settings travel through the run, not
  through YAML.
- `uat` goes away and `stg`/`qat` are added. `.env.stg` and `.env.qat` must be
  created with those environments' credentials. The code does not touch
  `.env.uat`. If it holds staging credentials, rename it to `.env.stg` by hand.
- There is no stale-YAML guard. The existing generated configs under
  `Project/config/` are being deleted and regenerated, so no pre-ADR YAML
  with a literal `DEV_` name is expected to remain.
- Schemas (for example `CONFORMED_RRADHAKR`) are not environment-aware. A run
  in another environment works only if that schema name exists there too.
  Revisit in a new ADR if it doesn't.
- Out of scope: the 4 webapp-side YAML writers (prompt tab, RPJ tab, custom
  editor, `excel_batch_loader.write_yaml()`) do not go through
  `yaml_config_writer.py`, so they won't tokenize. A user can type
  `{env}_...` by hand there, and run time resolves it either way.
- Out of scope for this ADR (separate ADRs later): the new YAML shape the
  manager sent, the `int_<table>` naming of Silver YAML files, moving
  `validation_plan` into its own file, and the report section.

## Resolved questions

1. `uat`: dropped. The choices are exactly `dev, stg, qat, prod, local`.
2. Token values: the colleague's values are used (`DEV`/`STG`/`QAT`/`PRD`,
   and `DEV` for local).
3. Schemas: no schema rule for now.
4. Bronze database: left as-is. The prefix rule picks up `{env}_BRONZE_EDGE` /
   `{env}_EDGE_SILVER`-style names automatically once they exist.
5. Stale-YAML guard: dropped, because the existing configs are regenerated
   from scratch.
