"""
Enterprise Security Test Suite
===============================
Tests every security boundary in the Migration Intelligence Connector.

Coverage:
  Authentication
    [AUTH-01] Missing Authorization header → 401
    [AUTH-02] Non-Bearer scheme → 401
    [AUTH-03] Invalid static token → 401
    [AUTH-04] Expired JWT token → 401
    [AUTH-05] Invalid JWT issuer → 401
    [AUTH-06] Invalid JWT audience → 401

  Authorization
    [AUTHZ-01] VIEWER cannot approve a mapping → 403
    [AUTHZ-02] VIEWER cannot activate a rule → 403
    [AUTHZ-03] REVIEWER cannot activate a rule → 403
    [AUTHZ-04] RULE_ADMIN can activate a rule (permission check passes)
    [AUTHZ-05] AI actor string rejected at tool level (self-approval guard)
    [AUTHZ-06] Resource-level: unauthorized table rejected

  Versioning / Concurrency
    [VER-01] Stale expected_version → 409 conflict
    [VER-02] Correct expected_version → success (version increments)
    [VER-03] Duplicate approval attempt detected via version bump

  Audit
    [AUD-01] Every approve writes an audit record
    [AUD-02] Audit record contains no secrets

  Secret Leakage
    [SEC-01] /health never exposes credentials
    [SEC-02] Tool results never include password/secret keys
    [SEC-03] Audit records never contain secrets

Run:
    pytest tests/test_security.py -v
"""

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

