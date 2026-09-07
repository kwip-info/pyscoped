import os

SECRET_KEY = "example-only-not-for-deployment"
INSTALLED_APPS = ["django.contrib.contenttypes", "django.contrib.auth", "pyscoped", "billing"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("PYSCOPED_EXAMPLE_DB", "example.sqlite3"),
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
PYSCOPED_CONTEXT_RESOLVER = "billing.auth.resolve_context"
