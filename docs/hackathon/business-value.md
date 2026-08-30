# Business Value & ROI

> All metrics below are from the hackathon prototype in demo/pilot conditions unless otherwise noted. Production results will vary.

---

## The Problem in Numbers

Enterprise database migrations at scale typically involve:

| Challenge | Typical Scale | Impact |
|-----------|--------------|--------|
| Tables to migrate | 100–1,000+ | Weeks of engineer time |
| Columns per table | 20–100 | Manual SQL authoring per column |
| Source systems | 2–5 heterogeneous DBs | Different dialects, different type systems |
| Validation SQL per table | 4–8 queries | Count + data validation per layer |
| Manual review cycles | 3–5 per table | Spreadsheet-based, no audit trail |
| Migration project duration | 3–12 months | High labour cost |

---

## Quantified Demo Benchmarks

*(Pilot measurement — controlled demo environment)*

| Metric | Value | Notes |
|--------|-------|-------|
| Tables validated in demo | 4 (events, general_ledger_line_items, migration_test, xyz) | Bronze layer |
| Columns processed | 38+ per table | events: 38 columns |
| Exact match rate | 100% (events table) | All 38 columns matched without AI |
| AI calls required | 0 (events) | Strong schema naming consistency |
| Validation plan generation time | < 30 seconds | Per table including schema extraction |
| Manual SQL queries avoided | 4 per table (count + data per source/target) | Deterministic SQL generation from plan |
| Audit records per approval action | 1 | Append-only, immutable |
| Concurrent write conflicts detected | 100% | VersionConflictError correctly raised |
| Credential leakage in API responses | 0 | All 3 secret leakage tests pass |
| Security test pass rate | 34/34 | 100% |

---

## Qualitative Business Impact

### 1. Compliance & Auditability

**Before:** No documented approval trail. Mapping decisions made verbally or in email.

**After:** Every mapping decision recorded with:
- Human actor (email)
- Timestamp (ISO-8601 UTC)
- Reason (free text)
- Previous and new state
- Version number

Suitable for regulated industries (healthcare, finance) requiring documented data lineage.

---

### 2. Risk Reduction via Confidence Scoring

**Before:** All mappings treated equally — no signal for which are risky.

**After:** Three-tier confidence routing:
- ≥95%: Auto-accepted, move fast
- 75–94%: Standard review, engineer decides
- <75%: Mandatory review, never auto-approved

Engineers spend review time on genuinely ambiguous mappings, not obvious ones.

---

### 3. Investigation Speed via Natural Language

**Before:** DBA investigates failures by running ad-hoc SQL queries. Takes 30–60 minutes.

**After:** Gemini explains failures in natural language with root-cause hypotheses:
> "12 NULLs in source email column not present in Snowflake — likely a connector filter."

Investigation time: seconds.

---

### 4. SQL Authoring Automation

**Before:** Engineer manually writes 4–8 validation SQL queries per table:
- Source count query
- Target count query  
- Source data normalization query
- Target data normalization query
- (Repeat per layer)

**After:** Deterministic SQL generation from `CanonicalValidationPlan`. Zero manual SQL for well-matched schemas.

---

### 5. Multi-Source Federation

**Before:** Separate validation scripts per source system. No unified view.

**After:** Single Gemini query covers PostgreSQL + MSSQL + Athena → Snowflake simultaneously. Portfolio-level coverage dashboard in one natural language question.

---

## ROI Formula (Illustrative)

For a migration with 200 tables, 40 columns average:

```
Manual approach:
  200 tables × 40 columns × 5 min per mapping = 6,667 hours
  + 200 tables × 4 SQL queries × 30 min per query = 400 hours
  Total: ~7,067 hours

Automated approach (at 90% automation rate):
  200 tables × 40 columns × 10% requiring review × 5 min = 667 hours
  + SQL generation: automated (near zero)
  + Investigation: Gemini natural language (factor of 10× faster)
  Total: ~700 hours

Time savings: ~6,367 hours (~90% reduction)
At €100/hour: ~€636,700 saved per migration project
```

This is an illustrative calculation based on industry-typical effort estimates, not measured production data.

---

## Lessons Learned: Getting a REST Connector Bound to Gemini Enterprise

Registering this connector with the hackathon's shared Gemini Enterprise app surfaced a real
protocol mismatch worth documenting, since it likely affects other REST/OpenAPI connector teams:

1. **`agent.json` (AgentCard) registration invokes the A2A protocol, not OpenAPI discovery.**
   We assumed Gemini Enterprise would call `/tools/{tool_name}` per our published OpenAPI spec
   (`/openapi.json`), since that's the standard "custom connector" pattern. Instead, the
   registered `url` is called directly over A2A JSON-RPC (`message/send`) — confirmed by a live
   `404 Not Found` the first time Gemini Enterprise tried to invoke us.
2. **Fix: a thin protocol bridge, not a rewrite.** Rather than re-architecting the connector as
   an MCP/A2A-native server, we added [`src/gemini_connector/a2a.py`](../../src/gemini_connector/a2a.py) —
   translates the A2A `message/send` envelope into the same `GeminiAgent.chat()` call our
   existing `/chat` REST endpoint already used. Zero duplicated business logic.
3. **The model name also needs independent verification, not just internal consistency.**
   A hardcoded default model name (`gemini-3.6-flash`) turned out not to exist as a Vertex AI
   publisher model in this project's region, even though it worked against the separate Gemini
   Developer API locally — confirmed via a live `404` from Cloud Run logs, not assumption.
   Switched to `gemini-2.5-flash` after cross-checking against another team's confirmed-working
   Vertex AI model in the same hackathon environment, rather than guessing again.
4. **Takeaway:** "the schema validates" and "the registration was accepted" are necessary but
   not sufficient — the only real proof a connector works is an end-to-end call through the
   actual Gemini Enterprise UI, checked against server-side logs, not just a green checkmark
   from whoever reviews the registration request.

---

## Why This Matters for Stream 3 (Gemini Connectors)

Migration Validator demonstrates what a purpose-built Gemini connector enables:

| Traditional Tool | Gemini-Connected Tool |
|-----------------|----------------------|
| CLI script | Conversational investigation |
| Manual SQL | AI-generated, human-approved |
| Spreadsheet review | Governed approval workflow with audit |
| Separate per-system scripts | Federated multi-source view |
| No governance | RBAC + OCC + audit trail |
| Expert required | Any stakeholder can query status |

The connector pattern enables Gemini to act as a **governed intelligent agent** — not just a chatbot — over complex enterprise data operations.
