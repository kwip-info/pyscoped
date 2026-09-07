"""Django adapter test fixtures."""

from __future__ import annotations

import pytest

django = pytest.importorskip("django")

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.test_contrib.test_django.settings")

import django as _django

_django.setup()
