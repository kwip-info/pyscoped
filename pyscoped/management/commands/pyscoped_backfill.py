import json
from dataclasses import asdict

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from pyscoped import scope
from pyscoped.history import backfill


class Command(BaseCommand):
    help = "Preview (default) or apply an idempotent baseline for one model and scope."

    def add_arguments(self, parser):
        parser.add_argument("model", help="app_label.ModelName")
        parser.add_argument("--actor", required=True)
        parser.add_argument("--scope", required=True)
        parser.add_argument("--database", default="default")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--reason", default="Existing-data baseline")

    def handle(self, *args, **options):
        try:
            model = apps.get_model(options["model"])
        except (LookupError, ValueError) as exc:
            raise CommandError(f"Unknown model: {options['model']}") from exc
        with scope(actor=options["actor"], scope=options["scope"], reason=options["reason"]):
            result = backfill(
                model,
                using=options["database"],
                dry_run=not options["apply"],
                batch_size=options["batch_size"],
            )
        self.stdout.write(json.dumps(asdict(result), sort_keys=True))
