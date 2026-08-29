# Demo Script — Migration Validator
## Stream 3: Connectors for Gemini Applications
**Total time: 5 minutes | Format: Screen recording with voiceover**

---

## Before You Start — Setup Checklist

Run these in two terminals before pressing record:

```bash
# Terminal 1
python start_connector.py

# Terminal 2
streamlit run webapp/app.py
```

Then in the browser:
- Open the app at `http://localhost:8501`
- Go to **🤖 Gemini Chat** tab
- Expand **🪪 Session Identity & Permissions**
- Type your email: `ganesh.mane@epam.com`
- Zoom browser to 125% for readability

---

## Segment 1 — The Problem (0:00–0:30)

**[Screen: show the title card or a blank screen with just your voice]**

### What you say:
> "Every enterprise migration project has the same invisible problem.
>
> You've moved 50 tables from PostgreSQL and MSSQL into Snowflake.
> But how do you actually *know* the data arrived correctly?
>
> The traditional answer is: write validation SQL manually, one table at a time.
> Compare row counts in a spreadsheet. Flag mismatches by hand.
> Hope the person reviewing it understands both the source schema and the target.
>
> There is no natural language interface for this.
> No confidence scoring on column mappings.
> No governed approval trail when something looks wrong.
>
> Migration Validator changes that — by making Gemini the migration engineer's co-pilot."

---

## Segment 2 — What This Is (0:30–1:00)

**[Screen: show the Streamlit app — Connections tab briefly, then switch to Gemini Chat]**

### What you say:
> "Migration Validator is a Gemini connector — a FastAPI server that exposes
> 24 structured tools to Google Gemini via function calling.
>
> It connects to your real source databases — PostgreSQL, MSSQL, AWS Athena —
> and your Snowflake target.
>
> Gemini drives the conversation in plain English.
> The connector handles authentication, role-based authorization, and audit logging.
>
> Nothing Gemini says becomes real until a human with the right role signs off on it.
> That's the governance layer — and I'll show you exactly how it works."

---

## Segment 3 — Live Gemini: Discovery (1:00–1:30)

**[Screen: Gemini Chat tab — identity panel is filled with your email, status bar shows green]**

### What you say:
> "I'm logged in as ganesh.mane@epam.com.
> In production this identity comes from a JWT token — role-checked on every request.
> In this demo I'm using static admin access so I can show all capabilities in one session.
>
> Let me start by asking Gemini what's connected."

**[Type into chat:]**
```
What source databases are connected and what tables are available?
```

**[Wait for response — tool calls appear]**

### What you say when Gemini responds:
> "Gemini called two tools automatically — `discover_connections` then `list_tables`.
> It didn't need instructions on how to do that. It figured out the sequence itself.
>
> You can see the actual tool call results in the expandable section below the response.
> This is real data from our live PostgreSQL and Snowflake connections — not a mock."

**[Click the 🔧 Tools used expander to show the raw tool results]**

---

## Segment 4 — Live Gemini: Plan Generation (1:30–2:15)

**[Screen: still in Gemini Chat]**

### What you say:
> "Now let's do the actual validation work.
> I'll ask Gemini to build a validation plan for our events table."

**[Type into chat:]**
```
Generate a validation plan for the events table in the bronze layer.
```

**[Wait for response]**

### What you say when Gemini responds:
> "Gemini called `generate_validation_plan` — it discovered 38 columns,
> automatically excluded 4 Fivetran audit columns that should never be compared,
> and matched every remaining column to its Snowflake counterpart.
>
> 38 out of 38 matched with 100% confidence — all auto-accepted.
>
> Notice the warning at the bottom: the primary key structure changed from `[id]`
> in PostgreSQL to `[ID, _FIVETRAN_START]` in Snowflake — that's an SCD2 pattern
> from Fivetran. Gemini surfaced that automatically. No one had to check manually."

---

## Segment 5 — Human Approval Flow (2:15–3:00)

**[Screen: still in Gemini Chat — this is the most important segment for the judges]**

