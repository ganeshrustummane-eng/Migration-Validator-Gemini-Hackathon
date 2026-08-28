# End-to-End Test Runner

This folder contains the orchestrated validation test for the Migration Validator.

Run from the repository root:

```powershell
python tests/e2e/run_all_tests.py
```

Run without live databases:

```powershell
python tests/e2e/run_all_tests.py --skip-live
```

Run a selected layer/table set:

```powershell
python tests/e2e/run_all_tests.py --layer bronze --tables Addresses AcctSoftware
```

The runner checks, in order:

1. Python dependencies and required imports
2. Package imports
3. YAML configuration loading and grouped-config flattening
4. Generated SQL dialect safety and aggregate comma rules
5. Existing regression tests
6. Live source/target connectivity
7. Live YAML validation execution
8. Output CSV/log artifact creation

Results are written to `output/test_runs/<run_id>/test_report.json`.

A validation `FAIL` means the framework detected a real source/target data-quality difference. A validation `ERROR` means the framework could not execute the check and fails the orchestrated test.
