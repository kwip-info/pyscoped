"""Blob storage backends — pluggable binary content storage.

Blob backends handle the actual storage of binary content (files, images,
documents). The metadata (size, content type, checksum) is tracked in the
SQL database; the bytes live in the blob backend.

Two implementations are provided:
  - ``InMemoryBlobBackend`` — for tests (stores bytes in a dict)
  - ``LocalBlobBackend`` — filesystem-based (stores files under a root dir)
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO


class BlobBackend(ABC):
    """Abstract interface for binary content storage."""

    @abstractmethod
    def store(self, blob_id: str, data: bytes) -> str:
        """Store binary data and return a storage path/key.

        The returned string is opaque to the framework — it's whatever
        the backend needs to retrieve the data later.
        """

    @abstractmethod
    def retrieve(self, storage_path: str) -> bytes:
        """Retrieve binary data by its storage path/key.

        Raises ``FileNotFoundError`` if the blob doesn't exist.
        """

    @abstractmethod
    def delete(self, storage_path: str) -> bool:
        """Delete binary data. Returns True if deleted, False if not found."""

    @abstractmethod
    def exists(self, storage_path: str) -> bool:
        """Check if a blob exists at the given path."""

    def store_stream(self, blob_id: str, fp: BinaryIO) -> str:
        """Store binary data from a file-like object and return a storage path.

        Default implementation reads into memory and delegates to ``store()``.
        Backends can override for true streaming support.
        """
        return self.store(blob_id, fp.read())

    def retrieve_stream(self, storage_path: str) -> Iterator[bytes]:
        """Retrieve binary data as an iterator of chunks.

        Default implementation loads into memory and yields a single chunk.
        Backends can override for true streaming support.
        """
        yield self.retrieve(storage_path)

    @staticmethod
    def compute_content_hash(data: bytes) -> str:
        """SHA-256 hash of binary content."""
        return hashlib.sha256(data).hexdigest()


class InMemoryBlobBackend(BlobBackend):
    """In-memory blob storage for testing."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def store(self, blob_id: str, data: bytes) -> str:
        path = f"mem://{blob_id}"
        self._store[path] = data
        return path

    def retrieve(self, storage_path: str) -> bytes:
        if storage_path not in self._store:
            raise FileNotFoundError(f"Blob not found: {storage_path}")
        return self._store[storage_path]

    def delete(self, storage_path: str) -> bool:
        if storage_path in self._store:
            del self._store[storage_path]
            return True
        return False

    def exists(self, storage_path: str) -> bool:
        return storage_path in self._store

    def store_stream(self, blob_id: str, fp: BinaryIO) -> str:
        path = f"mem://{blob_id}"
        self._store[path] = fp.read()
        return path

    def retrieve_stream(self, storage_path: str) -> Iterator[bytes]:
        if storage_path not in self._store:
            raise FileNotFoundError(f"Blob not found: {storage_path}")
        yield self._store[storage_path]

    @property
    def count(self) -> int:
        """Number of blobs currently stored (for testing)."""
        return len(self._store)


class LocalBlobBackend(BlobBackend):
    """Filesystem-based blob storage.

    Blobs are stored as files under ``root_dir``, organized into
    subdirectories using the first 4 characters of the blob_id
    to avoid too many files in a single directory.

    Structure::

        root_dir/
            ab/cd/
                abcdef1234567890...
    """

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, storage_path: str) -> Path:
        """Resolve a storage path and verify it stays within the root directory."""
        full = (self._root / storage_path).resolve()
        if not str(full).startswith(str(self._root)):
            raise ValueError(f"Path traversal detected: {storage_path}")
        return full

    def store(self, blob_id: str, data: bytes) -> str:
        path = self._blob_path(blob_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path.relative_to(self._root))

    def retrieve(self, storage_path: str) -> bytes:
        full_path = self._safe_path(storage_path)
        if not full_path.exists():
            raise FileNotFoundError(f"Blob not found: {storage_path}")
        return full_path.read_bytes()

    def delete(self, storage_path: str) -> bool:
        full_path = self._safe_path(storage_path)
        if full_path.exists():
            full_path.unlink()
            return True
        return False

    def exists(self, storage_path: str) -> bool:
        return self._safe_path(storage_path).exists()

    def store_stream(self, blob_id: str, fp: BinaryIO) -> str:
        """Store from a file-like object using 64KB chunked writes."""
        path = self._blob_path(blob_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as out:
            while True:
                chunk = fp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
        return str(path.relative_to(self._root))

    def retrieve_stream(self, storage_path: str) -> Iterator[bytes]:
        """Yield 64KB chunks from a stored blob."""
        full_path = self._safe_path(storage_path)
        if not full_path.exists():
            raise FileNotFoundError(f"Blob not found: {storage_path}")
        with open(full_path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk

    def _blob_path(self, blob_id: str) -> Path:
        """Shard blobs into subdirectories: ab/cd/abcdef..."""
        prefix_a = blob_id[:2]
        prefix_b = blob_id[2:4]
        return self._root / prefix_a / prefix_b / blob_id
