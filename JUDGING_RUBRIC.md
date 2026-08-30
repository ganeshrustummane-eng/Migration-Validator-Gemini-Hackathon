# Judging Rubric — Evidence Mapping

**Hackathon:** Stream 3 — Connectors for Gemini Applications  
**Project:** Migration Validator

---

## Criterion 0: Team Members' Certification Status — 20%

> Score depends on the proportion of the team holding a Gemini Enterprise certification.
> 0 certified members = 0 points; all members certified = maximum points.

| Team Member | Role | Gemini Enterprise Certified? | Certificate / Evidence Link |
|-------------|------|-------------------------------|------------------------------|
| Ganesh Rustum Mane | _TODO: role_ | ☑ Yes | [Badge 1](https://www.credly.com/badges/13e2241f-f6ed-4e3d-9dea-8a0b7cf14037/public_url) · [Badge 2](https://www.credly.com/badges/4a1f552a-f0a8-46b9-88df-166ec34078e3/public_url) · [Badge 3](https://www.credly.com/badges/f4766542-66bf-44e9-b555-51c01070dc69/public_url) |
| Kaustubh Verma | _TODO: role_ | ☑ Yes (3 certifications) | [Badge 1](https://www.credly.com/badges/e62f25ca-ab29-4bf5-9f6c-acabb7bbfdfb/public_url) · [Badge 2](https://www.credly.com/badges/f665b0e3-5f39-421c-bb54-78830da94376/public_url) · _TODO: 3rd badge link (duplicate submitted)_ |
| Ayush Singh Tomar | _TODO: role_ | ☑ Yes | [Deploy Gemini Enterprise with Workspace Data Sources and Model Armor](https://www.credly.com/badges/baf7e3e0-e2ab-4a0d-a0b0-dd1cb418c7ae/public_url) · [Govern Agent Access with Gemini Enterprise Agent Platform](https://www.credly.com/badges/ce957ebb-c6aa-48e1-9efb-568dd23c71d8/public_url) · [Add Agents to Gemini Enterprise](https://www.credly.com/badges/e90d0db0-a95d-40d4-951b-ddb24c2f1616/public_url) |
| Abdul Rasheed Shaik | _TODO: role_ | ☑ Yes (incl. advanced certification) | [Badge 1](https://partner.skills.google/public_profiles/59c33cd3-d7fd-4a3e-8dc3-b970fb95b366/badges/27404094) · [Badge 2](https://partner.skills.google/public_profiles/59c33cd3-d7fd-4a3e-8dc3-b970fb95b366/badges/27391207) · [Badge 3](https://partner.skills.google/public_profiles/59c33cd3-d7fd-4a3e-8dc3-b970fb95b366/badges/27390964) · [Badge 4](https://partner.skills.google/public_profiles/59c33cd3-d7fd-4a3e-8dc3-b970fb95b366/badges/27389815) · [Badge 5](https://partner.skills.google/public_profiles/59c33cd3-d7fd-4a3e-8dc3-b970fb95b366/badges/27389132) · [Badge 6](https://partner.skills.google/public_profiles/59c33cd3-d7fd-4a3e-8dc3-b970fb95b366/badges/27387697) |

**Certification rate: 4 of 4 team members (100%)**

> **Action needed before submission:** replace the `_TODO: role_` cells with each member's actual
> role on the team, and replace the remaining `_TODO: Credly badge link_` cells with each
> person's Credly public badge URL once shared.

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

### Gemini Enterprise Binding: Live and Verified (not just local)

This connector is registered with the hackathon's shared Gemini Enterprise app
(`epa.ms/gemini-enterprise`) as **"Migration Validator Connector"**, deployed on Cloud Run at
`https://migration-connector-877936790636.us-central1.run.app`. Verified end-to-end on
2026-08-30 by asking Gemini Enterprise directly (not curl, not localhost) *"what migrations
are configured?"* — it correctly routed the question through the connector and returned live
data (real PostgreSQL/MSSQL/Athena source connections, real Snowflake target).

Getting there required solving a genuine protocol mismatch: Gemini Enterprise's `agent.json`
registration invokes the registered URL over the **A2A (Agent2Agent) protocol** (JSON-RPC 2.0),
not via OpenAPI tool discovery as initially assumed — confirmed by a live `404` when Gemini
Enterprise called the connector's base URL. [`src/gemini_connector/a2a.py`](src/gemini_connector/a2a.py)
is a thin A2A↔REST bridge that translates incoming `message/send` calls into the same
`GeminiAgent` conversation loop `/chat` uses — no tool-calling logic duplicated. See
[`docs/architecture/gemini-integration.md`](docs/architecture/gemini-integration.md) for the
full registration/IAM details and [`docs/hackathon/agent.json`](docs/hackathon/agent.json) for
the registered AgentCard.

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
| Client Use Cases | 35% | 10 use cases, 24 tools, full conversation loop, **live-verified Gemini Enterprise binding** (not just local) | `tools.py`, `gemini_agent.py`, `api.py`, `a2a.py` |
| Data Accessibility | 15% | 3 source systems + Snowflake, multi-layer | `validate_cli.py`, `supported-databases.md` |
| Write-Back & Auth | 15% | 6 write tools, JWT+static auth, RBAC, OCC | `auth.py`, `authz.py`, `version_store.py` |
| Documentation | 15% | 24 docs, Mermaid diagrams, video script, rubric, GCP deployment guide | `docs/`, `README.md` |

**Total: 100%**
