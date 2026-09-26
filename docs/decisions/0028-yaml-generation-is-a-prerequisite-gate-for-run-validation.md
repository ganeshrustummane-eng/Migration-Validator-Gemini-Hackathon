# 0028. YAML generation is documented as a hard prerequisite gate for Run Validation

**Status:** Accepted
**Date:** 2026-09-26

## Context

Asked to "run a migration validation" for a table, Claude Code sessions were
routing straight to `Project/runner.py::start_validation()` / `Project/main.py`
without first checking whether a YAML config existed for that table. That
step reads an already-generated YAML from
`Project/config/<layer>/data_validation/<table>_validation.yaml` — it does
not generate one — so the run either fails outright or (worse) silently picks
up a stale YAML left over from a different table/mapping.

The two-phase order (Generate YAML → Run Validation) was already correct and
visible in one place: the webapp's own Guide tab
(`webapp/app.py::tab_guide`, the "END-TO-END WORKFLOW" step chips). But
`CLAUDE.md` and the skills only mentioned "reads already-generated YAML" as
incidental background inside the `data-comparison-report` skill — phrased as
a fact about the code, not as an instruction for how to handle a "run
validation" request. Nothing told an agent to check first. The
`migration-validator-orchestrator` agent's specialist list and dispatch order
are also scoped to multi-domain *code-change* requests, not to operational
"run this for me" requests, so it didn't cover this case either.

## Decision

State the prerequisite as an explicit, enforced rule in two places:

1. `CLAUDE.md` gets a new "Operational workflow order" section, near the top,
   that spells out the two phases and the check-before-route rule directly
   (not just as a cross-reference).
2. The `data-comparison-report` skill's description of `Project/main.py`
   is reworded from background trivia ("reads already-generated YAML
   configs") to an explicit rule with the check-first instruction and a
   pointer back to this ADR.

No code changes — this is a documentation-only fix. The runtime behavior
(YAML must pre-exist) was already correct; only the guidance that should have
stopped an agent from skipping the generation step was missing.

## Alternatives considered

- Add a runtime existence check inside `runner.py`/`main.py` that errors
  clearly when the YAML is missing. Worth doing independently as a defensive
  improvement, but it doesn't fix the actual problem: an agent skipping
  straight to "run" without generating anything first, which a runtime error
  only catches after the fact.
- Extend `migration-validator-orchestrator` with a new "operational run"
  domain that always calls the YAML-generator specialist before the run step.
  Rejected for now — the orchestrator's job is coordinating multi-domain code
  *changes*, not being the entry point for every "run validation" request; a
  documentation rule in `CLAUDE.md` (read by every agent regardless of which
  one gets invoked) covers the same case without adding orchestrator scope
  creep.

## Consequences

- Any agent/skill handling a "run/validate table X" request now has an
  explicit instruction to check `Project/config/<layer>/data_validation/`
  first, in the one file (`CLAUDE.md`) every agent reads before working.
- The architecture diagram
  (`docs/architecture/migration-validator-runtime.archify.json`) already
  showed "Create YAML" and "Run validation" as sibling flows off the UI with
  a dashed dependency edge; it didn't make the dependency read as a mandatory
  gate. Updated alongside this ADR to label that edge explicitly.
- If a future request needs the orchestrator itself to gate operational runs
  (not just document the rule), revisit the second alternative above instead
  of re-deciding this from scratch.
