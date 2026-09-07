"""Tests for ComplianceReporter and ComplianceReport."""

from __future__ import annotations

from scoped.testing.reports import ComplianceReport, ComplianceReporter
from scoped.types import generate_id, now_utc


def _setup_principal(backend) -> str:
    pid = generate_id()
    ts = now_utc().isoformat()
    reg_id = generate_id()
    backend.execute(
        "INSERT OR IGNORE INTO registry_entries "
        "(id, urn, kind, namespace, name, registered_at, registered_by) "
        "VALUES (?, ?, 'MODEL', 'test', 'stub', ?, 'system')",
        (reg_id, f"scoped:MODEL:test:stub_{pid[:8]}:1", ts),
    )
    backend.execute(
        "INSERT INTO principals (id, kind, display_name, registry_entry_id, created_at) "
        "VALUES (?, 'user', 'Test User', ?, ?)",
        (pid, reg_id, ts),
    )
    return pid


class TestComplianceReporter:
    def test_generate_full_report(self, sqlite_backend):
        _setup_principal(sqlite_backend)
        reporter = ComplianceReporter(sqlite_backend)

        report = reporter.generate()

        assert report.passed
        assert report.audit_result is not None
        assert report.introspection_result is not None
        assert report.health_status is not None

    def test_generate_audit_only(self, sqlite_backend):
        _setup_principal(sqlite_backend)
        reporter = ComplianceReporter(sqlite_backend)

        report = reporter.generate(
            include_introspection=False,
            include_health=False,
        )

        assert report.audit_result is not None
        assert report.introspection_result is None
        assert report.health_status is None

    def test_generate_health_only(self, sqlite_backend):
        reporter = ComplianceReporter(sqlite_backend)

        report = reporter.generate(
            include_audit=False,
            include_introspection=False,
        )

        assert report.health_status is not None
        assert report.audit_result is None


class TestComplianceReport:
    def test_summary(self, sqlite_backend):
        _setup_principal(sqlite_backend)
        reporter = ComplianceReporter(sqlite_backend)
        report = reporter.generate()

        summary = report.summary

        assert "overall_passed" in summary
        assert summary["overall_passed"] is True
        assert "audit" in summary
        assert "registry" in summary
        assert "health" in summary

    def test_format_text(self, sqlite_backend):
        _setup_principal(sqlite_backend)
        reporter = ComplianceReporter(sqlite_backend)
        report = reporter.generate()

        text = report.format_text()

        assert "SCOPED COMPLIANCE REPORT" in text
        assert "Static Compliance" in text
        assert "Registry Introspection" in text
        assert "Health" in text
        assert "PASSED" in text

    def test_format_text_with_failures(self, sqlite_backend):
        # Create a valid principal, then orphan it by deleting registry entry
        pid = _setup_principal(sqlite_backend)
        row = sqlite_backend.fetch_one(
            "SELECT registry_entry_id FROM principals WHERE id = ?", (pid,),
        )
        sqlite_backend.execute("PRAGMA foreign_keys = OFF", ())
        sqlite_backend.execute(
            "DELETE FROM registry_entries WHERE id = ?", (row["registry_entry_id"],),
        )
        sqlite_backend.execute("PRAGMA foreign_keys = ON", ())

        reporter = ComplianceReporter(sqlite_backend)
        report = reporter.generate()

        text = report.format_text()

        assert "FAIL" in text
        assert "FAILED" in text

    def test_empty_report_passes(self):
        report = ComplianceReport(generated_at=now_utc())

        assert report.passed  # No checks = passed
        summary = report.summary
        assert summary["overall_passed"] is True

    def test_generated_at(self, sqlite_backend):
        reporter = ComplianceReporter(sqlite_backend)
        report = reporter.generate()

        assert report.generated_at is not None
        assert "generated_at" in report.summary
