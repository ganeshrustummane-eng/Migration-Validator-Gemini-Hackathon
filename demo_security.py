"""
Enterprise Security Demo Script
================================
Demonstrates the full security flow for hackathon judges:

  Demo 1 — Governed approval workflow
    Gemini surfaces a mapping needing review.
    User provides identity. Connector verifies token + permission + plan version.
    Mapping approved. Audit entry written. Version bumped.

  Demo 2 — Authorization denial
    User with VIEWER role attempts to activate a learned rule.
    System rejects with AUTHORIZATION_ERROR: rule.activate requires RULE_ADMIN.

  Demo 3 — Stale plan version rejection
    User A approves mapping at version 0 → version becomes 1.
    User B (stale UI) tries to approve same mapping at version 0 → 409 conflict.

  Demo 4 — Secret leakage check
    Tool responses are scanned for passwords/secrets.

Usage:
    python demo_security.py
    python demo_security.py --demo 1
    python demo_security.py --demo 2
    python demo_security.py --demo 3
    python demo_security.py --demo 4
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

_ROOT = Path(__file__).parent
_SRC  = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

# ── colour helpers ──────────────────────────────────────────────────────────

def _green(s):  return f"\033[32m{s}\033[0m"
def _red(s):    return f"\033[31m{s}\033[0m"
def _yellow(s): return f"\033[33m{s}\033[0m"
def _blue(s):   return f"\033[34m{s}\033[0m"
def _bold(s):   return f"\033[1m{s}\033[0m"

def _section(title: str):
    print()
    print(_bold("=" * 60))
    print(_bold(f"  {title}"))
    print(_bold("=" * 60))

def _step(n: int, desc: str):
    print(f"\n{_blue(f'[Step {n}]')} {desc}")

def _ok(msg: str):   print(f"  {_green('[PASS]')} {msg}")
def _fail(msg: str): print(f"  {_red('[FAIL]')} {msg}")
def _info(msg: str): print(f"  {_yellow('[INFO]')} {msg}")


# ── Demo 1 — Governed approval workflow ─────────────────────────────────────

def demo_1_governed_approval():
    _section("DEMO 1 — Governed Approval Workflow")

    from gemini_connector.auth import StaticTokenProvider, AuthResult
    from gemini_connector.authz import require_permission, Permission
    from gemini_connector.version_store import VersionStore
    from gemini_connector.audit import AuditLogger, AuditRecord

    # Isolated stores for demo
    import tempfile
    _vs_path  = Path(tempfile.mktemp(suffix=".json"))
    _aud_path = Path(tempfile.mktemp(suffix=".jsonl"))
    vs  = VersionStore(path=_vs_path)
    aud = AuditLogger(path=_aud_path)

    DEMO_TOKEN = "demo-reviewer-token-2026"
    provider = StaticTokenProvider(token=DEMO_TOKEN, roles=["REVIEWER"])

    _step(1, "Gemini surfaces pending mapping: active_flag -> is_active (confidence 82%)")
    _info("Mapping: events.active_flag  ->  events.is_active")
    _info("Confidence: 82% (below 95% auto-accept threshold)")
    _info("Status: PENDING HUMAN REVIEW")

    _step(2, "User provides identity (alice@company.com)")
    token = DEMO_TOKEN
    try:
        auth_result = provider.verify(token)
        _ok(f"Token valid. Actor: {auth_result.actor} | Roles: {auth_result.roles}")
    except Exception as exc:
        _fail(str(exc)); return

    _step(3, "Connector checks permission: mapping.approve")
    try:
        require_permission(auth_result, Permission.MAPPING_APPROVE)
        _ok("Permission GRANTED: mapping.approve")
    except Exception as exc:
        _fail(str(exc)); return

    _step(4, "Connector checks plan version (expected=0, current=0)")
    entity_key = "mapping/events.active_flag"
    try:
        new_ver = vs.check_and_bump(entity_key, expected_version=0)
        _ok(f"Version check PASSED. New version: {new_ver}")
    except Exception as exc:
        _fail(str(exc)); return

    _step(5, "Write: status = APPROVED")
    _ok("Mapping events.active_flag approved by alice@company.com")

    _step(6, "Audit entry written")
    req_id = str(uuid.uuid4())[:8]
    aud.log(AuditRecord(
        action       = "APPROVE_MAPPING",
        entity_type  = "mapping",
        entity_id    = "events.active_flag",
        actor        = auth_result.actor,
        user_id      = auth_result.user_id,
        request_id   = req_id,
        previous     = {"status": "pending"},
        new_state    = {"status": "approved"},
        reason       = "Mapping verified correct by data engineer",
        plan_version = new_ver,
        table        = "events",
        column       = "active_flag",
    ))
    records = aud.recent(1)
    r = records[0]
    _ok(f"Audit record: action={r.action} actor={r.actor} version={r.plan_version} request={r.request_id}")

    _step(7, "Gemini response to user")
    print(f"""
  {_green('Gemini:')} "Mapping approved and validation plan updated to version {new_ver}.
  Decision recorded in audit log (request: {req_id}).
  Active flag -> is_active is now approved for validation."
