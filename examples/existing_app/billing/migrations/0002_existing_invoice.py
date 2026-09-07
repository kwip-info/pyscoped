from django.db import migrations


def seed_existing(apps, schema_editor):
    # Historical models intentionally represent the database before adoption.
    # This is baseline data, not a claim that past writes were tracked by PyScoped.
    Organization = apps.get_model("billing", "Organization")
    Invoice = apps.get_model("billing", "Invoice")
    alias = schema_editor.connection.alias
    Organization.objects.using(alias).create(id="acme", name="Acme")
    Invoice.objects.using(alias).create(
        id=501,
        organization_id="acme",
        amount="10.00",
        status="draft",
        private_note="retained; never audited",
    )


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed_existing, migrations.RunPython.noop)]
