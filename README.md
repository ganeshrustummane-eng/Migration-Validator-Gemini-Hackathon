# Migration Validator

> **Migration Validator is a Gemini-enabled enterprise migration intelligence connector that allows users to validate heterogeneous database migrations through natural language while maintaining governed human approval, deterministic validation rules, secure enterprise access, and auditable execution.**

[![Stream 3 — Gemini Connector](https://img.shields.io/badge/Hackathon-Stream%203%20Gemini%20Connector-blue)](#)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-green)](#)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688)](#)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B)](#)
[![Status: Prototype](https://img.shields.io/badge/Status-Hackathon%20Prototype-orange)](#)

---

## Table of Contents

1. [The Problem](#1-the-problem)
2. [Why This Matters](#2-why-this-matters)
3. [Why Gemini?](#3-why-gemini)
4. [Solution Architecture](#4-solution-architecture)
5. [What the Connector Does](#5-what-the-connector-does)
6. [Supported Enterprise Systems](#6-supported-enterprise-systems)
7. [Human-in-the-Loop](#7-human-in-the-loop)
8. [Rule Book](#8-rule-book)
9. [Authentication & Security](#9-authentication--security)
10. [Write-Back](#10-write-back)
11. [Client Use Cases](#11-client-use-cases)
12. [Quickstart](#12-quickstart)
13. [Connecting Gemini](#13-connecting-gemini)
14. [Gemini Tools Exposed](#14-gemini-tools-exposed)
15. [Demo Scenario](#15-demo-scenario)
16. [Business Value & ROI](#16-business-value--roi)
17. [Documentation Index](#17-documentation-index)

---

## 1. The Problem

Enterprise database migrations from heterogeneous sources (PostgreSQL, MSSQL, AWS Athena) into Snowflake medallion architectures are high-risk, labour-intensive operations.

**Today's process:**

```
Data Engineer
  → Write column-mapping scripts manually
  → Generate SQL queries per table
  → Run validation queries
  → Compare source vs. target row counts
  → Manually review mismatches in spreadsheets
  → Escalate to architects for ambiguous mappings
  → Repeat across hundreds of tables
```

**Failure modes:**
- Silent data loss due to wrong column mappings
- Missed Fivetran audit columns polluting comparisons
- No governed approval trail for mapping decisions
- No systematic rule enforcement across tables
- Engineers spending 60–80% of migration time on validation mechanics

---

## 2. Why This Matters

| Dimension | Impact |
|-----------|--------|
| **Scale** | Enterprise migrations involve 100–1,000+ tables across multiple source systems |
| **Risk** | Undetected row count mismatches, type coercion errors, and mapping mistakes cause production incidents |
| **Compliance** | Financial and healthcare data requires auditable approval trails for every mapping decision |
| **Cost** | Senior data engineers spend weeks on validation mechanics instead of architecture |
| **Speed** | Migrations that take 3 months can be reduced to days with automated validation + AI investigation |

---

## 3. Why Gemini?

Migration validation has traditionally been a manual, script-driven process. This is the wrong tool for the job.

### Traditional vs. AI-Enabled Workflow

```
TRADITIONAL WORKFLOW
────────────────────
Engineer → custom scripts → database → raw SQL output → manual spreadsheet review
Problem: No natural language. No reasoning. No investigation. No governance.


GEMINI-ENABLED WORKFLOW
────────────────────────
User ──→ Gemini (natural language)
           ↓
     Migration Connector (24 governed tools)
           ↓
     Migration Validator (deterministic engine)
           ↓
     PostgreSQL / MSSQL / Athena → Snowflake
           ↓
     Explanation + recommendations delivered back to user
```

### What Gemini Provides That Scripts Cannot

| Capability | Scripts | Gemini + Connector |
|------------|---------|-------------------|
| Natural language queries | ✗ | ✓ |
| Cross-table investigation | Manual | Automated |
| Ambiguity resolution | Engineer time | AI-proposed + human-approved |
| Root-cause explanation | None | Conversational |
| Confidence-based routing | None | Auto-accept / review / reject thresholds |
| Audit trail on decisions | None | Append-only JSONL per action |
| Multi-source federation | Complex | Single query |
| Controlled action execution | Scripts run unchecked | Write tools require human actor |

### Gemini Is Not Just a Chatbot Here

Gemini drives a **structured tool-calling workflow**. Every action it takes is:
- Backed by a deterministic tool implementation
- Permission-checked against RBAC
- Version-checked against optimistic concurrency
- Audit-logged with actor, timestamp, and reason
- Subject to human approval before write-back

---

## 4. Solution Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    GEMINI CLIENT                                 │
│              (gemini.google.com / Vertex AI)                    │
└─────────────────────┬───────────────────────────────────────────┘
                      │  Natural language + function calling
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│              MIGRATION VALIDATOR CONNECTOR                       │
│                  FastAPI · Port 8001                            │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐   │
│  │   Auth      │  │   Authz     │  │   24 Tool Functions   │   │
│  │ JWT/Static/ │  │ 5 Roles     │  │ Discovery · Mapping   │   │
│  │ Dev modes   │  │ 15 Perms    │  │ Rules · Approvals     │   │
│  └─────────────┘  └─────────────┘  │ Validation · Metrics  │   │
│                                     └──────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                  Persistence Layer                        │  │
│  │  ApprovalStore · AuditLogger · VersionStore · Metrics    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────┬───────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────┐
│              MIGRATION VALIDATOR ENGINE                          │
│                                                                  │
│  ValidationPipeline → CanonicalValidationPlan                   │
│  ExclusionManager  → RuleBook  → SQLQueryGenerator              │
│  ConfidenceScorer  → FuzzyMatcher → AIRulePlanner               │
└──────┬──────────────────────────────────────┬───────────────────┘
       │                                      │
       ▼                                      ▼
┌──────────────────┐                ┌───────────────────┐
│  SOURCE SYSTEMS  │                │  TARGET SYSTEM    │
│                  │                │                   │
│  PostgreSQL      │                │  Snowflake        │
│  MSSQL           │                │  (Bronze/Silver/  │
│  AWS Athena      │                │   Gold layers)    │
└──────────────────┘                └───────────────────┘
```

**Full architecture documentation:** [`docs/architecture/system-architecture.md`](docs/architecture/system-architecture.md)

---

## 5. What the Connector Does

The connector is a **FastAPI REST server** (`start_connector.py`) that:

1. **Exposes 24 structured tools** to Gemini via a `/tools` endpoint returning Gemini function-calling declarations
2. **Dispatches tool calls** from Gemini via `POST /tools/{tool_name}`
3. **Enforces authentication** on every write operation (JWT, static token, or dev mode)
4. **Enforces RBAC authorization** — 5 roles, 15 fine-grained permissions
5. **Applies optimistic concurrency control** — version conflict detection prevents concurrent write conflicts
6. **Audit-logs every action** to an append-only JSONL file
7. **Prevents AI self-approval** — write tools reject any actor string matching `gemini_ai` or `ai`
8. **Keeps all credentials server-side** — Gemini never receives passwords, keys, or raw connection strings

**Connector documentation:** [`docs/architecture/gemini-integration.md`](docs/architecture/gemini-integration.md)

---

## 6. Supported Enterprise Systems

| System | Role | Status |
|--------|------|--------|
| **PostgreSQL** | Source database | Implemented |
| **Microsoft SQL Server (MSSQL)** | Source database | Implemented |
| **AWS Athena** | Source database | Implemented |
| **Snowflake** | Target data warehouse | Implemented |
| **EPAM DIAL** | AI proxy (GPT-4o, Claude, Gemini, Llama) | Implemented |
| **Google Gemini** | Conversational agent + function calling | Implemented |
| **Anthropic Claude** | Direct AI backend (fallback) | Implemented |

**Medallion layers supported:** Bronze, Silver, Gold, Reporting

**Multi-connection registry:** Up to N named source connections (`SRC_1`, `SRC_2`, `SRC_3`, ...) discoverable via `discover_connections` tool

---

## 7. Human-in-the-Loop

Migration Validator enforces a **governed approval workflow** for every column mapping decision. AI recommendations are proposals — humans decide.

### Confidence Thresholds

| Confidence | Action | Who decides |
|------------|--------|-------------|
| ≥ 95% | Auto-accepted | System (configurable) |
| 75–94% | Queued for human review | Human reviewer |
| < 75% | Mandatory human review | Human reviewer |

### Approval Flow

```
Gemini AI
   ↓ generates recommendation
AI Recommendation (with confidence score)
   ↓ if confidence < 95%
Human Review Queue (ApprovalStore)
   ├── Approve  → ApprovalRecord{status: APPROVED, actor, timestamp, reason, version}
   ├── Modify   → ApprovalRecord{status: MODIFIED, new_target_column, new_rule, ...}
   └── Reject   → ApprovalRecord{status: REJECTED, rejection_reason, ...}
         ↓
CanonicalValidationPlan (immutable plan with all approved mappings)
   ↓
SQL Generation (deterministic from plan)
   ↓
Validation Execution
```

### Human Review Interfaces

- **Streamlit Web UI** — Tab: Review & Approve (`webapp/app.py`)
- **Gemini Chat** — `approve_mapping`, `reject_mapping`, `modify_mapping` tools (require actor)
- **REST API** — `POST /approve/mapping/{record_id}`

**Full HITL documentation:** [`docs/human-in-the-loop/review-workflow.md`](docs/human-in-the-loop/review-workflow.md)

---

## 8. Rule Book

The Rule Book governs how columns are mapped and validated across all tables.

### Rule Categories

| Category | Description | Storage |
|----------|-------------|---------|
| **Base Rules** | Built-in Fivetran column exclusions | `config/exclusions.yaml` |
| **Global Exclusions** | Per-DB-type user-defined exclusions | `config/postgresql_exclusions.yaml` etc. |
| **Pattern Rules** | Regex-based column exclusion patterns | Config YAML |
| **Transformation Rules** | Type coercion rules (boolean→text, timestamp→text) | Validation plan |
| **Normalization Rules** | Cross-dialect SQL normalization | Generated YAML |
| **Learned Rules** | AI-proposed rules pending or approved | `rule_book_learned.json` |

### Rule Lifecycle

```
AI Proposal
   ↓
Draft (saved to rule_book_learned.json)
   ↓
Human Review (RULE_ADMIN role required for activation)
   ↓
Approved → Active
   ↓
Versioned (every change tracked via VersionStore)
```

**Rule Book documentation:** [`docs/rules/rule-book.md`](docs/rules/rule-book.md)

---

## 9. Authentication & Security

### Authentication Modes

| Mode | Mechanism | Use Case |
|------|-----------|----------|
| `static` (default) | `CONNECTOR_API_TOKEN` env var | Hackathon demo, CI/CD |
| `jwt` | HS256 JWT with configurable issuer/audience | Enterprise integration |
| `dev` | No validation, ADMIN role | Local development only |

### Authorization (RBAC)

| Role | Key Permissions |
|------|----------------|
| `VIEWER` | Read-only: schema, mappings, rules, plans |
| `REVIEWER` | VIEWER + approve/modify/reject mappings, approve plans |
| `RULE_ADMIN` | REVIEWER + create/approve/activate rules |
| `VALIDATION_OPERATOR` | VIEWER + approve mappings/plans, execute validation |
| `ADMIN` | All 15 permissions |

### Security Guarantees

- **No credentials exposed to Gemini** — connection strings stay server-side
- **AI self-approval blocked** — `gemini_ai` and `ai` actor strings rejected at tool level
- **Optimistic concurrency control** — version conflict detection (HTTP 409) prevents concurrent write conflicts
- **Append-only audit trail** — `output/audit_log.jsonl` — immutable, no secrets logged
- **Resource-level allowlists** — `AUTHZ_SOURCE_ALLOWLIST`, `AUTHZ_DB_ALLOWLIST`, `AUTHZ_SCHEMA_ALLOWLIST`
- **Per-user table restrictions** — JWT `allowed_tables` claim for row-level access control

**Security documentation:** [`docs/architecture/security-architecture.md`](docs/architecture/security-architecture.md)

---

## 10. Write-Back

Write-back actions in Migration Validator are:

1. **Approval decisions** — `approve_mapping`, `reject_mapping`, `modify_mapping` → persisted to `output/approval_store.jsonl`
2. **Plan approvals** — `approve_plan` → triggers validation SQL generation
3. **Validation execution** — `execute_validation` → runs generated SQL against live databases
4. **Rule activation** — `approve_rule` → promotes draft rule to active status

Every write-back:
- Requires an authenticated human actor (not AI)
- Checks the expected version (optimistic concurrency)
- Writes an `AuditRecord` to the audit log
- Returns the new version number for subsequent operations

---

## 11. Client Use Cases

| # | Use Case | Gemini Prompt | Tools Used |
|---|----------|---------------|-----------|
| 1 | Validate migration | `"Validate the customer table migration"` | `generate_validation_plan`, `execute_validation` |
| 2 | Investigate failure | `"Why did the events table fail validation?"` | `get_validation_failures`, `get_validation_result` |
| 3 | Migration health | `"Give me a migration health dashboard"` | `get_migration_summary`, `get_business_metrics` |
| 4 | Review ambiguous mappings | `"Show me all mappings needing review"` | `get_pending_reviews`, `get_column_mappings` |
| 5 | Explain validation rules | `"What rules apply to the general_ledger table?"` | `get_applicable_rules`, `get_rule` |
| 6 | Approve mapping | `"Approve the created_at mapping"` | `approve_mapping` |
| 7 | Approve learned rules | `"Approve the timestamp normalization rule"` | `approve_rule` |
| 8 | Compare migration runs | `"How does today's run compare to yesterday?"` | `get_migration_summary`, `get_coverage` |
| 9 | Find low-coverage tables | `"Which tables are below 95% coverage?"` | `get_coverage` |
| 10 | Investigate data quality | `"Show me all failed rows in customer"` | `get_validation_failures`, `get_validation_result` |

---

## 12. Quickstart

### Prerequisites

- Python 3.10+
- Access to at least one source database (PostgreSQL, MSSQL, or AWS Athena)
- Snowflake account
- Google Gemini API key (for `gemini-1.5-pro` agent) **or** EPAM DIAL API key

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

Minimum `.env` for Gemini connector:

```bash
# Source database (PostgreSQL example)
SRC_1_DB_TYPE=postgresql
SRC_1_HOST=your-postgres-host
SRC_1_PORT=5432
SRC_1_USERNAME=your-user
SRC_1_PASSWORD=your-password

# Snowflake target
SNOWFLAKE_ACCOUNT=your-account
SNOWFLAKE_USERNAME=your-user
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=your-warehouse
SNOWFLAKE_ROLE=your-role

# Gemini API key
GOOGLE_API_KEY=your-gemini-api-key

# Connector authentication
AUTH_MODE=static
CONNECTOR_API_TOKEN=your-secret-token
CONNECTOR_ROLES=ADMIN
```

### 3. Start the connector server

```bash
python start_connector.py
```

Output:
```
Migration Validator Connector
  URL:    http://0.0.0.0:8001
  Docs:   http://localhost:8001/docs
  Health: http://localhost:8001/health
  Tools:  http://localhost:8001/tools
```

### 4. Start the Streamlit web UI (optional)

```bash
streamlit run webapp/app.py
```

### 5. Run the CLI validator

```bash
python -m src.validate_cli
```

**Full setup guide:** [`docs/deployment/local-setup.md`](docs/deployment/local-setup.md)

### 6. Deploy to Google Cloud (Cloud Run)

```bash
gcloud builds submit --tag YOUR_REGION-docker.pkg.dev/YOUR_PROJECT_ID/migration-validator/connector:latest .
gcloud run deploy migration-connector --image YOUR_REGION-docker.pkg.dev/YOUR_PROJECT_ID/migration-validator/connector:latest --region YOUR_REGION
```

**Full GCP deployment guide (Secret Manager, VPC access, rollback):** [`docs/deployment/gcp-deployment.md`](docs/deployment/gcp-deployment.md)

---

## 13. Connecting Gemini

The connector exposes a `/tools` endpoint that returns Gemini-compatible function declarations.

### Register as a Gemini Extension

1. Start the connector: `python start_connector.py`
2. Point Gemini at: `http://your-host:8001`
3. Gemini will call `GET /tools` to discover all 24 tool schemas
4. Tool calls arrive as `POST /tools/{tool_name}` with JSON `{"arguments": {...}}`

### Direct API Usage

```python
import requests

# Discover available tools
tools = requests.get("http://localhost:8001/tools").json()

# Call a tool
result = requests.post(
    "http://localhost:8001/tools/get_migration_summary",
    json={"arguments": {"layer": "bronze"}},
    headers={"Authorization": "Bearer your-token"}
).json()

# Conversational agent
response = requests.post(
    "http://localhost:8001/chat",
    json={"message": "Validate the customer table", "actor": "jane.doe@company.com"}
).json()
```

### Gemini Function Calling

```python
import google.generativeai as genai
# See docs/architecture/gemini-integration.md for full example
```

---

## 14. Gemini Tools Exposed

The connector exposes **24 tools** organized into 6 categories:

| Category | Tools | Description |
|----------|-------|-------------|
| **Discovery** | `discover_connections`, `list_databases`, `list_schemas`, `list_tables`, `get_table_schema` | Explore connected databases |
| **Mapping** | `get_table_mapping`, `get_column_mappings`, `get_pending_reviews` | Column mapping analysis |
| **Rules** | `get_rule`, `get_applicable_rules` | Rule Book access |
| **Plans** | `get_validation_plan`, `generate_validation_plan`, `generate_validation_sql` | Validation planning |
| **Execution** | `execute_validation`, `get_validation_result`, `get_validation_failures` | Run validations |
| **Summary** | `get_migration_summary`, `get_coverage`, `get_business_metrics` | Portfolio view |
| **Write-Back** | `approve_mapping`, `reject_mapping`, `modify_mapping`, `approve_rule`, `approve_plan` | Governed decisions |

**Full tool documentation with schemas and examples:** [`docs/api/connector-tools.md`](docs/api/connector-tools.md)

---

## 15. Demo Scenario

**Business context:** An enterprise is migrating `customer` table data from PostgreSQL (`fms.public.customer`) into Snowflake Bronze (`DEV_DB.PUBLIC.CUSTOMER`).

**Step-by-step Gemini interaction:**

```
User: "Validate the customer migration."

Gemini calls: discover_connections()
→ Returns: PostgreSQL (fms), Snowflake (DEV_DB)

Gemini calls: get_table_schema(table="customer", source="postgresql")
→ Returns: 42 columns including id, email, created_at, _fivetran_synced

Gemini calls: generate_validation_plan(source_table="customer", layer="bronze")
→ Returns: Plan with 38 active mappings (4 excluded as Fivetran audit cols)
→ 2 mappings at 82% confidence → queued for human review

Gemini: "I've generated a validation plan. 38 columns mapped, 4 excluded
         (Fivetran audit columns). 2 mappings need your review:
         - 'created_timestamp' → 'CREATED_AT' (82% confidence)
         - 'cust_id' → 'CUSTOMER_ID' (78% confidence)"

User: "Approve the created_timestamp mapping."

Gemini calls: approve_mapping(record_id="...", actor="jane.doe@corp.com",
                              reason="Confirmed via source DDL review")
→ ApprovalRecord written, AuditRecord logged, version bumped

Gemini calls: execute_validation(source_table="customer", actor="jane.doe@corp.com")
→ Returns: {status: "WARNING", coverage: 99.1%, failed_checks: 1}

Gemini: "Validation complete. 99.1% coverage. One check failed:
         'email' column has 12 NULLs in source, 0 in target.
         This may indicate a filtering difference in the Snowflake load."
```

**Full demo script:** [`docs/hackathon/demo-script.md`](docs/hackathon/demo-script.md)

---

## 16. Business Value & ROI

> **Note:** The following metrics are from demo/pilot measurements in a controlled environment. Production results will vary by migration scale and complexity.

| Metric | Demo Benchmark | Notes |
|--------|---------------|-------|
| Tables validated per run | 4 (pilot) | Scales linearly |
| Columns auto-mapped (exact match) | 100% (events table) | 38/38 exact |
| Mappings requiring human review | 0–25% | Depends on schema similarity |
| Manual SQL queries avoided per table | 2–4 | Count + data validation SQL |
| Validation plan generation time | < 30 seconds | Per table, AI calls included |
| Audit records per approval action | 1 | Append-only, immutable |
| Mismatch detection rate | 100% (simulated failures) | Deterministic SQL comparison |

### Qualitative Business Impact

- **Compliance:** Every mapping decision has an auditable human actor, timestamp, reason, and version
- **Risk reduction:** Confidence scoring surfaces ambiguous mappings before they reach production
- **Investigation speed:** Gemini can explain failures in natural language — no SQL expertise required
- **Scale:** Multi-table batch processing with portfolio-level coverage dashboard

**Full ROI documentation:** [`docs/hackathon/business-value.md`](docs/hackathon/business-value.md)

---

## 17. Documentation Index

| Section | Document | Description |
|---------|----------|-------------|
| **Architecture** | [`docs/architecture/system-architecture.md`](docs/architecture/system-architecture.md) | Full system design with Mermaid diagrams |
| | [`docs/architecture/gemini-integration.md`](docs/architecture/gemini-integration.md) | Gemini function calling deep dive |
| | [`docs/architecture/security-architecture.md`](docs/architecture/security-architecture.md) | Auth, authz, audit, concurrency |
| | [`docs/architecture/data-flow.md`](docs/architecture/data-flow.md) | Data flow and normalization |
| | [`docs/architecture/sequence-diagrams.md`](docs/architecture/sequence-diagrams.md) | End-to-end sequence diagrams |
| **API** | [`docs/api/connector-tools.md`](docs/api/connector-tools.md) | All 24 tools: inputs, outputs, examples |
| | [`docs/api/schemas.md`](docs/api/schemas.md) | Data structures and plan schema |
| | [`docs/api/authentication.md`](docs/api/authentication.md) | Auth modes and token setup |
| | [`docs/api/authorization.md`](docs/api/authorization.md) | RBAC roles and permissions |
| **Rules** | [`docs/rules/rule-book.md`](docs/rules/rule-book.md) | Rule categories, lifecycle, examples |
| | [`docs/rules/rule-examples.md`](docs/rules/rule-examples.md) | Normalization rule examples |
| **Human-in-the-Loop** | [`docs/human-in-the-loop/review-workflow.md`](docs/human-in-the-loop/review-workflow.md) | Approval flow and confidence model |
| | [`docs/human-in-the-loop/approval-model.md`](docs/human-in-the-loop/approval-model.md) | ApprovalStore schema and states |
| | [`docs/human-in-the-loop/audit-trail.md`](docs/human-in-the-loop/audit-trail.md) | AuditLogger and audit record format |
| **Validation** | [`docs/validation/validation-strategies.md`](docs/validation/validation-strategies.md) | Count and data validation strategies |
| | [`docs/validation/supported-databases.md`](docs/validation/supported-databases.md) | Source system capabilities |
| | [`docs/validation/normalization-rules.md`](docs/validation/normalization-rules.md) | Type normalization across dialects |
| **Deployment** | [`docs/deployment/local-setup.md`](docs/deployment/local-setup.md) | Step-by-step local setup |
| | [`docs/deployment/environment.md`](docs/deployment/environment.md) | All environment variables |
| | [`docs/deployment/gcp-deployment.md`](docs/deployment/gcp-deployment.md) | Cloud Run deployment for the connector + review UI |
| **Hackathon** | [`docs/hackathon/demo-script.md`](docs/hackathon/demo-script.md) | Rehearsed demo script |
| | [`docs/hackathon/presentation-outline.md`](docs/hackathon/presentation-outline.md) | 8-slide deck content |
| | [`docs/hackathon/business-value.md`](docs/hackathon/business-value.md) | ROI analysis |
| | [`docs/hackathon/video-script.md`](docs/hackathon/video-script.md) | 5-minute video script |
| **Submission** | [`JUDGING_RUBRIC.md`](JUDGING_RUBRIC.md) | Criterion-by-criterion evidence mapping |
| | [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md) | Pre-submission verification checklist |

---

## Repository Structure

```
Migration-validator/
├── README.md                          # This file
├── JUDGING_RUBRIC.md                  # Hackathon rubric evidence mapping
├── SUBMISSION_CHECKLIST.md            # Pre-submission checklist
│
├── start_connector.py                 # FastAPI connector entry point
├── webapp/app.py                      # Streamlit web UI
├── demo_security.py                   # Security demonstration script
├── Dockerfile                         # Container image for Cloud Run (see docs/deployment/gcp-deployment.md)
│
├── src/
│   ├── validate_cli.py                # CLI interface (3,700+ lines)
│   ├── gemini_connector/
│   │   ├── api.py                     # FastAPI REST server
│   │   ├── tools.py                   # 24 tool implementations
│   │   ├── gemini_agent.py            # GeminiAgent + TOOL_DECLARATIONS
│   │   ├── auth.py                    # JWT/Static/Dev auth providers
│   │   ├── authz.py                   # RBAC (5 roles, 15 permissions)
│   │   ├── approval_store.py          # Human approval persistence
│   │   ├── audit.py                   # Append-only audit logger
│   │   ├── version_store.py           # Optimistic concurrency control
│   │   └── metrics.py                 # Business metrics tracker
│   └── core/
│       ├── validation_plan.py         # CanonicalValidationPlan
│       ├── plan_store.py              # Plan persistence
│       └── validation_pipeline.py    # AI matching pipeline
│
├── config/
│   ├── exclusions.yaml                # Base exclusion rules
│   ├── postgresql_exclusions.yaml     # PostgreSQL-specific exclusions
│   ├── mssql_exclusions.yaml          # MSSQL-specific exclusions
│   └── database_registry.yaml        # Non-secret connection metadata
│
├── output/
│   ├── plans/                         # Generated validation plans (JSON)
│   ├── audit_log.jsonl                # Immutable audit trail
│   ├── approval_store.jsonl           # Approval decisions
│   └── entity_versions.json          # OCC version store
│
├── Project/config/
│   ├── bronze/                        # Bronze layer validation configs
│   ├── silver/                        # Silver layer validation configs
│   └── gold/                          # Gold layer validation configs
│
├── tests/
│   ├── test_security.py              # 31 security tests (auth, authz, audit)
│   ├── test_plan_contract.py         # Plan serialization contract tests
│   ├── test_exclusion_manager.py     # Exclusion config tests
│   ├── test_config_schema.py         # YAML config generation tests
│   ├── test_ai_only_generation.py    # AI-required regression guards
│   └── e2e/run_all_tests.py          # Orchestrated end-to-end runner
│
├── docs/                              # Enterprise documentation package
│   ├── architecture/
│   ├── api/
│   ├── rules/
│   ├── human-in-the-loop/
│   ├── validation/
│   ├── deployment/
│   └── hackathon/
│
└── requirements.txt                   # Python dependencies
```

---

## Status Labels Used in This Repository

| Label | Meaning |
|-------|---------|
| **Implemented** | Feature is built, tested, and working |
| **Prototype** | Feature works in demo conditions; not hardened for production scale |
| **Planned** | Feature is designed but not yet built |
| **Demo-only** | Feature works for the demo scenario; may have rough edges outside it |

---

*Migration Validator — Hackathon Stream 3: Connectors for Gemini Applications*
