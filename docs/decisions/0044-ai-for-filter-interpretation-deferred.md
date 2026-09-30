# 0044. Do we need AI to interpret the filter workbooks? Not now: deterministic first, AI later as a reviewed proposer only

**Status:** Accepted — implemented in [0045](0045-scope-filters-implemented-bronze-and-silver.md)
**Date:** 2026-09-28
**Follows on from:** [0041](0041-what-the-bronze-filter-workbooks-tell-us.md), [0042](0042-declarative-scope-filter-spec-rendered-per-layer.md)
**Related:** [0003](0003-ai-usage-yaml-generation-vs-run-validation.md) (AI only at generation, never at run)

## Context

The filter column is free text. It is sometimes SQL (Postgres sheet),
sometimes prose (SiteLink: "join on sites table with site_id then join owners
… then scorpcode filter can be used"), and sometimes a keyword ("scorpcode",
"compare full"). Future sheets will drop the hand-written Snowflake and
Postgres queries. The question is whether turning this text into filters needs
AI.

Constraint today: AI access is through a lead's account. There is no AI access
wired to the databases, and we are waiting before adding AI.

## Decision

**Build it without AI.** Leave one clearly bounded slot where AI can be added later.

Why deterministic is enough (evidence from 0041):

- 27 Postgres filters reduce to 6 shapes over **3 anchor tables** and **1
  scope list**. 28 SiteLink rows reduce to **3 shapes over 1 path**. There is
  no long tail that needs language understanding.
- The pieces a row carries can be pulled out with a few narrow patterns:
  `company_id in (…)`, `<col> >= '<date>'`, `<col> in ('…')`,
  `join <parent> … on <a>=<b>`. The join path is then **checked against the
  scope registry** (0042), not trusted.
- For SiteLink, the three phrasings map to three fixed rules:
  - "scorpcode" → direct column;
  - "compare full" → no filter;
  - "join on sites … owners" → the registry path.
- **Anything that doesn't match → "needs human" in the review grid.** It is
  never guessed. With about 55 rows, a handful of manual entries costs less
  than an AI integration.
- A deterministic parser flags the sheet's own defects (0041: the broken
  quote in `transactions`, the copy-pasted `account_balance_taxes`, the wrong
  join column in `addresses`) instead of confidently "fixing" them. AI tends to
  quietly repair a typo into *something*. For a validator, that is the wrong
  failure mode.

Where AI would add value later (not now):

- **Rows with no filter at all, or novel prose.** AI *proposes* a spec: the
  path, the date column, the predicate. It uses the table's column list and the
  registry, and a human approves it in the same review grid.
- AI output is **always the structured spec from 0042, never SQL**. Rendering
  stays deterministic. This matches the existing rule that AI is used only for
  ambiguous cases (`src/ai/rule_planner.py`) and the learned-rules
  "replay an existing template, never own SQL" guard.
- **AI never needs database access for this.** It needs the row text plus a
  column list that we already extract. So the current access limitation does
  not block the design. It only postpones the optional proposer.
- When it is added, reuse the existing `AISQLQueryGenerator` /
  `explain_and_derive_filter()` entry point and the DIAL→Claude backend
  selection. Do not add a new AI integration.

The first slice doesn't need AI anyway. A cheaper aid comes first: the
registry's known anchor hops can **auto-propose** a path for a blank row from
its column names (`facility_id` present → facilities path). A human still
approves it.

## Alternatives considered

- **AI converts each row's text straight to SQL per layer.** Rejected: it
  produces non-deterministic SQL for a validator, gives no stable spec to review,
  inherits the sheet's typos, and needs three dialects.
- **AI now, through the lead's account.** Rejected for now: it isn't needed for
  the current 55 rows, and the access situation is temporary.
- **Require the test team to write strict SQL.** Rejected: SiteLink already
  shows they won't. The prose is fine as long as it maps to a known path.

## Consequences

- Easier: works today with no AI access; the output is reproducible; sheet
  errors surface as review items.
- Harder: a new phrasing or anchor path needs a registry or pattern update, or
  a manual entry.
- Revisit when either happens: sheets arrive with many blank or novel filter
  rows that the column-based auto-proposal can't cover, or the direct Claude key is issued.
