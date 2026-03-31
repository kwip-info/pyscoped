"""Shared SQL dialect utilities.

Helpers for translating between SQL placeholder styles and other
dialect-specific syntax. Used by PostgresBackend and the Django ORM backend.
"""

from __future__ import annotations

import re

# Matches a ``?`` placeholder that is NOT inside a single-quoted string.
_PLACEHOLDER_RE = re.compile(r"'[^']*'|(\?)")


def translate_placeholders(sql: str) -> str:
    """Convert SQLite-style ``?`` positional placeholders to ``%s``.

    Single-quoted string literals are left untouched so that a literal
    ``'?'`` inside a SQL string is not replaced.
    """

    def _replace(match: re.Match) -> str:
        if match.group(1) is not None:
            return "%s"
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, sql)
