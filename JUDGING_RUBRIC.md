# Judging Rubric — Evidence Mapping

**Hackathon:** Stream 3 — Connectors for Gemini Applications  
**Project:** Migration Validator

---

## Criterion 0: Team Members' Certification Status — 20%

> Score depends on the proportion of the team holding a Gemini Enterprise certification.
> 0 certified members = 0 points; all members certified = maximum points.

| Team Member | Role | Gemini Enterprise Certified? | Certificate / Evidence Link |
|-------------|------|-------------------------------|------------------------------|
| Ganesh Rustum Mane | _TODO: role_ | ☑ Yes | Certified Partner Specialist – Gemini Enterprise Deployment ([screenshot/link TODO](#)) |
| Kaustubh Verma | _TODO: role_ | ☑ Yes | Certified Partner Specialist – Gemini Enterprise Deployment ([screenshot/link TODO](#)) |
| Ayush Singh Tomar | _TODO: role_ | ☑ Yes | Certified Partner Specialist – Gemini Enterprise Deployment ([screenshot/link TODO](#)) |
| Abdul Rasheed Shaik | _TODO: role_ | ☑ Yes | Certified Partner Specialist – Gemini Enterprise Deployment ([screenshot/link TODO](#)) |

**Certification rate: 4 of 4 team members (100%)**

> **Action needed before submission:** replace the `_TODO: role_` cells with each member's actual
> role on the team, and replace each `[screenshot/link TODO](#)` with a real link to that
> person's Credly certificate or a screenshot of their completion page (upload the screenshot
> into `docs/` or `Project/` and link it here, or paste a Credly badge URL).

---

## Criterion 1: Enabling Client Use Cases — 35%

> The connector must enable meaningful, realistic enterprise use cases through Gemini.

### Implemented Use Cases

| Use Case | Implementation | Evidence | Demo Timestamp |
|----------|---------------|---------|---------------|
| Validate a migration table | `generate_validation_plan` + `execute_validation` tools | `src/gemini_connector/tools.py:generate_validation_plan` | Demo Act 2–4 |
| Investigate a failed migration | `get_validation_failures` + Gemini natural language explanation | `tools.py:get_validation_failures` | Demo Act 5 |
| Migration health dashboard | `get_migration_summary` + `get_business_metrics` | `tools.py:get_migration_summary` | Demo Act 6 |
| Review ambiguous column mappings | `get_pending_reviews` + approval workflow | `approval_store.py` + `tools.py:get_pending_reviews` | Demo Act 3 |
| Explain validation rules | `get_applicable_rules` + `get_rule` | `tools.py:get_applicable_rules` | — |
| Approve a column mapping | `approve_mapping` with actor + version | `tools.py:approve_mapping` | Demo Act 3 |
| Approve a learned rule | `approve_rule` (RULE_ADMIN required) | `tools.py:approve_rule` | Demo security demo |
| Compare migration layers | `get_migration_summary(layer="bronze")` vs `(layer="silver")` | Multi-layer YAML configs | — |
| Find low-coverage tables | `get_coverage(threshold=0.95)` | `tools.py:get_coverage` | Demo Act 6 |
| Investigate data quality failures | `get_validation_failures` + column-level mismatch details | `tools.py:get_validation_failures` | Demo Act 5 |

### Tool Count: 24 tools across 6 categories

Discovery (5) · Mapping (3) · Rules (2) · Plans (3) · Execution (3) · Summary (3) · Write-back (5)

### Gemini Function Calling: Implemented

- `TOOL_DECLARATIONS` in `src/gemini_connector/gemini_agent.py` — 24 Gemini-compatible function schemas
- Multi-round tool loop (max 10 rounds)
- Offline fallback via keyword parsing when no API key

**Documentation:** [`docs/api/connector-tools.md`](docs/api/connector-tools.md)

---

## Criterion 2: Data Accessibility & Integration — 15%

> The connector must enable access to relevant enterprise data sources.

### Implemented Integrations

| System | Driver | Status | Config |
|--------|--------|--------|--------|
| PostgreSQL | psycopg2-binary | Implemented | `SRC_N_DB_TYPE=postgresql` |
| Microsoft SQL Server | pyodbc | Implemented | `SRC_N_DB_TYPE=mssql` |
| AWS Athena | boto3 | Implemented | `SRC_N_DB_TYPE=athena` |
| Snowflake (Bronze/Silver/Gold) | snowflake-connector-python | Implemented | `SNOWFLAKE_*` vars |
| EPAM DIAL proxy | openai SDK | Implemented | `DIAL_API_KEY` |
| Google Gemini | google-generativeai | Implemented | `GOOGLE_API_KEY` |

### Multi-Source Federation

- `discover_connections` returns all registered source connections
- Any source-to-Snowflake pair can be targeted per table
- Portfolio-level summary (`get_migration_summary`) aggregates across all sources

### Token Efficiency

- Summary-first responses (stats, not raw data)
- Pagination on all list operations (page_size max 200)
- No raw dataset dumps to Gemini
- ID-based references (record_id, rule_id) not inline objects

**Documentation:** [`docs/validation/supported-databases.md`](docs/validation/supported-databases.md)

---

## Criterion 3: Write-Back & Authentication — 15%

> The connector must support governed write-back and enterprise authentication.

### Write-Back: Implemented

| Action | Tool | Permission | Audit |
|--------|------|-----------|-------|
| Approve column mapping | `approve_mapping` | `MAPPING_APPROVE` | Yes |
| Reject column mapping | `reject_mapping` | `MAPPING_REJECT` | Yes |
| Modify column mapping | `modify_mapping` | `MAPPING_MODIFY` | Yes |
| Activate rule | `approve_rule` | `RULE_ACTIVATE` | Yes |
| Approve validation plan | `approve_plan` | `PLAN_APPROVE` | Yes |
| Execute validation | `execute_validation` | `VALIDATION_EXECUTE` | Yes |

Every write-back:
1. Requires authentication (bearer token)
2. Checks RBAC permission
3. Requires human actor (AI actor string blocked)
4. Checks expected version (OCC)
5. Writes `AuditRecord` to `output/audit_log.jsonl`
6. Returns new version number

### Authentication: Implemented

| Mode | Mechanism | File |
|------|-----------|------|
| Static | `CONNECTOR_API_TOKEN` env var | `auth.py:StaticTokenProvider` |
| JWT | HS256 with configurable issuer/audience | `auth.py:JWTAuthProvider` |
| Dev | No validation (local only) | `auth.py:DevAuthProvider` |

### Authorization: Implemented (RBAC)

- 5 roles: VIEWER, REVIEWER, RULE_ADMIN, VALIDATION_OPERATOR, ADMIN
- 15 fine-grained permissions
- Resource-level allowlists (source, database, schema)
- Per-user table restrictions (JWT `allowed_tables` claim)

**Documentation:** [`docs/api/authentication.md`](docs/api/authentication.md) · [`docs/api/authorization.md`](docs/api/authorization.md) · [`docs/architecture/security-architecture.md`](docs/architecture/security-architecture.md)

---

## Criterion 4: Documentation & Presentation — 15%

> Enterprise-grade documentation: polished README, architecture diagrams, API reference, presentation, crisp video.

### Documentation Package

| Document | Status | Location |
|----------|--------|----------|
| README.md | Complete | `README.md` |
| System architecture | Complete + Mermaid diagrams | `docs/architecture/system-architecture.md` |
| Gemini integration | Complete | `docs/architecture/gemini-integration.md` |
| Security architecture | Complete | `docs/architecture/security-architecture.md` |
| Data flow | Complete | `docs/architecture/data-flow.md` |
| Sequence diagrams | Complete (6 Mermaid diagrams) | `docs/architecture/sequence-diagrams.md` |
| API/tool reference | Complete (24 tools with examples) | `docs/api/connector-tools.md` |
| Data schemas | Complete | `docs/api/schemas.md` |
| Authentication | Complete | `docs/api/authentication.md` |
| Authorization | Complete | `docs/api/authorization.md` |
| Rule Book | Complete | `docs/rules/rule-book.md` |
| Rule examples | Complete | `docs/rules/rule-examples.md` |
| Human-in-the-loop | Complete | `docs/human-in-the-loop/review-workflow.md` |
| Audit trail | Complete | `docs/human-in-the-loop/audit-trail.md` |
| Approval model | Complete | `docs/human-in-the-loop/approval-model.md` |
| Validation strategies | Complete | `docs/validation/validation-strategies.md` |
| Supported databases | Complete | `docs/validation/supported-databases.md` |
| Normalization rules | Complete | `docs/validation/normalization-rules.md` |
| Local setup | Complete | `docs/deployment/local-setup.md` |
| Environment reference | Complete | `docs/deployment/environment.md` |
| GCP deployment (Cloud Run) | Complete | `docs/deployment/gcp-deployment.md` |
| Demo script | Complete | `docs/hackathon/demo-script.md` |
| Presentation outline | Complete (8 slides) | `docs/hackathon/presentation-outline.md` |
| Business value / ROI | Complete | `docs/hackathon/business-value.md` |
| Video script | Complete (5-minute, timed) | `docs/hackathon/video-script.md` |
| Judging rubric | This file | `JUDGING_RUBRIC.md` |
| Submission checklist | Complete | `SUBMISSION_CHECKLIST.md` |

### README Quality

The README:
- Opens with an enterprise-grade positioning statement (not implementation details)
- Answers all 15 required questions (see README.md sections 1–16)
- Includes architecture ASCII diagram
- Includes full documentation index with links
- Labels all features as Implemented / Prototype / Planned / Demo-only
- No unexplained TODOs
- No broken links
- No credentials

---

## Summary Scorecard

| Criterion | Weight | Evidence Quality | Key Files |
|-----------|--------|-----------------|-----------|
| Team Certification | 20% | 4 of 4 members certified (100%) — see Criterion 0 table above | `JUDGING_RUBRIC.md` |
| Client Use Cases | 35% | 10 use cases, 24 tools, full conversation loop | `tools.py`, `gemini_agent.py`, `api.py` |
| Data Accessibility | 15% | 3 source systems + Snowflake, multi-layer | `validate_cli.py`, `supported-databases.md` |
| Write-Back & Auth | 15% | 6 write tools, JWT+static auth, RBAC, OCC | `auth.py`, `authz.py`, `version_store.py` |
| Documentation | 15% | 24 docs, Mermaid diagrams, video script, rubric, GCP deployment guide | `docs/`, `README.md` |

**Total: 100%**