### What you say:
> "Now I'll show the part that makes this enterprise-grade — the human-in-the-loop workflow.
>
> Not every column mapping is a clean exact match.
> When confidence drops below 95%, Gemini flags it and *waits*.
> It cannot proceed without a human decision. Let me show you."

**[Type into chat:]**
```
Show me all column mappings that need human review.
```

**[Wait for response]**

### What you say:
> "One mapping is pending: `created_ts` maps to `CREATED_AT` at 82% confidence.
> The AI thinks it's right — same business concept, different naming convention.
> But 82% is below our 95% threshold, so it's blocked.
>
> This is intentional design. High-confidence mappings auto-proceed.
> Anything uncertain requires a human to confirm.
>
> I've checked the source DDL — `created_ts` is definitely the creation timestamp.
> Let me approve it."

**[Type into chat:]**
```
I've confirmed in the source DDL that created_ts is the creation timestamp. Approve it.
```

**[Wait for response]**

### What you say:
> "Approved. Three things happened in that one action:
>
> One — the mapping status changed from PENDING to APPROVED.
> Two — the version number incremented. That's optimistic concurrency control —
>   if two engineers tried to approve the same mapping simultaneously,
>   the second one would get a 409 conflict, not a silent overwrite.
> Three — an audit record was written: my identity, the timestamp, the reason I gave.
>
> Gemini suggested the mapping. I confirmed it. That's the governance contract."

---

## Segment 6 — Review & Approve Tab (3:00–3:30)

**[Switch to ✅ Review & Approve tab]**

### What you say:
> "Engineers don't have to work through the chat if they prefer a UI.
> The Review and Approve tab gives the same governance workflow in a structured interface.
>
> At the top: metrics — how many mappings are pending, approved, modified, rejected.
> In the middle: the portfolio view — which tables are complete, which need attention.
>
> Each pending mapping shows its confidence score color-coded:
> green above 95, orange between 75 and 95, red below 75.
>
> At the bottom — the full audit history. Every action. Every actor. Every timestamp.
> This log is append-only. Nothing is ever deleted or overwritten.
> It's designed to meet compliance and audit requirements."

**[Scroll to show the audit history table]**

---

## Segment 7 — Normalisation Rules (3:30–4:00)

**[Switch to 📚 Rule Book tab briefly, then back]**

### What you say:
> "Behind every validation SQL is the Rule Book — how we normalize values before comparing them.
>
> A boolean TRUE in PostgreSQL becomes '1'. A boolean TRUE in Snowflake also becomes '1'.
> A timestamp is formatted to microsecond precision on both sides.
> A NULL becomes the text sentinel double-angle NULL so SQL can actually compare it.
>
> We just added datatype-aware normalization for JSON, JSONB, and HStore columns.
> Instead of comparing raw JSON strings — which differ by key order and whitespace —
> we flatten the document into sorted path-value pairs.
> So `{b:2, a:1}` and `{a:1, b:2}` are treated as equal, because they are equal.
>
> The value 22 and the string '22' are treated as different, because they are different.
>
> These rules are not hardcoded guesses — they are the team's explicit decisions,
> versioned in code, and visible to anyone reviewing the validation logic."

---

## Segment 8 — Security (4:00–4:30)

**[Open a terminal — run the security demo]**

```bash
python demo_security.py
```

### What you say as output scrolls:
> "The security demo runs four scenarios automatically.
>
> First: governed approval — an approved mapping writes an audit record with actor and version.
>
> Second: authorization denial — a user with VIEWER role tries to activate a rule.
>   They get a 403. The RBAC system blocked them. The audit log records the denial.
>
> Third: concurrent write conflict — two engineers try to modify the same mapping simultaneously.
>   The second request gets a 409 version conflict. No silent data corruption.
>
> Fourth: zero credential leakage — we inspect every tool response Gemini receives.
>   Connection strings, passwords, API keys — none of them appear.
>   Gemini sees data, never credentials.
>
> 34 security tests. All passing."

---

## Segment 9 — ROI and Close (4:30–5:00)

**[Switch back to Gemini Chat — type one last prompt]**

**[Type:]**
```
Show me the business metrics and automation rate for the bronze layer.
```

