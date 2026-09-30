# 0050. `webapp/app.py` split into one module per tab; Silver results clear themselves

**Status:** Accepted — implemented
**Date:** 2026-09-29

## Context

- `webapp/app.py` had grown to about 5,300 lines in one file: the theme,
  about 40 helpers, the sidebar and ten tabs. It was hard to find anything in
  it, and every agent or skill that touched the UI had to read the whole
  file.
- In the Silver flow, **Fetch nodes and build plans** *merged* new plans into
  the earlier ones, and the page showed every node ever fetched. After
  switching to another node, the previous node's plan, key pick and filter
  editor were still there, and it looked like they applied to the new node.

## Decision

### Split

| File | What's in it |
|---|---|
| `webapp/app.py` (≈100 lines) | Page config, then on every rerun: theme, pending toast, sidebar, header, `st.tabs(...)`, then `with tab_x: <view>.render()` per tab. |
| `webapp/ui_common.py` | Imports, paths, `.env`, constants and every shared helper: DB-discovery caches, `render_mapping_review`, `render_custom_sql_section`, the scope-filter editor, `_build_row_hash_sql`, and so on. `__all__` exports private names too, because the views use `from ui_common import *`. |
| `webapp/ui_theme.py` | The CSS block, as `apply()`. |
| `webapp/sidebar.py` | `render()` (environment status) and `render_scheduler()`. |
| `webapp/views/<tab>.py` | One `render()` per tab: `generate_yamls` (Bronze + Silver), `custom_sql`, `run_validation`, `history`, `rule_book`, `exclusions`, `jira`, `usage`, `guide`, `output_files`. |

**How the split was done.** Each tab body was moved **byte for byte**: its
`with tab_x:` line became `def render():`, and the body was already indented
4 spaces. That keeps the HTML inside multi-line `st.markdown` strings
exactly as it was. Only two lines changed, both in `output_files`:

- `pathlib.Path(__file__).parent.parent` became `_ROOT_DIR`, because
  `__file__` now points to `views/`.
- An inner `import os` was removed. Inside a function it would have made `os`
  a local name and broken its earlier use.

**Rule this relies on.** Streamlit re-runs `app.py` on every interaction,
but it imports other modules only once. So anything that must *render*
every time is a function called from `app.py` (theme, toast, sidebar,
views). `ui_common` holds only definitions and one-time setup.

**Checks run:**

- Every file compiles.
- A static check that every name each view uses resolves to a local, a
  builtin, or a `ui_common` export.
- Before and after, the non-blank lines are the same except for the two
  changes above.
- A headless `AppTest` run: all tabs render with no exceptions; the Bronze
  step-6 workbook filter works against live local Postgres; a real pasted
  Silver node builds a plan.
- The 174 tests pass.

### Silver auto-clear

`views/generate_yamls.py`:

- **Fetch** now starts from scratch. It drops all earlier plans and every
  per-node widget state (key pick, filter editor, AI result). The workbook
  choice and workspace ID are kept.
- Plans are shown only for nodes still in the rows. A changed or removed row
  hides its old plan ("fetch again to rebuild"), and a row with no plan yet
  says so.
- **🧹 Clear all nodes** resets the rows and the plans.

## Alternatives considered

- **Streamlit multipage (`pages/`).** Rejected: it would change the
  navigation users know (tabs become separate pages) and would split
  `session_state` flows that go across tabs.
- **Rewriting the tabs into smaller functions at the same time.** Rejected
  for now: a verbatim move can be checked mechanically; a rewrite can't.
  `views/generate_yamls.py` (≈1,400 lines, Bronze + Silver) is the next
  candidate to split into `bronze.py` / `silver.py`.

## Consequences

- UI work now reads one view file, not 5,300 lines.
- Line-number references in skills and docs (for example
  `webapp/app.py:2408-2681`) are replaced by module names.
- `streamlit run webapp/app.py` is unchanged; `.claude/launch.json` still
  works.
