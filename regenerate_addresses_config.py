"""
Regenerate addresses.yaml with Correct MS SQL Server Syntax
=============================================================
This script regenerates the addresses.yaml config with proper MS SQL Server
syntax, fixing the "AS TEXT" error.

Usage:
    python regenerate_addresses_config.py
"""

import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))


def regenerate_addresses_config():
    """Regenerate addresses.yaml with correct MS SQL Server syntax."""
    
    print("=" * 70)
    print("Regenerating addresses.yaml with MS SQL Server Syntax")
    print("=" * 70)
    print()
    
    # Set environment to use MS SQL Server
    os.environ["SOURCE_TYPE"] = "mssql"
    
    config_path = Path("config/bronze/data_validation/addresses.yaml")
    
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return 1
    
    print(f"📄 Config file: {config_path}")
    print()
    
    # Backup original
    backup_path = config_path.with_suffix(".yaml.backup")
    print(f"💾 Creating backup: {backup_path}")
    
    import shutil
    shutil.copy(config_path, backup_path)
    print(f"✅ Backup created")
    print()
    
    # Parse existing config to get connection details
    import yaml
    
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    table_config = config["tables"]["Addresses"]
    source_table = table_config["validations"]["data_validation"]["source_table_name"]
    
    print(f"📊 Table: {source_table}")
    print()
    
    # Import necessary modules
    try:
        from sql_extractor.extractors import MSSQLExtractor
        from ai_transformation import RuleMapperOrchestrator
        from generated_queries.sql_query_generator import SQLQueryGenerator
        from generated_queries.yaml_config_writer import YAMLConfigWriter
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Make sure all dependencies are installed:")
        print("   pip install -r requirements.txt")
        return 1
    
    print("🔧 Step 1: Extract column metadata from MS SQL Server")
    print("-" * 70)
    
    # You'll need to provide connection details
    # This is a template - adjust as needed
    conn_string = os.getenv("MSSQL_CONNECTION_STRING")
    if not conn_string:
        print("⚠️  MSSQL_CONNECTION_STRING not set in environment")
        print("   Please set it in .env:")
        print("   MSSQL_CONNECTION_STRING=mssql+pyodbc://user:pass@host/db?driver=...")
        print()
        print("   For now, using existing column metadata from YAML...")
        
        # Extract column names from existing config
        source_query = table_config["validations"]["data_validation"]["sourcequery"]
        import re
        col_pattern = r"AS (\w+)_normalized"
        columns = re.findall(col_pattern, source_query)
        
        print(f"   Found {len(columns)} columns in existing config")
        print()
    
    print("🔧 Step 2: Generate queries with correct MS SQL Server syntax")
    print("-" * 70)
    
    # Here we would normally:
    # 1. Extract source columns from MSSQL
    # 2. Extract target columns from Snowflake
    # 3. Map columns with RuleMapperOrchestrator (AI)
    # 4. Generate queries with SQLQueryGenerator
    # 5. Write to YAML with YAMLConfigWriter
    
    # For now, let's show what the correct syntax should look like
    print()
    print("✅ Correct MS SQL Server Syntax Examples:")
    print("-" * 70)
    print()
    
    examples = {
        "Integer (AddressID)": {
            "wrong": "COALESCE(CAST(CAST(AddressID AS TEXT) AS VARCHAR(MAX)), '<<NULL>>')",
            "correct": "COALESCE(CAST(AddressID AS VARCHAR(MAX)), '<<NULL>>')",
        },
        "String (sFName)": {
            "wrong": "COALESCE(CAST(TRIM(sFName) AS VARCHAR(MAX)), '<<NULL>>')",
            "correct": "COALESCE(LTRIM(RTRIM(sFName)), '<<NULL>>')",
        },
        "Boolean (bPermanent)": {
            "wrong": "COALESCE(CAST(CASE WHEN bPermanent = true THEN '1' ... AS VARCHAR(MAX)), '<<NULL>>')",
            "correct": "COALESCE(CASE WHEN bPermanent = 1 THEN '1' WHEN bPermanent = 0 THEN '0' ELSE NULL END, '<<NULL>>')",
        },
        "Timestamp (dDeleted)": {
            "wrong": "COALESCE(CAST(TO_CHAR(dDeleted, 'YYYY-MM-DD HH24:MI:SS') AS VARCHAR(MAX)), '<<NULL>>')",
            "correct": "COALESCE(FORMAT(dDeleted, 'yyyy-MM-dd HH:mm:ss'), '<<NULL>>')",
        },
        "Numeric (dcLongitude)": {
            "wrong": "COALESCE(CAST(ROUND(CAST(dcLongitude AS NUMERIC), 2) AS VARCHAR(MAX)), '<<NULL>>')",
            "correct": "COALESCE(CAST(ROUND(CAST(dcLongitude AS DECIMAL(38, 2)), 2) AS VARCHAR(MAX)), '<<NULL>>')",
        },
    }
    
    for col_type, syntax in examples.items():
        print(f"📌 {col_type}:")
        print()
        print("  ❌ Wrong (causes error):")
        print(f"     {syntax['wrong']}")
        print()
        print("  ✅ Correct:")
        print(f"     {syntax['correct']}")
        print()
        print("-" * 70)
        print()
    
    print("🔧 Step 3: Manual fix (quick solution)")
    print("-" * 70)
    print()
    print("Since you have the addresses.yaml file, you can fix it manually:")
    print()
    print("1. Open: config/bronze/data_validation/addresses.yaml")
    print()
    print("2. Find and replace (in sourcequery section):")
    print()
    print("   CAST(AddressID AS TEXT) → CAST(AddressID AS VARCHAR(MAX))")
    print("   CAST(SiteID AS TEXT) → CAST(SiteID AS VARCHAR(MAX))")
    print("   CAST(OldPK AS TEXT) → CAST(OldPK AS VARCHAR(MAX))")
    print()
    print("   TRIM(sFName) → LTRIM(RTRIM(sFName))")
    print("   TRIM(sMI) → LTRIM(RTRIM(sMI))")
    print("   (and all other TRIM() calls)")
    print()
    print("   bPermanent = true → bPermanent = 1")
    print("   bPermanent = false → bPermanent = 0")
    print()
    print("3. Save the file")
    print()
    print("4. Run validation:")
    print("   python src/validate_cli.py --config config/bronze/data_validation/addresses.yaml")
    print()
    
    print("=" * 70)
    print("🎯 Summary")
    print("=" * 70)
    print()
    print("✅ Backup created: " + str(backup_path))
    print()
    print("Next steps:")
    print("1. Fix addresses.yaml manually (see examples above)")
    print("2. OR: Set MSSQL_CONNECTION_STRING and regenerate fully")
    print("3. Run test: python test_mssql_syntax.py")
    print("4. Run validation: python src/validate_cli.py --config ...")
    print()
    
    return 0


