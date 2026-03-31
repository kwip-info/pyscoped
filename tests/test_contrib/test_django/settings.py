"""Minimal Django settings for testing the Scoped Django adapter."""

SECRET_KEY = "test-secret-key-not-for-production"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

INSTALLED_APPS = [
    "scoped.contrib.django",
]

MIDDLEWARE = [
    "scoped.contrib.django.middleware.ScopedContextMiddleware",
]

ROOT_URLCONF = "tests.test_contrib.test_django.urls"

SCOPED_PRINCIPAL_HEADER = "HTTP_X_SCOPED_PRINCIPAL_ID"
SCOPED_EXEMPT_PATHS = ["/exempt/"]
