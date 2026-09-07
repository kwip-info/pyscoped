"""Incremental Django scoping. Import model APIs after Django settings are configured."""

__version__ = "2.0.0"

from .context import ScopeContext, current_context, scope
from .registry import register, scoped

__all__ = ["ScopeContext", "current_context", "scope", "register", "scoped"]