**[Wait for response]**

### What you say:
> "90% of column mappings automated. 48 manual SQL queries avoided.
>
> In a typical migration project with 50 tables and 40 columns each,
> that's 2,000 columns a data engineer would have had to map and write SQL for manually.
> Migration Validator handles 1,800 of them automatically.
> The remaining 200 are the genuinely ambiguous ones — the ones that deserve human attention.
>
> That's the value proposition:
> Not replacing human judgment. Directing it where it actually matters.
>
> Migration Validator. 24 tools. Enterprise governance. Natural language interface.
> Available now."

---

## Timing Summary

| Segment | Content | Time |
|---|---|---|
| 1 | The problem | 0:00–0:30 |
| 2 | What this is | 0:30–1:00 |
| 3 | Live: Discovery | 1:00–1:30 |
| 4 | Live: Plan generation | 1:30–2:15 |
| 5 | Human approval flow | 2:15–3:00 |
| 6 | Review & Approve UI | 3:00–3:30 |
| 7 | Normalization rules | 3:30–4:00 |
| 8 | Security demo | 4:00–4:30 |
| 9 | ROI + close | 4:30–5:00 |
| **Total** | | **5:00** |

---

## If Gemini Is Slow or Offline

The app has an offline fallback — it still dispatches tools and returns cached data.
Do not apologise for it. Say:

> "The connector works independently of the Gemini API — all 24 tools are callable
> directly via REST. The conversational layer is additive."

Then use the quick-action buttons instead of typing.

---

## The One Thing Judges Will Remember

When you approve the `created_ts` mapping in Segment 5, pause and say:

> "This is the line Gemini cannot cross. It proposed the mapping. I confirmed it.
> The system is designed so that boundary is enforced in code, not in policy."

That sentence captures Write-Back & Authentication, Human-in-the-Loop, and Enterprise
Governance in 15 words. Make sure you say it clearly.

---

## Bonus Segment (Optional) — Validation Execution Engine

Not part of the timed 5-minute cut above — use this if you have a longer demo slot,
a live Q&A, or a technical follow-up session. Covers `Project/main.py`'s YAML-driven
execution engine and its webapp surface, which the 5-minute script above doesn't touch.

**[Screen: 🚀 Run Validation tab]**

> "Behind Generate Single/Batch YAML is a second engine — Project/main.py — that
> actually executes those YAML plans against live source and Snowflake connections.
> This tab lets you pick exactly which generated YAML files to run, file by file,
> not just by table name."

**[Select all files, click Run validation]**

> "That's real count and data validation queries running right now against
> Postgres, MSSQL, and Snowflake. Results come back as paginated cards —
> summary first, then every row-level mismatch is available to drill into,
> without ever leaving the browser or opening a CSV by hand."

**[Expand a mismatch detail file, e.g. events]**

> "Every diff file stays local — it's never sent to any AI model. Only counts
> and pass/fail status are ever surfaced upstream."

**[Switch to 📈 History & Trends]**

> "Every run is recorded automatically — SQLite-backed, not scattered log
> folders. I can see pass-rate trends per table over time, and drill into
> any historical run."

**[Switch to ✅ Review & Approve, set layer to "All"]**

> "Review and Approve now aggregates across every medallion layer at once —
> bronze, silver, gold — instead of checking one at a time. And if a mapping
> needs follow-up beyond what a human can resolve in this session, there's a
> one-click Jira ticket, pre-filled with the table, columns, confidence, and
> AI recommendation, so nothing falls through the cracks between this tool
> and the team's existing workflow."

**Optional closer, if judges ask about testing rigor:**

> "We didn't just build these features — we verified them end-to-end, and in
> doing so found and fixed four real bugs: a count-validation PASS/FAIL check
> that was comparing the wrong columns and always failing, a case-sensitivity
> bug that silently crashed every data validation before it could write a
> result, a stale-environment-variable bug in the auth layer that only
> surfaced under test isolation, and a Windows console encoding crash in our
> own startup script. All fixed, all covered by the test suite now — 92
> passing, including 34/34 in the security suite."
