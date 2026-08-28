# Migration Validator Documentation

This folder contains the complete handover and operating documentation.

## Core Principles

Three rules explain most of the design:

1. **The plan is the contract.** `CanonicalValidationPlan`, persisted as JSON in
   `output/plans/`, is the single source of truth. SQL and YAML are render
   targets generated from it — regenerable, never hand-edited.
2. **Generation is AI-only.** There is no rule-based fallback. Without
   `DIAL_API_KEY` the run fails loudly rather than silently comparing guesses.
3. **Exclusions are always reported.** Coverage is printed next to every pass
   rate. A validator that silently drops columns emits a green 100% nobody
   questions.

## Start Here

1. [Handover Guide](HANDOVER_GUIDE.md) — complete technical and operational reference.
2. [Environment Setup](ENVIRONMENT_SETUP.md) — install dependencies and configure `.env`.
3. [Operations Guide](OPERATIONS_GUIDE.md) — run generation, YAML validation, reports, and tests.
4. [Architecture](ARCHITECTURE.md) — component responsibilities and data flow.
5. [Validation Methodology](VALIDATION_METHODOLOGY.md) — what each validation means and how results are interpreted.
6. [Changelog](CHANGELOG.md) — what changed and why.
7. [Future Roadmap](FUTURE_ROADMAP.md) — planned improvements and extension points.

## Quick Commands

From the repository root:

```powershell
# Offline checks — no database, no API key needed
python -m pytest -q
python src\validate_cli.py lint
python tests\e2e\run_all_tests.py --skip-live

# Live
python test_env_connections.py
python tests\e2e\run_all_tests.py
```

| Path | Contents | Hand-editable |
|---|---|---|
| `output/plans/` | Validation plans (the contract) | No |
| `config/bronze/` | Generated YAML render targets | No — regenerate |
| `config/exclusions.yaml` | Exclusion policy | **Yes** |
| `config/database_registry.yaml` | Non-secret connection metadata | **Yes** |
| `output/` | CSV results, logs, test reports | No |
