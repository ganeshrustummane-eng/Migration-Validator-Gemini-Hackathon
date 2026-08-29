import os
from pathlib import Path

from dotenv import dotenv_values

from db.postgres import Postgres
from db.mssqlserver import Mssqlserver
from db.athena import Athena
from db.snowflake import Snowflake

PROJECT_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = PROJECT_DIR.parent

# One .env file per environment — same shape/keys as the root .env already
# used by webapp/src (SRC_N_*, SNOWFLAKE_*), just a different file per target.
# This replaces the old Project/creds/<env>.yaml scheme so there is a single
# credential format instead of two.
_ENV_FILE_BY_ENVIRONMENT = {
    "local": ".env",
    "dev": ".env.dev",
    "uat": ".env.uat",
    "prod": ".env.prod",
}

_TYPE_ALIASES = {
    "postgresql": {"postgresql", "postgres"},
    "mssql": {"mssql", "mssqlserver"},
    "athena": {"athena", "aws_athena"},
}


def _load_env(environment: str) -> dict:
    fname = _ENV_FILE_BY_ENVIRONMENT.get(environment, f".env.{environment}")
    path = ROOT_DIR / fname
    if not path.exists():
        raise FileNotFoundError(
            f"No env file for environment '{environment}': expected {path}. "
            f"Copy .env.example to {fname} and fill in real values."
        )
    return dotenv_values(path)


def _find_source(env: dict, db_type: str) -> dict:
    """First SRC_N_* connection in env whose TYPE matches db_type. Returns the
    per-connection fields with the 'SRC_N_' prefix stripped."""
    wanted = _TYPE_ALIASES.get(db_type, {db_type})
    for i in range(1, 11):
        prefix = f"SRC_{i}_"
        raw_type = (env.get(f"{prefix}TYPE") or "").lower().strip()
        if raw_type in wanted:
            return {k[len(prefix):]: v for k, v in env.items() if k.startswith(prefix) and v}
    raise ValueError(
        f"No SRC_N_TYPE={db_type} connection found for this environment's env file. "
        f"Add one (SRC_N_TYPE={db_type}, SRC_N_HOST, SRC_N_DATABASE, ...)."
    )


def get_database(db_type, BASE_DIR, environment):
    env = _load_env(environment)

    if db_type == "postgresql":
        src = _find_source(env, "postgresql")
        return Postgres(
            dbname=src["DATABASE"], host=src["HOST"],
            user=src["USERNAME"], password=src.get("PASSWORD", ""),
            port=int(src.get("PORT") or 5432),
        )

    elif db_type == "mssql":
        src = _find_source(env, "mssql")
        return Mssqlserver(
            DRIVER=src.get("DRIVER", "ODBC Driver 18 for SQL Server"),
            SERVER=src["HOST"], DATABASE=src["DATABASE"],
            UID=src.get("USERNAME", ""), PWD=src.get("PASSWORD", ""),
        )

    elif db_type == "athena":
        src = _find_source(env, "athena")
        region = src.get("REGION") or src.get("HOST", "us-east-1")
        s3_output = src.get("QUERY_RESULT_LOCATION") or env.get("ATHENA_S3_OUTPUT", "")
        return Athena(
            AWS_REGION=region, ATHENA_DB=src["DATABASE"], ATHENA_OUTPUT=s3_output,
            ACCESS_KEY=src.get("USERNAME", ""), SECRET_KEY=src.get("PASSWORD", ""),
        )

    elif db_type == "snowflake":
        return Snowflake(
            SNOWFLAKE_ACCOUNT=env.get("SNOWFLAKE_ACCOUNT", ""),
            SNOWFLAKE_USER=env.get("SNOWFLAKE_USERNAME", ""),
            SNOWFLAKE_PASSWORD=env.get("SNOWFLAKE_PASSWORD", ""),
            SNOWFLAKE_DATABASE=env.get("SNOWFLAKE_DATABASE", ""),
            SNOWFLAKE_SCHEMA=env.get("SNOWFLAKE_SCHEMA", ""),
            SNOWFLAKE_WAREHOUSE=env.get("SNOWFLAKE_WAREHOUSE", ""),
        )

    else:
        raise ValueError(f"Unsupported database: {db_type}")
