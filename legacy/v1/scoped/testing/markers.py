"""Pytest markers for backend-specific test filtering.

Usage::

    import pytest
    from scoped.testing.markers import sqlite_only, postgres_only

    @sqlite_only
    def test_fts5_search():
        ...  # Runs only when the backend fixture is SQLite

    @postgres_only
    def test_rls_policies():
        ...  # Runs only when the backend fixture is PostgreSQL

Register markers in ``conftest.py`` or ``pyproject.toml``::

    [tool.pytest.ini_options]
    markers = [
        "sqlite_only: Run only with SQLite backend",
        "postgres_only: Run only with PostgreSQL backend",
    ]
"""

from __future__ import annotations

import pytest

sqlite_only = pytest.mark.sqlite_only
"""Skip unless the test is running against a SQLite backend."""

postgres_only = pytest.mark.postgres_only
"""Skip unless the test is running against a PostgreSQL backend."""
