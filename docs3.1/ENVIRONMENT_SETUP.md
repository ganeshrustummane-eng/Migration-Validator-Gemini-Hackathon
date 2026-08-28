# Environment Setup

## 1. Install Python Dependencies

From the repository root:

```powershell
python -m pip install -r requirements.txt
```

or 

```Powershell

python -m pip install --only-binary=:all: -r requirements.txt

```

Important packages include:

- `python-dotenv` for `.env` loading
- `pyyaml` for validation configuration
- `pydantic` for validating generated configs at load time
- `openai` for AI mapping and SQL generation (**required**)
- `pandas` for comparison and CSV reports
- `psycopg2-binary` for PostgreSQL
- `pyodbc` for Microsoft SQL Server
- `snowflake-connector-python` for Snowflake
- `boto3` for Athena
- `pytest` for regression tests

For MSSQL, install an appropriate Microsoft ODBC Driver separately.

> On Python 3.13/3.14, prefer `--only-binary=:all:`. Older pinned driver versions
> have no wheels for those interpreters and pip will otherwise attempt — and fail
> — a source build.

## 1a. Configure AI Access (required)

Column mapping and validation SQL are AI-generated. There is no rule-based
fallback, so this is not optional:

```env
DIAL_API_KEY=<your key>
DIAL_API_BASE=https://ai-proxy.lab.epam.com
DIAL_API_VERSION=2025-04-01-preview
DIAL_MODEL=gpt-4o
```

The DIAL endpoint requires VPN access. Verify with:

```powershell
python src\validate_cli.py list-models
```

Work that does not need a key: `pytest`, `validate_cli.py lint`, and
`run_all_tests.py --skip-live`.

## 2. Configure `.env`

Copy the example file if needed:

```powershell
copy .env.example .env
```

Never commit `.env`.

### PostgreSQL source

```env
SRC_1_TYPE=postgresql
SRC_1_HOST=<host>
SRC_1_PORT=5432
SRC_1_DATABASE=<database>
SRC_1_SCHEMA=<schema>
SRC_1_USERNAME=<username>
SRC_1_PASSWORD=<password>
```

### MSSQL source

```env
SRC_2_TYPE=mssql
SRC_2_HOST=<server>
SRC_2_PORT=1433
SRC_2_DATABASE=<database>
SRC_2_SCHEMA=dbo
SRC_2_USERNAME=<username>
SRC_2_PASSWORD=<password>
SRC_2_AUTH=sql
```

For Windows authentication, use `SRC_2_AUTH=windows` and configure the account accordingly.

### Athena source

```env
SRC_3_TYPE=athena
SRC_3_REGION=<aws-region>
SRC_3_DATABASE=<catalog-database>
SRC_3_QUERY_RESULT_LOCATION=s3://<bucket>/<prefix>/
SRC_3_USERNAME=<access-key-optional>
SRC_3_PASSWORD=<secret-key-optional>
```

### Snowflake target

```env
SNOWFLAKE_ACCOUNT=<account>
SNOWFLAKE_DATABASE=<database>
SNOWFLAKE_SCHEMA=<schema>
SNOWFLAKE_USERNAME=<username>
SNOWFLAKE_PASSWORD=<password>
SNOWFLAKE_WAREHOUSE=<warehouse>
SNOWFLAKE_ROLE=<role-optional>
```

### AI configuration

**Required.** See section 1a. Column mapping and validation SQL are AI-generated
and there is no static fallback — an unset `DIAL_API_KEY` makes generation fail
rather than silently produce unreviewed output.

```env
DIAL_API_KEY=<key>
DIAL_API_BASE=https://ai-proxy.lab.epam.com
DIAL_API_VERSION=2025-04-01-preview
DIAL_MODEL=gpt-4o
```

## 3. Non-Secret Metadata

`config/database_registry.yaml` provides database and schema names when those
values are missing from `.env`. Keys match the `.env` prefix with the trailing
underscore removed (`SRC_1_HOST` → key `SRC_1`).

It must contain metadata only. Do not put passwords, usernames, API keys, or
tokens in this file — it is version-controlled.

## 4. Verify Environment

Offline first (no database, no API key):

```powershell
python -m pytest -q
python src\validate_cli.py lint
python tests\e2e\run_all_tests.py --skip-live
```

Then live:

```powershell
python test_env_connections.py
python src\validate_cli.py list-models
```

Expected connection result:

```text
PostgreSQL  PASS
MSSQL       PASS
Snowflake   PASS
Athena      PASS
```
