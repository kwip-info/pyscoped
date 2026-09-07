# Layer 0: Compliance Testing Engine

## Purpose

The compliance engine is the reason everything else works. It is the **enforcement guarantee** — the system that validates, at test time and at runtime, that no construct, no action, and no data flow bypasses the framework's invariants.

Without the compliance engine, the other layers are promises. With it, they're guarantees.

## Why Layer 0

It's numbered zero because it wraps everything else. It doesn't sit in the dependency chain — it sits around it. Every layer is subject to compliance checks. The compliance engine can validate its own validation.

## Core Concepts

### Static Compliance (Test Time / CI)

These checks run during the test suite or in CI pipelines. They analyze the application without running it.

#### 1. Registry Completeness

Scan the entire application — every module, every class, every function, every view, every serializer, every signal handler, every task — and verify that each has a corresponding registry entry.

If a construct exists in the application but is not registered: **test failure.**

This is the most fundamental check. It guarantees that nothing can sneak past the framework. If a developer adds a new model and forgets to register it, CI catches it.

#### 2. Isolation Integrity

Verify that no code path bypasses the `ScopedManager`:
- No direct Django `objects.all()` calls that skip isolation filtering
- No raw SQL that doesn't go through the storage backend
- No queryset operations that ignore the acting principal's scope

This is static analysis — scanning the codebase for patterns that would break isolation.

#### 3. Trace Coverage

For every code path that mutates state (create, update, delete, share, revoke, etc.), verify that a corresponding trace entry is produced. This is tested by:
- Running the operation in a test
- Checking that the audit trail contains the expected trace entry
- Checking that the trace entry has the correct actor, action, target, before/after states

#### 4. Rule Consistency

Analyze the rule set for:
- Contradictions (two rules with the same binding but opposite effects and no priority difference)
- Orphaned bindings (bindings that reference targets that no longer exist)
- Unreachable rules (rules that can never match because a higher-priority rule always wins)

#### 5. Scope Boundary Validation

Verify that no object is accessible outside its scope without an explicit projection. This is tested by:
- Creating objects in isolated scopes
- Attempting to access them from outside the scope
- Verifying that `AccessDeniedError` is raised

#### 6. Secret Hygiene

Verify that:
- No secret values appear in audit trail states
- No secret values appear in environment snapshots
- No secret values appear in connector traffic records
- No secret values appear in object version data (unless the object IS a secret)
- Secret refs are revoked when environments are discarded

### Runtime Compliance (Middleware / Manager)

These checks run during actual operation. They're enforced in middleware and managers.

#### 1. Context Enforcement

Every request or operation must have a `ScopedContext` with a valid principal. If a code path reaches a framework operation without a context: `ComplianceViolation` is raised immediately.

#### 2. Trace Completeness

After every operation, the compliance middleware checks that a trace entry was produced. If an operation completes without producing a trace: `ComplianceViolation` is raised.

This is the "if it didn't produce a trace, it didn't happen" invariant enforced mechanically.

#### 3. Revocation Immediacy

After a revocation (scope membership, secret ref, connector), the compliance middleware verifies that subsequent access attempts fail within the same transaction. If a revoked principal can still access resources: `ComplianceViolation`.

#### 4. Version Integrity

After every save operation, the compliance middleware verifies that a new version was created. If an object's data changed without a new version: `ComplianceViolation`.

#### 5. Secret Leak Detection

Before any data is written to the audit trail, snapshots, or connector traffic, the compliance middleware checks for plaintext secret values. If found: `SecretLeakDetectedError` and the write is blocked.

### Test Utilities

#### ScopedTestCase

Base test class that sets up isolated principals, scopes, and environments for each test. Provides assertion helpers:

```python
class TestMyFeature(ScopedTestCase):
    def test_object_isolation(self):
        obj = self.create_object("Document", owner=self.user_a)

        with self.as_principal(self.user_b):
            self.assert_access_denied(lambda: self.read_object(obj.id))

    def test_sharing_flow(self):
        obj = self.create_object("Document", owner=self.user_a)
        scope = self.create_scope(owner=self.user_a, members=[self.user_b])
        self.project(obj, scope)

        with self.as_principal(self.user_b):
            self.assert_can_read(obj.id)
```

#### ComplianceAuditor

Run against a Django project to produce a compliance report:

```
$ python manage.py scoped_compliance

Registry Completeness:  147/150 constructs registered (98.0%)
  MISSING: myapp.views.LegacyExportView
  MISSING: myapp.tasks.cleanup_temp_files
  MISSING: myapp.signals.on_user_delete

Isolation Integrity:    PASS (0 bypass patterns detected)
Trace Coverage:         PASS (all mutation paths produce traces)
Rule Consistency:       1 warning
  WARNING: Rule "deny-external-sharing" and "allow-partner-sharing"
           have overlapping bindings — deny wins by default
Secret Hygiene:         PASS
```

#### IsolationFuzzer

Generate random access patterns and verify no leaks:

1. Create N principals with random relationship graphs
2. Create M objects with random ownership
3. Create K scopes with random memberships and projections
4. Apply random rules
5. For every (principal, object) pair, verify that access matches what the framework says should be allowed
6. Repeat with random mutations (add/remove memberships, change rules, revoke access)

If any principal can access an object the framework says they shouldn't: **test failure.**

#### RollbackVerifier

For every mutation type in the system:

1. Capture state before mutation
2. Perform mutation
3. Perform rollback
4. Verify state matches pre-mutation state exactly

If rollback doesn't restore exact prior state: **test failure.**

## How It Connects

### To Every Layer

The compliance engine validates invariants across all layers. It is the universal auditor.

Specific connections:

- **Registry**: completeness checks
- **Identity**: context enforcement
- **Objects**: version integrity, isolation integrity
- **Tenancy**: scope boundary validation
- **Rules**: rule consistency analysis
- **Audit**: trace completeness, hash chain verification
- **Temporal**: rollback verification
- **Environments**: snapshot secret hygiene
- **Flow**: flow channel validation
- **Deployments**: gate check verification
- **Secrets**: leak detection, access log completeness
- **Integrations**: plugin sandbox verification
- **Connector**: traffic policy validation, secret exclusion

## Files

```
scoped/testing/
    __init__.py
    base.py            # ScopedTestCase, assertion helpers
    auditor.py         # ComplianceAuditor — static analysis
    fuzzer.py          # IsolationFuzzer — randomized testing
    rollback.py        # RollbackVerifier
    introspection.py   # Discover all constructs, compare against registry
    middleware.py       # Runtime compliance middleware
    reports.py         # Generate compliance reports
```

## Invariants

The compliance engine enforces all invariants from all layers. But its own invariants are:

1. Static compliance checks run in CI and block deployment on failure.
2. Runtime compliance checks are always-on in production (configurable via `runtime_compliance` setting).
3. Compliance violations are themselves traced (the framework traces its own enforcement).
4. The compliance engine can validate itself (self-referential integrity).
5. False positives are treated as bugs in the compliance engine, not exceptions to the rules.
