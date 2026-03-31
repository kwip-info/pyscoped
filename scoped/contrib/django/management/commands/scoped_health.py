"""Management command: run Scoped framework health checks."""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run Scoped framework health checks"

    def handle(self, *args, **options):
        from scoped.contrib.django import get_backend
        from scoped.testing.health import HealthChecker

        checker = HealthChecker(get_backend())
        status = checker.check_all()

        for name, check in status.checks.items():
            icon = "PASS" if check.passed else "FAIL"
            self.stdout.write(f"  [{icon}] {name}: {check.detail}")

        if status.healthy:
            self.stdout.write(self.style.SUCCESS("\nAll health checks passed."))
        else:
            self.stdout.write(self.style.ERROR("\nSome health checks failed."))
