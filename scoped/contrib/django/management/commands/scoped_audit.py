"""Management command: query the Scoped audit trail."""

from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Query the Scoped audit trail"

    def add_arguments(self, parser):
        parser.add_argument("--actor", type=str, help="Filter by actor ID")
        parser.add_argument("--target", type=str, help="Filter by target ID")
        parser.add_argument("--action", type=str, help="Filter by action type")
        parser.add_argument("--limit", type=int, default=20, help="Max entries (default 20)")

    def handle(self, *args, **options):
        from scoped.audit.query import AuditQuery
        from scoped.contrib.django import get_backend
        from scoped.types import ActionType

        query = AuditQuery(get_backend())

        kwargs: dict = {"limit": options["limit"]}
        if options["actor"]:
            kwargs["actor_id"] = options["actor"]
        if options["target"]:
            kwargs["target_id"] = options["target"]
        if options["action"]:
            kwargs["action"] = ActionType(options["action"])

        entries = query.query(**kwargs)

        if not entries:
            self.stdout.write("No audit entries found.")
            return

        for entry in entries:
            self.stdout.write(
                f"  [{entry.sequence}] {entry.action.value:<20s} "
                f"actor={entry.actor_id[:8]}  "
                f"target={entry.target_type}:{entry.target_id[:8]}  "
                f"at={entry.timestamp.isoformat()}"
            )

        self.stdout.write(f"\n{len(entries)} entries shown.")
