# Submission Checklist

**Project:** Migration Validator  
**Stream:** Stream 3 — Connectors for Gemini Applications

Complete each item before final submission. Check the box when done.

---

## Team Certification (20% of score)

- [ ] `JUDGING_RUBRIC.md` "Criterion 0" table is filled in with real team member names and roles
- [ ] Every team member's Gemini Enterprise certification status is confirmed (not assumed)
- [ ] A certificate or completion-page screenshot is linked as evidence for each certified member
- [ ] Certification rate (e.g. "4 of 5 = 80%") is calculated and stated in the rubric

---

## Repository & Code

- [ ] `README.md` is complete and passes all 15 required questions (see README sections 1–16)
- [ ] No TODOs left in production code that would confuse a reviewer
- [ ] No `print("debug")` or leftover debug statements in connector code
- [ ] All imports in `requirements.txt` are accurate and installable
- [ ] `start_connector.py` starts without errors on a clean install
- [ ] `streamlit run webapp/app.py` starts without errors
- [ ] `python -m src.validate_cli` starts without errors
- [ ] `python demo_security.py` runs all 4 demos and prints PASS

---

## Gemini Integration

- [ ] `GET /tools` returns all 24 tool declarations in valid JSON
- [ ] `POST /tools/get_migration_summary` returns valid JSON with `arguments: {"layer": "bronze"}`
- [ ] `POST /chat` returns a response with `text` and `tool_calls` fields
- [ ] Gemini function calling loop completes without error (max 10 rounds)
- [ ] Offline fallback (`chat_offline`) works when `GOOGLE_API_KEY` is absent
- [ ] `GEMINI_MODEL=gemini-2.5-flash` is the correct default model ID

---

## Authentication

- [ ] `AUTH_MODE=static` works with `CONNECTOR_API_TOKEN` set
- [ ] Request without `Authorization` header returns HTTP 401
- [ ] Request with wrong token returns HTTP 401
- [ ] `AUTH_MODE=jwt` validates JWT correctly (test with a generated token)
- [ ] `AUTH_MODE=dev` grants ADMIN access (for local use only)

---

## Authorization

- [ ] VIEWER role cannot call `approve_mapping` (returns HTTP 403)
- [ ] REVIEWER role can call `approve_mapping`
- [ ] VIEWER role cannot call `approve_rule` (returns HTTP 403)
- [ ] RULE_ADMIN role can call `approve_rule`
- [ ] Actor string `"gemini_ai"` is rejected by all write tools

---

## Write-Back

- [ ] `approve_mapping` creates an `AuditRecord` in `output/audit_log.jsonl`
- [ ] `reject_mapping` creates an `AuditRecord` in `output/audit_log.jsonl`
- [ ] `approve_mapping` with wrong `expected_version` returns HTTP 409
- [ ] `approve_plan` requires `PLAN_APPROVE` permission
- [ ] `execute_validation` requires `VALIDATION_EXECUTE` permission

---

## Human Review

- [ ] `ApprovalStore.pending()` returns records with `status == "PENDING"`
- [ ] `approve_mapping` changes status from `PENDING` to `APPROVED`
- [ ] `reject_mapping` changes status from `PENDING` to `REJECTED`
- [ ] `modify_mapping` changes status to `MODIFIED` and records `modified_target`
- [ ] Streamlit Review & Approve tab shows pending records
- [ ] Streamlit audit history table shows recent actions

---

## Audit Trail

- [ ] `output/audit_log.jsonl` exists after any write action
- [ ] Each line is valid JSON with all required fields (audit_id, action, actor, timestamp)
- [ ] No passwords or API keys appear in audit records
- [ ] `audit_logger.recent(30)` returns at most 30 records
- [ ] Running `demo_security.py` Demo 2 produces an audit entry for a VIEWER denial

---

## Security

- [ ] `demo_security.py` runs all 4 demos successfully
- [ ] `pytest tests/test_security.py -v` shows 34/34 passing
- [ ] `GET /health` response contains no credentials
- [ ] `POST /tools/discover_connections` response contains no passwords
- [ ] `POST /tools/get_migration_summary` response contains no secrets

---

## Validation Engine

