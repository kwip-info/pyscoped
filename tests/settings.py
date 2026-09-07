import os

SECRET_KEY = "pyscoped-test-only"
INSTALLED_APPS = ["pyscoped", "tests.testapp"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
if os.getenv("PYSCOPED_POSTGRES"):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("PGDATABASE", "pyscoped_test"),
        "USER": os.environ.get("PGUSER", "pyscoped_test"),
        "PASSWORD": os.environ.get("PGPASSWORD", ""),
        "HOST": os.environ.get("PGHOST", "127.0.0.1"),
        "PORT": os.environ.get("PGPORT", "55438"),
    }
USE_TZ = True
DATABASES["other"] = dict(DATABASES["default"])
DATABASES["other"]["NAME"] = (
    ":memory:"
    if DATABASES["default"]["ENGINE"].endswith("sqlite3")
    else DATABASES["default"]["NAME"] + "_other"
)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
