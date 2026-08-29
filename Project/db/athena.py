from db.base import Database
import pandas as pd
import boto3
import time


class Athena(Database):
    def __init__(self,AWS_REGION,ATHENA_DB,ATHENA_OUTPUT,ACCESS_KEY="",SECRET_KEY=""):
        self.AWS_REGION = AWS_REGION
        self.ATHENA_DB = ATHENA_DB
        self.ATHENA_OUTPUT = ATHENA_OUTPUT
        self.ACCESS_KEY = ACCESS_KEY
        self.SECRET_KEY = SECRET_KEY

    def connect(self):
        kwargs = dict(region_name=self.AWS_REGION)
        if self.ACCESS_KEY and self.SECRET_KEY:
            kwargs["aws_access_key_id"] = self.ACCESS_KEY
            kwargs["aws_secret_access_key"] = self.SECRET_KEY
        # Falls back to the default boto3 credential chain (AWS CLI profile,
        # instance role, etc.) when explicit keys aren't provided.
        session = boto3.Session(**kwargs)
        athena = session.client("athena")
        return athena
    
    def execute_query(self, query):
        athena = self.connect()
        response = athena.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": self.ATHENA_DB},
        ResultConfiguration={"OutputLocation": self.ATHENA_OUTPUT},)

        query_execution_id = response["QueryExecutionId"]

        # Wait for completion
        while True:
            status = athena.get_query_execution(QueryExecutionId=query_execution_id)
            state = status["QueryExecution"]["Status"]["State"]
            if state in ["SUCCEEDED", "FAILED", "CANCELLED"]:
                break
            time.sleep(2)

        if state != "SUCCEEDED":
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "No details")
            raise Exception(f"Query failed: {state} — {reason}")


        # Fetch result
        results = athena.get_query_results(QueryExecutionId=query_execution_id)
        column_info = results["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        col_names = [col["Name"] for col in column_info]

        rows = []
        for row in results["ResultSet"]["Rows"][1:]:  # skip header
            values = [c.get("VarCharValue", None) for c in row["Data"]]
            rows.append(values)

        return pd.DataFrame(rows, columns=col_names)