import yaml
import os
from db.postgres import Postgres
from db.mssqlserver import Mssqlserver
from db.athena import Athena
from db.snowflake import Snowflake

#Reading creds yaml
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_database(db_type,BASE_DIR,env):
    creds_path = os.path.join(BASE_DIR,"creds",f"{env}.yaml")
    with open(creds_path) as f:
        file = yaml.safe_load(f)    

    if db_type == "postgresql":
        postgres = file['postgresql']
        dbname = postgres['dbname']
        host = postgres['host']
        user = postgres['user']
        password = postgres['password']
        return Postgres(dbname=dbname,host=host,user=user,password=password,port=5432)
    
    elif db_type == 'mssql':
        mssqlserver = file['mssql']
        driver = mssqlserver['driver']
        server = mssqlserver['server']
        database = mssqlserver['database']
        uid = mssqlserver['uid']
        pwd = mssqlserver['pwd']
        return Mssqlserver(DRIVER=driver,SERVER=server,DATABASE=database,UID=uid,PWD=pwd)
    
    elif db_type == "athena":
        athena = file['athena']
        profile = athena['profile']
        aws_region = athena['aws_region']
        athena_db = athena['athena_db']
        athena_output = athena['athena_output']
        return Athena(PROFILE=profile,AWS_REGION=aws_region,ATHENA_DB=athena_db,ATHENA_OUTPUT=athena_output)
    
    elif db_type == "snowflake":
        snowflake = file['snowflake']
        snowflake_account = snowflake['snowflake_account']
        snowflake_user = snowflake['snowflake_user']
        snowflake_role = snowflake['snowflake_role']
        externalbrowser = snowflake['externalbrowser']
        snowflake_database = snowflake['snowflake_database']
        snowflake_schema = snowflake['snowflake_schema']
        snowflake_warehouse = snowflake['snowflake_warehouse']
        return Snowflake(SNOWFLAKE_ACCOUNT=snowflake_account,SNOWFLAKE_USER=snowflake_user,SNOWFLAKE_ROLE=snowflake_role,externalbrowser=externalbrowser,SNOWFLAKE_DATABASE=snowflake_database,SNOWFLAKE_SCHEMA=snowflake_schema,SNOWFLAKE_WAREHOUSE=snowflake_warehouse)
    else:
        raise ValueError(f"Unsupported database: {db_type}")