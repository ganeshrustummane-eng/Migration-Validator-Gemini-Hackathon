from db.base import Database
import snowflake.connector
import pandas as pd


class Snowflake(Database):
    def __init__(self, SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
                 SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE=""):
        self.SNOWFLAKE_ACCOUNT = SNOWFLAKE_ACCOUNT
        self.SNOWFLAKE_USER = SNOWFLAKE_USER
        self.SNOWFLAKE_PASSWORD = SNOWFLAKE_PASSWORD
        self.SNOWFLAKE_DATABASE = SNOWFLAKE_DATABASE
        self.SNOWFLAKE_SCHEMA = SNOWFLAKE_SCHEMA
        self.SNOWFLAKE_WAREHOUSE = SNOWFLAKE_WAREHOUSE

    def connect(self):
        kwargs = dict(
            account=self.SNOWFLAKE_ACCOUNT,
            user=self.SNOWFLAKE_USER,
            password=self.SNOWFLAKE_PASSWORD,
            database=self.SNOWFLAKE_DATABASE,
            schema=self.SNOWFLAKE_SCHEMA,
        )
        if self.SNOWFLAKE_WAREHOUSE:
            kwargs["warehouse"] = self.SNOWFLAKE_WAREHOUSE
        return snowflake.connector.connect(**kwargs)

    def execute_query(self, query):
        with self.connect() as conn:
            with conn.cursor() as cs:
                if self.SNOWFLAKE_WAREHOUSE:
                    cs.execute(f"USE WAREHOUSE {self.SNOWFLAKE_WAREHOUSE};")
                cs.execute(query)
                rows = cs.fetchall()
                df = pd.DataFrame(rows, columns=[c[0] for c in cs.description])
                return df
