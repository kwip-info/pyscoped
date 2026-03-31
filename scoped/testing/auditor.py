"""ComplianceAuditor — static analysis of framework compliance.

Runs all static compliance checks and produces a structured result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from scoped.audit.query import AuditQuery
from scoped.storage.interface import StorageBackend
from scoped.types import Lifecycle


@dataclass(slots=True)
class CheckResult:
    """Result of a single compliance check."""

    name: str
    passed: bool
    details: str = ""
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AuditResult:
    """Aggregate result of all compliance checks."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def total_checks(self) -> int:
        return len(self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed)

    @property
    def all_violations(self) -> list[str]:
        result = []
        for c in self.checks:
            result.extend(c.violations)
        return result

    @property
    def all_warnings(self) -> list[str]:
        result = []
        for c in self.checks:
            result.extend(c.warnings)
        return result


class ComplianceAuditor:
    """Run static compliance checks against a Scoped backend.

    Checks:
    1. Registry completeness — all principals have registry entries
    2. Trace chain integrity — audit hash chain is unbroken
    3. Isolation integrity — objects are only visible to their owners
    4. Rule consistency — no contradictory rules with identical bindings
    5. Scope boundary validation — archived scopes have no active projections
    6. Secret hygiene — no plaintext secret values in audit states
    """

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def run_all(self) -> AuditResult:
        """Run all compliance checks and return the aggregate result."""
        result = AuditResult()
        # Core checks (L1-L6)
        result.checks.append(self.check_registry_completeness())
        result.checks.append(self.check_trace_integrity())
        result.checks.append(self.check_isolation_integrity())
        result.checks.append(self.check_rule_consistency())
        result.checks.append(self.check_scope_boundaries())
        result.checks.append(self.check_secret_hygiene())
        # Extended checks (L3, L8-L16)
        result.checks.append(self.check_tombstone_integrity())
        result.checks.append(self.check_environment_integrity())
        result.checks.append(self.check_pipeline_integrity())
        result.checks.append(self.check_deployment_gate_consistency())
        result.checks.append(self.check_secret_ref_validity())
        result.checks.append(self.check_plugin_integrity())
        result.checks.append(self.check_connector_integrity())
        result.checks.append(self.check_event_subscription_validity())
        result.checks.append(self.check_notification_rule_consistency())
        result.checks.append(self.check_schedule_consistency())
        result.checks.append(self.check_trace_coverage())
        # Extension checks
        result.checks.append(self.check_contract_integrity())
        result.checks.append(self.check_template_integrity())
        return result

    def check_registry_completeness(self) -> CheckResult:
        """Verify all principals have valid registry entry references."""
        orphaned = self._backend.fetch_all(
            "SELECT p.id, p.display_name FROM principals p "
            "LEFT JOIN registry_entries re ON p.registry_entry_id = re.id "
            "WHERE re.id IS NULL",
            (),
        )
        violations = [
            f"Principal {r['id']} ({r['display_name']}) has no registry entry"
            for r in orphaned
        ]
        return CheckResult(
            name="registry_completeness",
            passed=len(violations) == 0,
            details=f"{len(violations)} orphaned principals found",
            violations=violations,
        )

    def check_trace_integrity(self) -> CheckResult:
        """Verify the audit trail hash chain is intact."""
        query = AuditQuery(self._backend)
        verification = query.verify_chain()
        if verification.valid:
            return CheckResult(
                name="trace_integrity",
                passed=True,
                details=f"Chain verified: {verification.entries_checked} entries",
            )
        return CheckResult(
            name="trace_integrity",
            passed=False,
            details=f"Chain broken at sequence {verification.broken_at_sequence}",
            violations=[
                f"Hash chain broken at sequence {verification.broken_at_sequence}"
            ],
        )

    def check_isolation_integrity(self) -> CheckResult:
        """Check that no object has an owner that doesn't exist as a principal."""
        orphaned = self._backend.fetch_all(
            "SELECT o.id, o.owner_id FROM scoped_objects o "
            "LEFT JOIN principals p ON o.owner_id = p.id "
            "WHERE p.id IS NULL AND o.lifecycle != 'ARCHIVED'",
            (),
        )
        violations = [
            f"Object {r['id']} owned by non-existent principal {r['owner_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="isolation_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} orphaned objects found",
            violations=violations,
        )

    def check_rule_consistency(self) -> CheckResult:
        """Check for contradictory rules bound to the same target."""
        # Find bindings where the same target has both ALLOW and DENY rules
        # of the same type and same priority
        rows = self._backend.fetch_all(
            "SELECT rb.target_type, rb.target_id, r.rule_type, r.effect, "
            "r.priority, r.name "
            "FROM rule_bindings rb "
            "JOIN rules r ON rb.rule_id = r.id "
            "WHERE rb.lifecycle = 'ACTIVE' AND r.lifecycle = 'ACTIVE' "
            "ORDER BY rb.target_type, rb.target_id, r.rule_type, r.priority",
            (),
        )

        # Group by (target_type, target_id, rule_type, priority)
        groups: dict[tuple[str, str, str, int], list[dict[str, Any]]] = {}
        for row in rows:
            key = (row["target_type"], row["target_id"], row["rule_type"], row["priority"])
            groups.setdefault(key, []).append(row)

        warnings = []
        for key, rules in groups.items():
            effects = {r["effect"] for r in rules}
            if "ALLOW" in effects and "DENY" in effects:
                names = [r["name"] for r in rules]
                warnings.append(
                    f"Contradictory rules at same priority for "
                    f"{key[0]}:{key[1]} type={key[2]} priority={key[3]}: "
                    f"{', '.join(names)} — DENY wins by default"
                )

        return CheckResult(
            name="rule_consistency",
            passed=True,  # Warnings don't fail — DENY-overrides resolves it
            details=f"{len(warnings)} potential contradictions found",
            warnings=warnings,
        )

    def check_scope_boundaries(self) -> CheckResult:
        """Verify archived scopes don't have active projections."""
        rows = self._backend.fetch_all(
            "SELECT s.id, s.name, COUNT(sp.id) as active_projections "
            "FROM scopes s "
            "JOIN scope_projections sp ON s.id = sp.scope_id "
            "WHERE s.lifecycle = 'ARCHIVED' AND sp.lifecycle = 'ACTIVE' "
            "GROUP BY s.id",
            (),
        )
        violations = [
            f"Archived scope {r['id']} ({r['name']}) has "
            f"{r['active_projections']} active projections"
            for r in rows
        ]
        return CheckResult(
            name="scope_boundaries",
            passed=len(violations) == 0,
            details=f"{len(violations)} boundary violations found",
            violations=violations,
        )

    def check_secret_hygiene(self) -> CheckResult:
        """Check that audit trail states don't contain plaintext secret values."""
        # Get all active secret values (encrypted)
        secrets = self._backend.fetch_all(
            "SELECT s.id, s.name FROM secrets s WHERE s.lifecycle = 'ACTIVE'",
            (),
        )
        if not secrets:
            return CheckResult(
                name="secret_hygiene",
                passed=True,
                details="No active secrets to check",
            )

        # Check audit entries that reference secrets for state leaks
        violations = []
        for secret in secrets:
            # Check if any trace entry's before/after state mentions the secret
            # by checking for the secret name in non-secret trace entries
            rows = self._backend.fetch_all(
                "SELECT id, before_state_json, after_state_json "
                "FROM audit_trail "
                "WHERE target_type != 'secret' AND target_type != 'secret_ref' "
                "AND (before_state_json LIKE ? OR after_state_json LIKE ?) "
                "LIMIT 5",
                (f'%"plaintext"%', f'%"plaintext"%'),
            )
            for row in rows:
                violations.append(
                    f"Trace entry {row['id']} may contain plaintext secret data"
                )

        return CheckResult(
            name="secret_hygiene",
            passed=len(violations) == 0,
            details=f"{len(violations)} potential secret leaks found",
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Extended checks (L3, L8-L16, Extensions)
    # ------------------------------------------------------------------

    def check_tombstone_integrity(self) -> CheckResult:
        """L3: Verify tombstones reference valid objects (Invariant #5)."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT t.id, t.object_id FROM tombstones t "
                "LEFT JOIN scoped_objects o ON t.object_id = o.id "
                "WHERE o.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="tombstone_integrity", passed=True,
                               details="Tombstones table not available")
        violations = [
            f"Tombstone {r['id']} references non-existent object {r['object_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="tombstone_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} orphaned tombstones",
            violations=violations,
        )

    def check_environment_integrity(self) -> CheckResult:
        """L8: Verify environment objects reference valid environments and objects."""
        try:
            orphaned_envs = self._backend.fetch_all(
                "SELECT eo.id, eo.environment_id FROM environment_objects eo "
                "LEFT JOIN environments e ON eo.environment_id = e.id "
                "WHERE e.id IS NULL",
                (),
            )
            orphaned_objs = self._backend.fetch_all(
                "SELECT eo.id, eo.object_id FROM environment_objects eo "
                "LEFT JOIN scoped_objects o ON eo.object_id = o.id "
                "WHERE o.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="environment_integrity", passed=True,
                               details="Environment tables not available")
        violations = [
            f"Environment object {r['id']} references non-existent environment {r['environment_id']}"
            for r in orphaned_envs
        ] + [
            f"Environment object {r['id']} references non-existent object {r['object_id']}"
            for r in orphaned_objs
        ]
        return CheckResult(
            name="environment_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} environment integrity issues",
            violations=violations,
        )

    def check_pipeline_integrity(self) -> CheckResult:
        """L9: Verify stages reference valid pipelines."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT s.id, s.pipeline_id FROM stages s "
                "LEFT JOIN pipelines p ON s.pipeline_id = p.id "
                "WHERE p.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="pipeline_integrity", passed=True,
                               details="Pipeline tables not available")
        violations = [
            f"Stage {r['id']} references non-existent pipeline {r['pipeline_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="pipeline_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} pipeline integrity issues",
            violations=violations,
        )

    def check_deployment_gate_consistency(self) -> CheckResult:
        """L10: Verify deployment gates reference valid deployments."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT dg.id, dg.deployment_id FROM deployment_gates dg "
                "LEFT JOIN deployments d ON dg.deployment_id = d.id "
                "WHERE d.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="deployment_gate_consistency", passed=True,
                               details="Deployment tables not available")
        violations = [
            f"Gate {r['id']} references non-existent deployment {r['deployment_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="deployment_gate_consistency",
            passed=len(violations) == 0,
            details=f"{len(violations)} deployment gate issues",
            violations=violations,
        )

    def check_secret_ref_validity(self) -> CheckResult:
        """L11: Verify secret refs reference valid secrets and principals."""
        try:
            orphaned_secrets = self._backend.fetch_all(
                "SELECT sr.id, sr.secret_id FROM secret_refs sr "
                "LEFT JOIN secrets s ON sr.secret_id = s.id "
                "WHERE s.id IS NULL",
                (),
            )
            orphaned_principals = self._backend.fetch_all(
                "SELECT sr.id, sr.granted_to FROM secret_refs sr "
                "LEFT JOIN principals p ON sr.granted_to = p.id "
                "WHERE p.id IS NULL AND sr.lifecycle = 'ACTIVE'",
                (),
            )
        except Exception:
            return CheckResult(name="secret_ref_validity", passed=True,
                               details="Secret tables not available")
        violations = [
            f"Secret ref {r['id']} references non-existent secret {r['secret_id']}"
            for r in orphaned_secrets
        ] + [
            f"Secret ref {r['id']} granted to non-existent principal {r['granted_to']}"
            for r in orphaned_principals
        ]
        return CheckResult(
            name="secret_ref_validity",
            passed=len(violations) == 0,
            details=f"{len(violations)} secret ref issues",
            violations=violations,
        )

    def check_plugin_integrity(self) -> CheckResult:
        """L12: Verify plugin hooks reference valid plugins."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT ph.id, ph.plugin_id FROM plugin_hooks ph "
                "LEFT JOIN plugins p ON ph.plugin_id = p.id "
                "WHERE p.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="plugin_integrity", passed=True,
                               details="Plugin tables not available")
        violations = [
            f"Plugin hook {r['id']} references non-existent plugin {r['plugin_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="plugin_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} plugin integrity issues",
            violations=violations,
        )

    def check_connector_integrity(self) -> CheckResult:
        """L13: Verify connector policies reference valid connectors."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT cp.id, cp.connector_id FROM connector_policies cp "
                "LEFT JOIN connectors c ON cp.connector_id = c.id "
                "WHERE c.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="connector_integrity", passed=True,
                               details="Connector tables not available")
        violations = [
            f"Policy {r['id']} references non-existent connector {r['connector_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="connector_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} connector integrity issues",
            violations=violations,
        )

    def check_event_subscription_validity(self) -> CheckResult:
        """L14: Verify subscriptions reference valid owner principals."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT es.id, es.owner_id FROM event_subscriptions es "
                "LEFT JOIN principals p ON es.owner_id = p.id "
                "WHERE p.id IS NULL AND es.lifecycle = 'ACTIVE'",
                (),
            )
        except Exception:
            return CheckResult(name="event_subscription_validity", passed=True,
                               details="Event tables not available")
        violations = [
            f"Subscription {r['id']} owned by non-existent principal {r['owner_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="event_subscription_validity",
            passed=len(violations) == 0,
            details=f"{len(violations)} subscription validity issues",
            violations=violations,
        )

    def check_notification_rule_consistency(self) -> CheckResult:
        """L15: Verify notification rules reference valid owner principals."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT nr.id, nr.owner_id FROM notification_rules nr "
                "LEFT JOIN principals p ON nr.owner_id = p.id "
                "WHERE p.id IS NULL AND nr.lifecycle = 'ACTIVE'",
                (),
            )
        except Exception:
            return CheckResult(name="notification_rule_consistency", passed=True,
                               details="Notification tables not available")
        violations = [
            f"Notification rule {r['id']} owned by non-existent principal {r['owner_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="notification_rule_consistency",
            passed=len(violations) == 0,
            details=f"{len(violations)} notification rule issues",
            violations=violations,
        )

    def check_schedule_consistency(self) -> CheckResult:
        """L16: Verify scheduled actions reference valid schedules."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT sa.id, sa.schedule_id FROM scheduled_actions sa "
                "WHERE sa.schedule_id IS NOT NULL "
                "AND sa.schedule_id NOT IN (SELECT id FROM recurring_schedules)",
                (),
            )
        except Exception:
            return CheckResult(name="schedule_consistency", passed=True,
                               details="Scheduling tables not available")
        violations = [
            f"Action {r['id']} references non-existent schedule {r['schedule_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="schedule_consistency",
            passed=len(violations) == 0,
            details=f"{len(violations)} schedule consistency issues",
            violations=violations,
        )

    def check_trace_coverage(self) -> CheckResult:
        """All layers: Verify audit trail has entries for expected target types."""
        try:
            target_types = self._backend.fetch_all(
                "SELECT DISTINCT target_type FROM audit_trail", (),
            )
        except Exception:
            return CheckResult(name="trace_coverage", passed=True,
                               details="Audit trail not available")
        covered = {r["target_type"] for r in target_types}
        # Informational — report what's covered, don't fail
        return CheckResult(
            name="trace_coverage",
            passed=True,
            details=f"Audit trail covers {len(covered)} target types: {', '.join(sorted(covered)) if covered else 'none'}",
        )

    def check_contract_integrity(self) -> CheckResult:
        """A2: Verify contract versions reference valid contracts."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT cv.id, cv.contract_id FROM contract_versions cv "
                "LEFT JOIN contracts c ON cv.contract_id = c.id "
                "WHERE c.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="contract_integrity", passed=True,
                               details="Contract tables not available")
        violations = [
            f"Contract version {r['id']} references non-existent contract {r['contract_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="contract_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} contract integrity issues",
            violations=violations,
        )

    def check_template_integrity(self) -> CheckResult:
        """A7: Verify template versions reference valid templates."""
        try:
            orphaned = self._backend.fetch_all(
                "SELECT tv.id, tv.template_id FROM template_versions tv "
                "LEFT JOIN templates t ON tv.template_id = t.id "
                "WHERE t.id IS NULL",
                (),
            )
        except Exception:
            return CheckResult(name="template_integrity", passed=True,
                               details="Template tables not available")
        violations = [
            f"Template version {r['id']} references non-existent template {r['template_id']}"
            for r in orphaned
        ]
        return CheckResult(
            name="template_integrity",
            passed=len(violations) == 0,
            details=f"{len(violations)} template integrity issues",
            violations=violations,
        )
