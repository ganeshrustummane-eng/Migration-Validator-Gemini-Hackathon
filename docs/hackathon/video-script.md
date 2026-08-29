# 5-Minute Video Script

**Target:** < 5 minutes  
**Format:** Screen recording with voiceover  
**Tone:** Confident, concise, enterprise-ready

---

## Setup Checklist (Before Recording)

- [ ] Connector running: `python start_connector.py`
- [ ] Web UI running: `streamlit run webapp/app.py`
- [ ] Gemini Chat tab open with actor `demo@company.com` entered
- [ ] Review & Approve tab pre-loaded with 1 pending mapping
- [ ] Terminal ready with `demo_security.py` command staged
- [ ] Browser zoom: 125% for readability

---

## Segment 1 — The Problem (0:00–0:30)

**[Show: blank slide or title card]**

> "Enterprise database migrations are one of the highest-risk operations in data engineering.
>
> Migrating hundreds of tables from PostgreSQL, MSSQL, and AWS Athena into Snowflake means writing validation queries manually, reviewing column mappings in spreadsheets, and hoping nothing was missed.
>
> There's no natural language interface. No confidence scoring. No governed approval trail.
>
> Migration Validator changes that."

---

## Segment 2 — Architecture (0:30–1:00)

**[Show: system architecture diagram from docs/architecture/system-architecture.md]**

> "Migration Validator is a Gemini-enabled connector — a FastAPI server that exposes 24 structured tools to Google Gemini.
>
> Gemini drives the conversation. The connector handles authentication, authorization, and persistence. The validation engine connects to your source databases and Snowflake.
>
> Every action Gemini takes is permission-checked, version-controlled, and audit-logged. Gemini cannot approve its own recommendations — that requires a human."

---

## Segment 3 — Live Gemini Interaction (1:00–2:30)

**[Show: Gemini Chat tab in web UI or terminal with Gemini API]**

**Type:**
> "Validate the events table migration."

**[Pause — let tool calls appear]**

> "Gemini calls discover_connections — finding our PostgreSQL source and Snowflake target.
>
> Then generate_validation_plan — 38 columns, 4 Fivetran audit columns excluded automatically.
>
> All 38 matched exactly. No AI calls needed — the schema naming was consistent.
>
> Now let me show a table with an ambiguous mapping."

**Type:**
> "Show me all column mappings that need review."

**[Pause — pending review appears]**

> "One mapping: created_ts maps to CREATED_AT with 82% confidence.
>
> The AI recommends it, but it's below the 95% auto-accept threshold — so it's waiting for a human decision."

**Type:**
> "I've confirmed it in the source DDL. Approve it."

**[Pause — approval confirmation appears]**

> "Approved. Actor recorded, version bumped, audit entry written. The AI cannot do this itself."

---

## Segment 4 — Human Review UI (2:30–3:15)

**[Switch to Review & Approve tab]**

> "The web UI gives reviewers a governed approval interface.
>
> Each pending mapping shows confidence score — green above 95%, orange 75 to 95, red below 75.
>
> Reviewers approve, modify, or reject.
>
> At the bottom, the full audit history — every action, every actor, every timestamp.
>
> This audit trail is append-only. Nothing is ever deleted or modified."

---

## Segment 5 — Rule Book and Validation (3:15–4:00)

**[Switch to terminal or CLI or show plan JSON]**

> "Behind every validation is the Rule Book.
>
> Base rules exclude Fivetran audit columns automatically — _fivetran_synced, _fivetran_deleted, and more — so they never cause false mismatches.
>
> User-defined global exclusions persist per database type. Pattern rules cover entire column families.
>
> Learned rules proposed by AI go through the same human approval flow before becoming active.
>
> The generated validation SQL is deterministic — count validation and row-level data comparison — both normalized for cross-dialect comparison. Boolean to string, timestamp to text, NULL to a sentinel value."

---

## Segment 6 — Security (4:00–4:30)

**[Run in terminal:]**

```bash
python demo_security.py
```

**[Show output scrolling]**

> "The security demo shows four scenarios in 20 seconds.
>
> Governed approval — a REVIEWER approves a mapping, version bumps, audit record written.
>
> Authorization denial — a VIEWER tries to activate a rule, gets a 403.
>
> Concurrent write conflict — two users try to modify the same mapping, one gets a 409 version conflict.
>
> Zero credential leakage — Gemini receives tool results, never passwords or connection strings.
>
> All 34 security tests pass."

---

## Segment 7 — ROI and Closing (4:30–5:00)

**[Show: business metrics from get_business_metrics or web UI]**

> "In our pilot, 90% of column mappings were automated. 48 manual SQL queries avoided.
>
> Gemini can explain any failure in natural language — what failed, why, and what to investigate.
>
> The connector is available now. 24 tools. 5 roles. 15 permissions. JWT or static token authentication.
>
> Migration Validator: governed enterprise migration validation through natural language.
>
> The repository, full documentation, and this demo are available in the submission."

---

## Timing Summary

| Segment | Content | Target Time |
|---------|---------|-------------|
| 1 | Problem | 0:00–0:30 |
| 2 | Architecture | 0:30–1:00 |
| 3 | Live Gemini interaction | 1:00–2:30 |
| 4 | Human review UI | 2:30–3:15 |
| 5 | Rule Book + validation SQL | 3:15–4:00 |
| 6 | Security demo | 4:00–4:30 |
| 7 | ROI + closing | 4:30–5:00 |
| **Total** | | **5:00** |

---

## Recording Tips

- Practice segment 3 (Gemini interaction) 3× before recording — it needs to feel natural
- Record segments separately if needed and edit together
- Keep the terminal font large (16pt minimum)
- Show the tool call expandable section at least once — it demonstrates function calling
- If Gemini is slow to respond, edit the pause out in post-production
- Caption the AI responses to improve readability