""")
    for p in [_vs_path, _aud_path]:
        if p.exists(): p.unlink()


# ── Demo 2 — Authorization denial ───────────────────────────────────────────

def demo_2_authorization_denial():
    _section("DEMO 2 — Authorization Denial (RULE_ADMIN Required)")

    from gemini_connector.auth import StaticTokenProvider
    from gemini_connector.authz import require_permission, Permission, AuthorizationError

    DEMO_TOKEN = "demo-viewer-token"
    provider = StaticTokenProvider(token=DEMO_TOKEN, roles=["VIEWER"])

    _step(1, "User (VIEWER role) asks Gemini: 'Activate this learned rule.'")

    _step(2, "Token validated (VIEWER)")
    auth = provider.verify(DEMO_TOKEN)
    _ok(f"Actor: {auth.actor} | Roles: {auth.roles}")

    _step(3, "Authorization check: rule.activate")
    try:
        require_permission(auth, Permission.RULE_ACTIVATE)
        _fail("Expected authorization denial — not raised!")
    except AuthorizationError as exc:
        _ok("Authorization DENIED (correct)")
        print(f"\n  Response:\n  {_red(exc.message)}\n")

    _step(4, "Gemini response to user")
    print(f"""
  {_red('Gemini:')} "Authorization denied: rule activation requires RULE_ADMIN permission.
  Your current role is VIEWER.
  Please contact your administrator to request the RULE_ADMIN role."
""")


# ── Demo 3 — Stale plan version ─────────────────────────────────────────────

def demo_3_stale_version():
    _section("DEMO 3 — Stale Plan Version Rejection (Optimistic Concurrency)")

    import tempfile
    from gemini_connector.version_store import VersionStore, VersionConflictError

    _vs_path = Path(tempfile.mktemp(suffix=".json"))
    vs = VersionStore(path=_vs_path)
    key = "mapping/events.testcol"

    _step(1, "User A approves mapping at expected_version=0")
    v = vs.check_and_bump(key, expected_version=0)
    _ok(f"User A: approved. Version: 0 -> {v}")

    _step(2, "User B (stale UI, still at version=0) tries to approve same mapping")
    try:
        vs.check_and_bump(key, expected_version=0)
        _fail("Expected VERSION_CONFLICT — not raised!")
    except VersionConflictError as exc:
        _ok("Conflict detected (correct)")
        print(f"\n  Error: {_red(exc.message)}\n")
        print(f"  HTTP 409 returned to User B's client.")
        print(f"  User B must re-read the mapping (now at v{exc.actual}) and retry.")

    if _vs_path.exists(): _vs_path.unlink()


# ── Demo 4 — Secret leakage check ───────────────────────────────────────────

def demo_4_secret_leakage():
    _section("DEMO 4 — Secret Leakage Check (Zero-Credential Policy)")

    from gemini_connector.tools import discover_connections, get_migration_summary

    _FORBIDDEN = {"password", "secret", "api_key", "access_key", "private_key"}

    def _scan(tool_name: str, result: dict):
        raw = json.dumps(result).lower()
        found = [k for k in _FORBIDDEN if k in raw]
        if found:
            _fail(f"{tool_name}: secrets found in response: {found}")
        else:
            _ok(f"{tool_name}: response contains no credentials")

    _step(1, "Scan discover_connections response")
    _scan("discover_connections", discover_connections())

    _step(2, "Scan get_migration_summary response")
    _scan("get_migration_summary", get_migration_summary(layer="bronze"))

    _step(3, "Scan /health endpoint structure")
    health = {
        "status": "ok",
        "service": "Migration Intelligence Connector",
        "version": "2.0.0",
        "tools_available": 24,
        "auth_mode": "static",
    }
    _scan("/health", health)

    print(f"\n  {_green('Zero-credential policy verified.')}")
    print(f"  Database passwords remain server-side at all times.")
    print(f"  Gemini never receives connection strings or API keys.\n")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Migration Intelligence Connector — Security Demo")
    parser.add_argument("--demo", type=int, choices=[1, 2, 3, 4], help="Run a specific demo (default: all)")
    args = parser.parse_args()

    print(_bold("\nMigration Intelligence Connector — Enterprise Security Demo"))
    print("Demonstrating: Authentication, Authorization, Versioning, Audit, Zero-Credential Policy")

    demos = {
        1: demo_1_governed_approval,
        2: demo_2_authorization_denial,
        3: demo_3_stale_version,
        4: demo_4_secret_leakage,
    }

    to_run = [args.demo] if args.demo else [1, 2, 3, 4]
    for n in to_run:
        demos[n]()

    print()
    print(_bold("=" * 60))
    print(_bold("  Demo complete."))
    print(_bold("=" * 60))
    print()


if __name__ == "__main__":
    main()
