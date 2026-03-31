"""Management command: run Scoped compliance report."""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run a full Scoped compliance report"

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-health", action="store_true", help="Skip health checks"
        )
        parser.add_argument(
            "--no-introspection", action="store_true", help="Skip registry introspection"
        )

    def handle(self, *args, **options):
        from scoped.contrib.django import get_backend
        from scoped.testing.reports import ComplianceReporter

        reporter = ComplianceReporter(get_backend())
        report = reporter.generate(
            include_health=not options["no_health"],
            include_introspection=not options["no_introspection"],
        )

        self.stdout.write(report.format_text())

        if report.passed:
            self.stdout.write(self.style.SUCCESS("\nCompliance: PASSED"))
        else:
            self.stdout.write(self.style.ERROR("\nCompliance: FAILED"))