- [ ] `ValidationPipeline` generates a plan for at least one real table
- [ ] `CanonicalValidationPlan` serializes and deserializes losslessly
- [ ] Generated count validation SQL is syntactically correct
- [ ] Generated data validation SQL is syntactically correct
- [ ] Fivetran columns (`_fivetran_synced` etc.) are excluded from all plans

---

## Documentation

- [ ] All links in `README.md` resolve to existing files
- [ ] All links in `JUDGING_RUBRIC.md` resolve to existing files
- [ ] No broken links anywhere in `docs/`
- [ ] Architecture diagrams (ASCII + Mermaid) render correctly on GitHub
- [ ] Sequence diagrams render correctly in a Mermaid viewer
- [ ] `docs/hackathon/video-script.md` is complete and timed to < 5 minutes
- [ ] `docs/hackathon/presentation-outline.md` has 8+ slides with content
- [ ] `JUDGING_RUBRIC.md` maps each rubric criterion to specific files and line numbers

---

## Security of Submission Package

- [ ] `.env` is in `.gitignore` and NOT committed
- [ ] No API keys in any committed file
- [ ] No database passwords in any committed file
- [ ] No credentials in screenshots or video
- [ ] `output/audit_log.jsonl` (if committed) contains no real personal data
- [ ] `output/approval_store.jsonl` (if committed) contains no real personal data

---

## Video

- [ ] Video is recorded and exported
- [ ] Video duration is ≤ 5 minutes
- [ ] Video follows the script in `docs/hackathon/video-script.md`
- [ ] All terminal text is readable at normal playback size
- [ ] No credentials visible on screen
- [ ] Video is uploaded to the submission platform

---

## GCP Deployment (if presenting a live deployed instance)

- [x] `docker build .` succeeds — verified via `gcloud builds submit` (Cloud Build), 2026-08-29
- [x] Connector image pushed to Artifact Registry and deployed to Cloud Run
      (`migration-connector`, region `us-central1`,
      `https://migration-connector-877936790636.us-central1.run.app`)
- [x] `curl <cloud-run-url>/health` and `/tools` return valid responses — verified HTTP 200 on
      both, with a Cloud Run identity token (see
      [`docs/architecture/gemini-integration.md`](docs/architecture/gemini-integration.md))
- [x] Secrets (`GOOGLE_API_KEY`, `CONNECTOR_API_TOKEN`, DB/Snowflake passwords) are in Secret
      Manager, not passed as plain `--set-env-vars`
- [ ] `--min-instances 1` is set on the connector before the judging/demo session to avoid
      cold-start latency — **needs verification**, run:
      `gcloud run services describe migration-connector --region=us-central1 --format='value(spec.template.metadata.annotations["autoscaling.knative.dev/minScale"])'`
- [x] The Streamlit review UI (`migration-webapp`) is **not** publicly invokable — org policy
      blocks `--allow-unauthenticated`; both services require a Cloud Run IAM identity token
- [x] Deployed connector URL is registered with Gemini Enterprise — organizer confirmed
      "agent has been added and shared with CloudGCPP-CCOEGEHmigrat-1646@epam.com" via
      `agent.json` (see [`docs/hackathon/agent.json`](docs/hackathon/agent.json)).
      **Still to verify:** a live tool call actually succeeds end-to-end through
      epa.ms/gemini-enterprise (not just against `localhost`) — confirm via the connector's
      own `/audit` endpoint after asking Gemini Enterprise a question that should trigger a tool.

---

## Final Check

- [ ] All commands in `docs/deployment/local-setup.md` have been executed successfully on a clean machine (or verified step-by-step)
- [ ] The demo scenario in `docs/hackathon/demo-script.md` has been rehearsed at least twice
- [ ] `JUDGING_RUBRIC.md` has been reviewed against the actual rubric criteria
- [ ] Repository name, description, and topics are set correctly on the hosting platform
- [ ] Submission form is complete (team members, project name, stream, repository URL, video URL)

---

## Sign-Off

| Item | Owner | Done |
|------|-------|------|
| Code review | | ☐ |
| Documentation review | | ☐ |
| Security review | | ☐ |
| Demo rehearsal | | ☐ |
| Video recording | | ☐ |
| Final submission | | ☐ |
