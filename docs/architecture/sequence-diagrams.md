# Sequence Diagrams

## 1. Full Validation Flow (Happy Path)

```mermaid
sequenceDiagram
    participant U as User
    participant G as Gemini
    participant C as Connector API
    participant V as Validator Engine
    participant SRC as Source DB
    participant SF as Snowflake

    U->>G: "Validate the customer migration"
    G->>C: POST /tools/discover_connections
    C->>SRC: Test connection
    C->>SF: Test connection
    C-->>G: {connections: [postgresql, snowflake]}

    G->>C: POST /tools/generate_validation_plan {table: "customer", layer: "bronze"}
    C->>V: ValidationPipeline.run_with_plan()
    V->>SRC: SELECT column_name, data_type FROM information_schema.columns
    V->>SF: SELECT column_name, data_type FROM information_schema.columns
    V->>V: ExclusionManager (filter Fivetran cols)
    V->>V: CandidateMatcher (exact/fuzzy/AI)
    V->>V: ConfidenceScorer
    V->>C: CanonicalValidationPlan{38 mappings, 0 pending}
    C-->>G: {status: "complete", active_mappings: 38}

    G->>C: POST /tools/execute_validation {table: "customer", actor: "jane@corp.com"}
    C->>C: require_permission(VALIDATION_EXECUTE)
    C->>C: AI actor guard check
    C->>V: Execute SQL
    V->>SRC: SELECT COUNT(*) / row comparison SQL
    V->>SF: SELECT COUNT(*) / row comparison SQL
    V->>V: Compare results
    C-->>G: {status: "PASS", coverage: 100%}

    G-->>U: "Validation complete. 38 columns validated, 100% coverage. All checks passed."
```

---

## 2. Human-in-the-Loop Approval Flow

```mermaid
sequenceDiagram
    participant U as User
    participant G as Gemini
    participant C as Connector API
    participant A as ApprovalStore
    participant VS as VersionStore
    participant AL as AuditLogger

    G->>C: POST /tools/generate_validation_plan {table: "orders"}
    C->>C: ConfidenceScorer → mapping "created_ts"→"CREATED_AT" = 0.82
    C->>A: upsert(ApprovalRecord{status: PENDING, confidence: 0.82})
    C-->>G: {active_mappings: 35, pending_review: 1}

    G-->>U: "Plan generated. 1 mapping needs your review:\n'created_ts' → 'CREATED_AT' (82% confidence)"

    U->>G: "Approve the created_ts mapping. I've confirmed it via the source DDL."
    G->>C: POST /tools/approve_mapping {record_id: "...", actor: "jane@corp.com", reason: "Confirmed via DDL", expected_version: 0}

    C->>C: require_permission(MAPPING_APPROVE)
    C->>C: AI actor guard: "jane@corp.com" is valid
    C->>VS: check_and_bump("mapping/orders/created_ts", expected=0)
    VS-->>C: new_version=1

    C->>A: approve(record_id, actor, reason)
    A-->>C: ApprovalRecord{status: APPROVED, version: 1}

    C->>AL: log(AuditRecord{action: "approve_mapping", actor: "jane@corp.com", ...})
    C-->>G: {status: "approved", new_version: 1}

    G-->>U: "Mapping approved. created_ts → CREATED_AT is now confirmed."
```

---

## 3. Authentication and Authorization Check

```mermaid
sequenceDiagram
    participant G as Gemini
    participant C as Connector API
    participant Auth as AuthProvider
    participant Authz as AuthzChecker

    G->>C: POST /tools/approve_mapping\nAuthorization: Bearer <token>

    C->>Auth: verify_bearer("Bearer <token>")
    alt JWT mode
        Auth->>Auth: decode JWT, validate sig/exp/iss/aud
        Auth-->>C: AuthResult{email, roles: ["REVIEWER"]}
    else Static mode
        Auth->>Auth: compare to CONNECTOR_API_TOKEN
        Auth-->>C: AuthResult{roles: ["ADMIN"]}
    else Invalid token
        Auth-->>C: AuthenticationError{INVALID_SIGNATURE}
        C-->>G: HTTP 401 {error: "AUTHENTICATION_ERROR"}
    end

    C->>Authz: require_permission(auth, MAPPING_APPROVE, source_system=..., table=...)
    alt Role has permission
        Authz->>Authz: check ROLE_PERMISSIONS[REVIEWER]
        Authz->>Authz: check AUTHZ_SOURCE_ALLOWLIST
        Authz->>Authz: check allowed_tables claim
        Authz-->>C: OK
    else Role lacks permission
        Authz-->>C: AuthorizationError{REVIEWER cannot activate rule}
        C-->>G: HTTP 403 {error: "AUTHORIZATION_ERROR"}
    end

    C->>C: AI actor guard check
    C->>C: Process tool call
```

---

## 4. Optimistic Concurrency Conflict

```mermaid
sequenceDiagram
    participant A as User A
    participant B as User B
    participant C as Connector
    participant VS as VersionStore

    A->>C: GET /table/customer → sees version=2
    B->>C: GET /table/customer → sees version=2

    A->>C: POST /approve/mapping/xyz {expected_version: 2}
    C->>VS: check_and_bump("mapping/customer/xyz", expected=2)
    VS-->>C: current=2, bumped to 3 ✓
    C-->>A: HTTP 200 {new_version: 3}

    B->>C: POST /approve/mapping/xyz {expected_version: 2}
    C->>VS: check_and_bump("mapping/customer/xyz", expected=2)
    VS-->>C: current=3 ≠ expected=2 → VersionConflictError
    C-->>B: HTTP 409 {error: "VERSION_CONFLICT", current: 3, expected: 2}
    Note over B: B must re-read and re-submit with expected_version=3
```

---

## 5. Rule Book Lifecycle

```mermaid
sequenceDiagram
    participant G as Gemini
    participant C as Connector
    participant RB as RuleBook
    participant RA as Rule Admin

    G->>C: POST /tools/get_applicable_rules {table: "events"}
    C->>RB: get_rules_for_table("events")
    RB-->>C: [{rule_id: "R1", status: "active", ...}]
    C-->>G: rules list

    Note over RA: Rule Admin identifies a new normalization pattern

    RA->>C: POST /tools (via CLI cmd_add_rule)
    C->>RB: save_draft_rule({type: "normalization", pattern: "...", status: "draft"})
    RB-->>C: {rule_id: "R42", status: "draft"}

    RA->>C: POST /tools/approve_rule {rule_id: "R42", actor: "admin@corp.com", expected_version: 0}
    C->>C: require_permission(RULE_ACTIVATE)
    C->>RB: activate_rule("R42")
    RB-->>C: {rule_id: "R42", status: "active", version: 1}
    C-->>RA: Rule activated
```

---

## 6. Multi-Source Coverage Query

```mermaid
sequenceDiagram
    participant U as User
    participant G as Gemini
    participant C as Connector
    participant PS as PlanStore

    U->>G: "Which tables are below 95% coverage?"

    G->>C: POST /tools/get_coverage {layer: "bronze", threshold: 0.95, page: 1}
    C->>PS: list_plans("bronze")
    loop For each plan
        PS->>PS: load plan, compute coverage_pct from exclusion_summary()
    end
    C-->>G: {items: [{table: "orders", coverage: 87.5}, {table: "payments", coverage: 91.2}], total: 2}

    G-->>U: "2 tables are below 95% coverage in the Bronze layer:\n• orders: 87.5% (6 unmatched columns)\n• payments: 91.2% (3 unmatched columns)\n\nRecommend reviewing the pending mappings for these tables."
```