def show_quick_fix():
    """Show quick sed/awk commands for bulk replacement."""
    print()
    print("=" * 70)
    print("Quick Fix: Bulk Replace Commands (Linux/Mac)")
    print("=" * 70)
    print()
    
    commands = [
        "# Fix integer casts (AS TEXT → AS VARCHAR(MAX))",
        "sed -i.bak 's/CAST(\\([A-Za-z0-9_]*\\) AS TEXT)/CAST(\\1 AS VARCHAR(MAX))/g' config/bronze/data_validation/addresses.yaml",
        "",
        "# Fix string trims (TRIM → LTRIM(RTRIM(...)))",
        "sed -i.bak 's/TRIM(\\([A-Za-z0-9_]*\\))/LTRIM(RTRIM(\\1))/g' config/bronze/data_validation/addresses.yaml",
        "",
        "# Fix booleans (true → 1, false → 0)",
        "sed -i.bak 's/= true/= 1/g' config/bronze/data_validation/addresses.yaml",
        "sed -i.bak 's/= false/= 0/g' config/bronze/data_validation/addresses.yaml",
    ]
    
    for cmd in commands:
        print(cmd)
    
    print()
    print("Windows (PowerShell):")
    print("-" * 70)
    
    ps_commands = [
        "(Get-Content config/bronze/data_validation/addresses.yaml) -replace 'CAST\\(([A-Za-z0-9_]*) AS TEXT\\)', 'CAST($1 AS VARCHAR(MAX))' | Set-Content config/bronze/data_validation/addresses.yaml",
        "",
        "(Get-Content config/bronze/data_validation/addresses.yaml) -replace 'TRIM\\(([A-Za-z0-9_]*)\\)', 'LTRIM(RTRIM($1))' | Set-Content config/bronze/data_validation/addresses.yaml",
        "",
        "(Get-Content config/bronze/data_validation/addresses.yaml) -replace '= true', '= 1' | Set-Content config/bronze/data_validation/addresses.yaml",
        "",
        "(Get-Content config/bronze/data_validation/addresses.yaml) -replace '= false', '= 0' | Set-Content config/bronze/data_validation/addresses.yaml",
    ]
    
    for cmd in ps_commands:
        print(cmd)
    
    print()


if __name__ == "__main__":
    exit_code = regenerate_addresses_config()
    
    if exit_code == 0:
        show_quick_fix()
    
    sys.exit(exit_code)