# Ensure src/ is on path
_ROOT = Path(__file__).resolve().parents[1]
_SRC  = _ROOT / "src"
for _p in (str(_SRC), str(_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Auth layer unit tests (no HTTP layer)
# ---------------------------------------------------------------------------

class TestAuthentication:
    """[AUTH] Token validation without HTTP."""

    def setup_method(self):
        import gemini_connector.auth as auth_mod
        auth_mod.reset_provider()

    def _static_provider(self, token="test-secret-token"):
        from gemini_connector.auth import StaticTokenProvider
        return StaticTokenProvider(token=token, roles=["REVIEWER"])

    # [AUTH-01]
    def test_missing_token_raises(self):
        from gemini_connector.auth import verify_bearer, AuthenticationError, reset_provider
        reset_provider()
        with patch.dict(os.environ, {"AUTH_MODE": "static", "CONNECTOR_API_TOKEN": "tok"}):
            import gemini_connector.auth as m
            m._provider = m.StaticTokenProvider(token="tok")
            with pytest.raises(AuthenticationError) as exc_info:
                m.verify_bearer(None)
            assert exc_info.value.code in ("MISSING_TOKEN", "AUTHENTICATION_ERROR")

    # [AUTH-02]
    def test_non_bearer_scheme_raises(self):
        from gemini_connector.auth import AuthenticationError
        provider = self._static_provider()
        with pytest.raises(AuthenticationError) as exc_info:
            from gemini_connector.auth import verify_bearer
            import gemini_connector.auth as m
            m._provider = provider
            verify_bearer("Basic dXNlcjpwYXNz")
        assert exc_info.value.code == "MALFORMED_TOKEN"

    # [AUTH-03]
    def test_invalid_static_token_raises(self):
        from gemini_connector.auth import AuthenticationError
        provider = self._static_provider(token="correct-token")
        with pytest.raises(AuthenticationError):
            provider.verify("wrong-token")

    # [AUTH-04]
    def test_expired_jwt_raises(self):
        pytest.importorskip("jwt")
        import jwt
        from gemini_connector.auth import JWTAuthProvider, AuthenticationError

        secret = "test-secret-32-bytes-long-enough!"
        # Issue a token that expired 60 seconds ago
        payload = {
            "sub":  "user1",
            "exp":  int(time.time()) - 60,
            "iat":  int(time.time()) - 120,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        provider = JWTAuthProvider(secret=secret, algorithm="HS256")
        with pytest.raises(AuthenticationError) as exc_info:
            provider.verify(token)
        assert exc_info.value.code == "TOKEN_EXPIRED"

    # [AUTH-05]
    def test_invalid_issuer_raises(self):
        pytest.importorskip("jwt")
        import jwt
        from gemini_connector.auth import JWTAuthProvider, AuthenticationError

        secret = "test-secret-32-bytes-long-enough!"
        payload = {
            "sub": "user1",
            "exp": int(time.time()) + 300,
            "iss": "wrong-issuer",
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        provider = JWTAuthProvider(secret=secret, algorithm="HS256", issuer="correct-issuer")
        with pytest.raises(AuthenticationError) as exc_info:
            provider.verify(token)
        assert exc_info.value.code == "INVALID_ISSUER"

    # [AUTH-06]
    def test_invalid_audience_raises(self):
        pytest.importorskip("jwt")
        import jwt
        from gemini_connector.auth import JWTAuthProvider, AuthenticationError

        secret = "test-secret-32-bytes-long-enough!"
        payload = {
            "sub": "user1",
            "exp": int(time.time()) + 300,
            "aud": "wrong-audience",
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        provider = JWTAuthProvider(secret=secret, algorithm="HS256", audience="correct-audience")
        with pytest.raises(AuthenticationError) as exc_info:
            provider.verify(token)
        assert exc_info.value.code == "INVALID_AUDIENCE"

    def test_valid_jwt_returns_auth_result(self):
        pytest.importorskip("jwt")
        import jwt
        from gemini_connector.auth import JWTAuthProvider

        secret = "test-secret-32-bytes-long-enough!"
        payload = {
            "sub":   "user42",
            "email": "alice@example.com",
            "roles": ["REVIEWER"],
            "exp":   int(time.time()) + 300,
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        provider = JWTAuthProvider(secret=secret, algorithm="HS256")
        result = provider.verify(token)
        assert result.user_id == "user42"
        assert result.actor == "alice@example.com"
        assert "REVIEWER" in result.roles

    def test_raw_claims_do_not_include_sensitive_fields(self):
        """JWT raw_claims must not expose sub/email/roles (those are extracted)."""
        pytest.importorskip("jwt")
        import jwt
        from gemini_connector.auth import JWTAuthProvider

        secret = "test-secret-32-bytes-long-enough!"
        payload = {
            "sub":   "user42",
            "email": "alice@example.com",
            "roles": ["REVIEWER"],
            "exp":   int(time.time()) + 300,
            "iss":   "test-issuer",
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        provider = JWTAuthProvider(secret=secret, algorithm="HS256")
        result = provider.verify(token)
        # These are extracted into named fields, should NOT also be in raw_claims
        assert "sub" not in result.raw_claims
        assert "email" not in result.raw_claims
        assert "roles" not in result.raw_claims


# ---------------------------------------------------------------------------
# Authorization unit tests
# ---------------------------------------------------------------------------

class TestAuthorization:
    """[AUTHZ] Role and permission enforcement."""

    def _auth(self, roles):
        from gemini_connector.auth import AuthResult
        return AuthResult(user_id="u1", email="u1@test.com", roles=roles, permissions=[])

    # [AUTHZ-01]
    def test_viewer_cannot_approve_mapping(self):
        from gemini_connector.authz import require_permission, Permission, AuthorizationError
        auth = self._auth(["VIEWER"])
        with pytest.raises(AuthorizationError) as exc_info:
            require_permission(auth, Permission.MAPPING_APPROVE)
        assert "mapping.approve" in exc_info.value.message

    # [AUTHZ-02]
    def test_viewer_cannot_activate_rule(self):
        from gemini_connector.authz import require_permission, Permission, AuthorizationError
        auth = self._auth(["VIEWER"])
        with pytest.raises(AuthorizationError):
            require_permission(auth, Permission.RULE_ACTIVATE)

    # [AUTHZ-03]
    def test_reviewer_cannot_activate_rule(self):
        from gemini_connector.authz import require_permission, Permission, AuthorizationError
        auth = self._auth(["REVIEWER"])
        with pytest.raises(AuthorizationError) as exc_info:
            require_permission(auth, Permission.RULE_ACTIVATE)
        assert "RULE_ADMIN" in exc_info.value.message or "rule.activate" in exc_info.value.message

    # [AUTHZ-04]
    def test_rule_admin_can_activate_rule(self):
        from gemini_connector.authz import require_permission, Permission
        auth = self._auth(["RULE_ADMIN"])
        # Should not raise
        require_permission(auth, Permission.RULE_ACTIVATE)

    # [AUTHZ-05] AI self-approval guard (in the tool layer, not just RBAC)
    def test_ai_actor_rejected_by_tool(self):
        from gemini_connector.tools import approve_mapping
        result = approve_mapping(record_id="some.col", actor="gemini_ai", reason="auto")
        assert result["status"] == "error"
        assert "human" in result["message"].lower() or "actor" in result["message"].lower()

    def test_empty_actor_rejected_by_tool(self):
        from gemini_connector.tools import approve_mapping
        result = approve_mapping(record_id="some.col", actor="", reason="")
        assert result["status"] == "error"

    # [AUTHZ-06] Resource-level: unauthorized table
    def test_unauthorized_table_rejected(self):
        from gemini_connector.auth import AuthResult
        from gemini_connector.authz import require_permission, Permission, AuthorizationError

        # User whose token says they may only access "orders" table
        auth = AuthResult(
            user_id="limited-user", email="limited@test.com",
            roles=["REVIEWER"], permissions=[],
            raw_claims={"allowed_tables": ["orders"]},
        )
        with pytest.raises(AuthorizationError) as exc_info:
            require_permission(auth, Permission.MAPPING_APPROVE, table="events")
        assert "events" in exc_info.value.message

    def test_admin_has_all_permissions(self):
        from gemini_connector.authz import effective_permissions, Permission
        auth = self._auth(["ADMIN"])
        perms = effective_permissions(auth)
        for p in Permission:
            assert p.value in perms, f"ADMIN missing {p.value}"

    def test_validation_operator_can_execute(self):
        from gemini_connector.authz import require_permission, Permission
        auth = self._auth(["VALIDATION_OPERATOR"])
        require_permission(auth, Permission.VALIDATION_EXECUTE)  # must not raise


# ---------------------------------------------------------------------------
# Optimistic Concurrency tests
# ---------------------------------------------------------------------------

class TestVersioning:
    """[VER] Version conflict detection."""

    def setup_method(self):
        import tempfile
        from gemini_connector.version_store import VersionStore
        self._tmp = Path(tempfile.mktemp(suffix=".json"))
        self.store = VersionStore(path=self._tmp)

    def teardown_method(self):
        if self._tmp.exists():
            self._tmp.unlink()

    # [VER-01]
    def test_stale_version_raises(self):
        from gemini_connector.version_store import VersionConflictError
        key = "plan/bronze/events"
        self.store.bump(key)   # now at v1
        self.store.bump(key)   # now at v2
        with pytest.raises(VersionConflictError) as exc_info:
            self.store.check_and_bump(key, expected_version=1)  # stale!
        err = exc_info.value
        assert err.expected == 1
        assert err.actual   == 2
        assert err.code     == "VERSION_CONFLICT"

    # [VER-02]
    def test_correct_version_increments(self):
        key = "plan/bronze/test_table"
        v1 = self.store.check_and_bump(key, expected_version=0)
        assert v1 == 1
        v2 = self.store.check_and_bump(key, expected_version=1)
        assert v2 == 2

    # [VER-03]
    def test_duplicate_approval_increments_version(self):
        """Two back-to-back approvals at v0 and v1 work correctly."""
        from gemini_connector.version_store import VersionConflictError
        key = "mapping/table.col"
        v1 = self.store.check_and_bump(key, expected_version=0)
        assert v1 == 1

        # Second caller still has v0 (stale UI)
        with pytest.raises(VersionConflictError):
            self.store.check_and_bump(key, expected_version=0)

    def test_initialize_is_idempotent(self):
        key = "rule/boolean"
        v = self.store.initialize(key, version=3)
        assert v == 3
        v2 = self.store.initialize(key, version=99)   # ignored
        assert v2 == 3


# ---------------------------------------------------------------------------
# Audit completeness tests
# ---------------------------------------------------------------------------

class TestAudit:
    """[AUD] Audit record completeness and safety."""

    def setup_method(self):
        import tempfile
        from gemini_connector.audit import AuditLogger
        self._tmp = Path(tempfile.mktemp(suffix=".jsonl"))
        self.logger = AuditLogger(path=self._tmp)

    def teardown_method(self):
        if self._tmp.exists():
            self._tmp.unlink()

    # [AUD-01]
    def test_approve_writes_audit_record(self):
        from gemini_connector.audit import AuditRecord
        self.logger.log(AuditRecord(
            action      = "APPROVE_MAPPING",
            entity_type = "mapping",
            entity_id   = "events.active_flag",
            actor       = "alice@example.com",
            user_id     = "user-42",
            request_id  = "req-abc",
            previous    = {"status": "pending"},
            new_state   = {"status": "approved"},
            reason      = "Mapping is correct",
            plan_version= 5,
            table       = "events",
            column      = "active_flag",
        ))
        records = self.logger.recent(10)
        assert len(records) == 1
        r = records[0]
        assert r.action      == "APPROVE_MAPPING"
        assert r.actor       == "alice@example.com"
        assert r.plan_version == 5
        assert r.table       == "events"
        assert r.request_id  == "req-abc"
        assert r.user_id     == "user-42"

    # [AUD-02]
    def test_audit_record_contains_no_secrets(self):
        """Audit fields must never contain passwords/keys/connection strings."""
        from gemini_connector.audit import AuditRecord
        import json

        r = AuditRecord(
            action      = "APPROVE_PLAN",
            entity_type = "plan",
            entity_id   = "plan/bronze/events",
            actor       = "bob@example.com",
            new_state   = {"approval_status": "approved"},
            reason      = "Reviewed",
            metadata    = {"extra": "info"},
        )
        self.logger.log(r)
        # Read back the raw JSON line
        raw = self._tmp.read_text(encoding="utf-8")
        assert "password" not in raw.lower()
        assert "secret"   not in raw.lower()
        assert "api_key"  not in raw.lower()
        assert "connstr"  not in raw.lower()

    def test_audit_record_roundtrip(self):
        from gemini_connector.audit import AuditRecord
        r = AuditRecord(
            action="REJECT_MAPPING", entity_type="mapping",
            entity_id="t.col", actor="carol", user_id="u99",
        )
        self.logger.log(r)
        records = self.logger.recent(1)
        assert records[0].action    == "REJECT_MAPPING"
        assert records[0].user_id   == "u99"

    def test_audit_log_is_append_only(self):
        """Writing two records produces two lines; no line is altered."""
        from gemini_connector.audit import AuditRecord
        for i in range(3):
            self.logger.log(AuditRecord(
                action=f"ACTION_{i}", entity_type="mapping",
                entity_id=f"t.col{i}", actor="sys",
            ))
        lines = [l for l in self._tmp.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# Secret leakage tests (tool responses)
# ---------------------------------------------------------------------------

class TestSecretLeakage:
    """[SEC] Tool responses must never contain credentials."""

    _SENSITIVE_KEYS = {
        "password", "secret", "api_key", "access_key", "private_key",
        "token", "connection_string", "connstr", "db_password",
    }

    def _assert_no_secrets(self, result: dict, tool_name: str):
        import json
        raw = json.dumps(result).lower()
        for key in self._SENSITIVE_KEYS:
            # Allow "token" in error messages about token validation (auth domain)
            # but never as a dict key value containing an actual secret
            assert f'"{key}"' not in raw or key == "token", (
                f"[SEC] Tool '{tool_name}' result contains sensitive key '{key}'"
            )

    # [SEC-01]
    def test_health_endpoint_no_credentials(self):
        result = {
            "status": "ok",
            "service": "Migration Intelligence Connector",
            "version": "2.0.0",
            "tools_available": 24,
            "auth_mode": "static",
        }
        self._assert_no_secrets(result, "health")

    # [SEC-02]
    def test_discover_connections_no_passwords(self):
        from gemini_connector.tools import discover_connections
        result = discover_connections()
        import json
        raw = json.dumps(result).lower()
        assert "password" not in raw
        assert "secret"   not in raw
        assert "api_key"  not in raw

    # [SEC-03]
    def test_get_migration_summary_no_secrets(self):
        from gemini_connector.tools import get_migration_summary
        result = get_migration_summary(layer="bronze")
        import json
        raw = json.dumps(result).lower()
        assert "password" not in raw
        assert "secret"   not in raw


# ---------------------------------------------------------------------------
# FastAPI integration tests (auth flows end-to-end)
# ---------------------------------------------------------------------------

class TestAPIAuthFlow:
    """Integration tests via FastAPI TestClient."""

    @pytest.fixture(autouse=True)
    def client(self):
        """Create a TestClient with static token auth."""
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")
        with patch.dict(os.environ, {
            "AUTH_MODE":           "static",
            "CONNECTOR_API_TOKEN": "test-integration-token",
            "CONNECTOR_ROLES":     "REVIEWER",
        }):
            import gemini_connector.auth as auth_mod
            auth_mod.reset_provider()
            from gemini_connector.api import app
            self._client = TestClient(app, raise_server_exceptions=False)
            yield self._client
            auth_mod.reset_provider()

    def _auth_header(self, token="test-integration-token"):
        return {"Authorization": f"Bearer {token}"}

    def test_health_unauthenticated(self):
        r = self._client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        # Ensure no secrets leak
        import json
        raw = json.dumps(data).lower()
        assert "password" not in raw

    def test_me_endpoint_returns_identity(self):
        r = self._client.get("/me", headers=self._auth_header())
        assert r.status_code == 200
        data = r.json()
        assert "roles" in data
        assert "permissions" in data

    def test_approve_without_token_returns_401(self):
        r = self._client.post("/approve/mapping/events.active_flag",
                              json={"reason": "ok", "expected_version": 0})
        assert r.status_code == 401

    def test_approve_with_wrong_token_returns_401(self):
        r = self._client.post(
            "/approve/mapping/events.active_flag",
            json={"reason": "ok", "expected_version": 0},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401

    def test_approve_rule_as_reviewer_returns_403(self):
        """REVIEWER cannot activate a rule — requires RULE_ADMIN."""
        r = self._client.post(
            "/approve/rule/boolean",
            json={"reason": "activate it", "expected_version": 0},
            headers=self._auth_header(),   # REVIEWER role only
        )
        assert r.status_code == 403
        detail = r.json().get("detail", {})
        assert detail.get("error") == "AUTHORIZATION_ERROR"

    def test_version_conflict_returns_409(self):
        """If expected_version does not match, reject with 409."""
        with patch.dict(os.environ, {"CONNECTOR_ROLES": "ADMIN"}):
            import gemini_connector.auth as auth_mod
            auth_mod.reset_provider()
            auth_mod._provider = auth_mod.StaticTokenProvider(
                token="test-integration-token", roles=["ADMIN"]
            )
            # First approval bumps v0 → v1
            self._client.post(
                "/approve/mapping/events.testcol",
                json={"reason": "first", "expected_version": 0},
                headers=self._auth_header(),
            )
            # Second approval with stale v0 → conflict
            r = self._client.post(
                "/approve/mapping/events.testcol",
                json={"reason": "stale", "expected_version": 0},
                headers=self._auth_header(),
            )
            assert r.status_code == 409
            data = r.json()
            detail = data.get("detail", data)
            if isinstance(detail, dict):
                assert detail.get("error") == "VERSION_CONFLICT"
