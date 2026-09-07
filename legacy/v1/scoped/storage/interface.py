"""Abstract storage backend interface.

Every persistence operation in the framework goes through this interface.
Backends must implement all methods. Transactions are explicit — no implicit
auto-commit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlalchemy as sa


class StorageTransaction(ABC):
    """
    A storage transaction.

    Used as a context manager:
        async with backend.transaction() as tx:
            tx.execute("INSERT ...", params)
    """

    @abstractmethod
    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        """Execute a single statement within this transaction."""
        ...

    @abstractmethod
    def execute_many(self, sql: str, params_seq: list[tuple[Any, ...]]) -> None:
        """Execute a statement with multiple parameter sets."""
        ...

    @abstractmethod
    def fetch_one(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> dict[str, Any] | None:
        """Execute a query and return the first row as a dict, or None."""
        ...

    @abstractmethod
    def fetch_all(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        """Execute a query and return all rows as dicts."""
        ...

    @abstractmethod
    def commit(self) -> None:
        """Commit the transaction."""
        ...

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the transaction."""
        ...

    def __enter__(self) -> StorageTransaction:
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        if exc_type is not None:
            self.rollback()
        # Caller is responsible for explicit commit


class StorageBackend(ABC):
    """
    Abstract storage backend.

    Implementations provide:
    - Connection management
    - Transaction support
    - Schema migration
    - Raw query execution
    """

    @property
    def dialect(self) -> str:
        """Return the SQL dialect identifier (e.g. ``'sqlite'``, ``'postgres'``)."""
        return "generic"

    @property
    def engine(self) -> sa.engine.Engine | None:
        """Return the SQLAlchemy engine, if available.

        SA-backed backends (``SASQLiteBackend``, ``SAPostgresBackend``)
        return their engine instance.  Legacy backends return ``None``.
        """
        return None

    @abstractmethod
    def initialize(self) -> None:
        """
        Initialize the backend — create tables, run migrations, etc.

        Called once during framework startup.
        """
        ...

    @abstractmethod
    def transaction(self) -> StorageTransaction:
        """
        Begin a new transaction.

        Usage:
            tx = backend.transaction()
            try:
                tx.execute(...)
                tx.commit()
            except:
                tx.rollback()
                raise
        """
        ...

    @abstractmethod
    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> Any:
        """Execute a statement outside a transaction (auto-commit)."""
        ...

    @abstractmethod
    def fetch_one(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> dict[str, Any] | None:
        """Query and return first row, or None."""
        ...

    @abstractmethod
    def fetch_all(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        """Query and return all rows."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the backend and release resources."""
        ...

    def execute_script(self, sql: str) -> None:
        """Execute multiple SQL statements at once.

        Default implementation splits on semicolons. Backends may override
        with a more efficient native implementation (e.g. sqlite3.executescript).
        """
        for statement in sql.split(";"):
            stmt = statement.strip()
            if stmt and not stmt.startswith("--"):
                self.execute(stmt)

    @abstractmethod
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        ...
