"""Compliance reports — structured output of compliance audit results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from scoped.testing.auditor import AuditResult, ComplianceAuditor
from scoped.testing.health import HealthChecker, HealthStatus
from scoped.testing.introspection import IntrospectionResult, RegistryIntrospector
from scoped.testing.manifest import LAYER_SPECS
from scoped.storage.interface import StorageBackend
from scoped.types import now_utc


@dataclass(slots=True)
class ComplianceReport:
    """Complete compliance report combining all checks."""

    generated_at: datetime
    audit_result: AuditResult | None = None
    introspection_result: IntrospectionResult | None = None
    health_status: HealthStatus | None = None

    @property
    def passed(self) -> bool:
        """True if all checks passed."""
        if self.audit_result and not self.audit_result.passed:
            return False
        if self.introspection_result and not self.introspection_result.is_clean:
            return False
        if self.health_status and not self.health_status.healthy:
            return False
        return True

    @property
    def summary(self) -> dict[str, Any]:
        """Summary dict for serialization or display."""
        result: dict[str, Any] = {
            "generated_at": self.generated_at.isoformat(),
            "overall_passed": self.passed,
        }
        if self.audit_result:
            result["audit"] = {
                "passed": self.audit_result.passed,
                "checks": self.audit_result.total_checks,
                "passed_count": self.audit_result.passed_count,
                "failed_count": self.audit_result.failed_count,
                "violations": self.audit_result.all_violations,
                "warnings": self.audit_result.all_warnings,
            }
        if self.introspection_result:
            result["registry"] = {
                "clean": self.introspection_result.is_clean,
                "total_entries": self.introspection_result.total_entries,
                "active_entries": self.introspection_result.active_entries,
                "orphaned": len(self.introspection_result.orphaned_entries),
                "duplicate_urns": len(self.introspection_result.duplicate_urns),
            }
        if self.health_status:
            result["health"] = {
                "healthy": self.health_status.healthy,
                "checks": {
                    name: {"passed": c.passed, "detail": c.detail}
                    for name, c in self.health_status.checks.items()
                },
            }
        return result

    def format_text(self) -> str:
        """Format as human-readable text report."""
        lines = []
        lines.append("=" * 60)
        lines.append("SCOPED COMPLIANCE REPORT")
        lines.append(f"Generated: {self.generated_at.isoformat()}")
        lines.append("=" * 60)
        lines.append("")

        if self.audit_result:
            lines.append("--- Static Compliance ---")
            for check in self.audit_result.checks:
                status = "PASS" if check.passed else "FAIL"
                lines.append(f"  [{status}] {check.name}: {check.details}")
                for w in check.warnings:
                    lines.append(f"         WARNING: {w}")
                for v in check.violations:
                    lines.append(f"         VIOLATION: {v}")
            lines.append("")

        if self.introspection_result:
            lines.append("--- Registry Introspection ---")
            ir = self.introspection_result
            lines.append(f"  Total entries: {ir.total_entries}")
            lines.append(f"  Active: {ir.active_entries}")
            lines.append(f"  Archived: {ir.archived_entries}")
            if ir.orphaned_entries:
                lines.append(f"  Orphaned: {len(ir.orphaned_entries)}")
            if ir.duplicate_urns:
                lines.append(f"  Duplicate URNs: {len(ir.duplicate_urns)}")
            status = "CLEAN" if ir.is_clean else "ISSUES FOUND"
            lines.append(f"  Status: {status}")
            lines.append("")

        if self.health_status:
            lines.append("--- Health ---")
            for name, check in self.health_status.checks.items():
                status = "PASS" if check.passed else "FAIL"
                lines.append(f"  [{status}] {name}: {check.detail}")
            lines.append("")

        # Layer coverage summary
        lines.append("--- Layer Coverage ---")
        for spec in LAYER_SPECS:
            lines.append(
                f"  L{spec.number:2d} {spec.name:<15s} "
                f"registry={'Y' if spec.has_registry else '-'} "
                f"audit={'Y' if spec.has_audit else '-'} "
                f"invariants={','.join(str(i) for i in spec.invariants)}"
            )
        lines.append("")

        overall = "PASSED" if self.passed else "FAILED"
        lines.append(f"Overall: {overall}")
        lines.append("=" * 60)

        return "\n".join(lines)


class ComplianceReporter:
    """Generate full compliance reports."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def generate(
        self,
        *,
        include_audit: bool = True,
        include_introspection: bool = True,
        include_health: bool = True,
    ) -> ComplianceReport:
        """Generate a complete compliance report."""
        report = ComplianceReport(generated_at=now_utc())

        if include_audit:
            auditor = ComplianceAuditor(self._backend)
            report.audit_result = auditor.run_all()

        if include_introspection:
            introspector = RegistryIntrospector(self._backend)
            report.introspection_result = introspector.scan()

        if include_health:
            checker = HealthChecker(self._backend)
            report.health_status = checker.check_all()

        return report
