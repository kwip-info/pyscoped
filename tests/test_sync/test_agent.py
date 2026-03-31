"""Tests for SyncAgent lifecycle and watermark management."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from scoped.audit.writer import AuditWriter
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager
from scoped.storage.sqlite import SQLiteBackend
from scoped.sync.agent import SyncAgent
from scoped.sync.config import SyncConfig
from scoped.sync.models import SyncBatchAck, SyncStatus, SyncStateSnapshot
from scoped.types import ActionType


@pytest.fixture
def backend():
    b = SQLiteBackend(":memory:")
    b.initialize()
    return b


@pytest.fixture
def populated_backend(backend):
    """Backend with some audit entries to sync."""
    ps = PrincipalStore(backend)
    audit = AuditWriter(backend)
    mgr = ScopedManager(backend, audit_writer=audit)
    user = ps.create_principal(kind="user", display_name="Alice", principal_id="alice")
    for i in range(5):
        mgr.create(object_type="Doc", owner_id=user.id, data={"n": i})
    return backend


@pytest.fixture
def mock_transport():
    transport = MagicMock()
    transport.push_batch.return_value = SyncBatchAck(
        batch_id="b1",
        accepted=True,
        server_sequence=100,
        server_chain_hash="h",
    )
    transport.compute_content_hash = MagicMock(return_value="content_hash_123")
    transport.sign_payload = MagicMock(return_value="sig_123")
    return transport


@pytest.fixture
def agent(populated_backend, mock_transport):
    config = SyncConfig(interval_seconds=1, batch_size=100)
    return SyncAgent(
        backend=populated_backend,
        api_key="psc_live_" + "a1" * 16,
        config=config,
        transport=mock_transport,
    )


class TestSyncAgentLifecycle:
    def test_start_creates_sync_state(self, agent, populated_backend):
        agent.start()
        time.sleep(0.2)
        agent.stop()

        row = populated_backend.fetch_one(
            "SELECT * FROM _sync_state WHERE id = 'singleton'", ()
        )
        assert row is not None

    def test_start_and_stop(self, agent):
        agent.start()
        time.sleep(0.1)
        agent.stop()
        # Should not raise

    def test_pause_and_resume(self, agent):
        agent.start()
        time.sleep(0.1)
        agent.pause()
        status = agent.status()
        assert status.status == SyncStatus.PAUSED

        agent.resume()
        time.sleep(0.1)
        status = agent.status()
        assert status.status in (SyncStatus.SYNCING, SyncStatus.PAUSED)
        agent.stop()

    def test_status_defaults(self, agent):
        status = agent.status()
        assert status.last_sequence == 0
        assert status.status == SyncStatus.IDLE

    def test_double_start_raises(self, agent):
        agent.start()
        time.sleep(0.1)
        from scoped.exceptions import SyncError
        with pytest.raises(SyncError, match="already running"):
            agent.start()
        agent.stop()


class TestSyncCycle:
    def test_pushes_batch(self, agent, mock_transport):
        agent.start()
        time.sleep(0.5)
        agent.stop()

        mock_transport.push_batch.assert_called()

    def test_batch_has_no_state_data(self, agent, mock_transport):
        """Entries in the batch must NOT contain before_state/after_state."""
        agent.start()
        time.sleep(0.5)
        agent.stop()

        if mock_transport.push_batch.called:
            batch = mock_transport.push_batch.call_args[0][0]
            for entry in batch.entries:
                assert not hasattr(entry, "before_state")
                assert not hasattr(entry, "after_state")

    def test_watermark_advances(self, agent, populated_backend, mock_transport):
        agent.start()
        time.sleep(0.5)
        agent.stop()

        status = agent.status()
        if mock_transport.push_batch.called:
            assert status.last_sequence > 0


class TestSyncStateTable:
    def test_table_exists(self, backend):
        assert backend.table_exists("_sync_state")

    def test_singleton_row(self, backend):
        ts = "2026-04-01T00:00:00+00:00"
        backend.execute(
            "INSERT OR IGNORE INTO _sync_state "
            "(id, last_sequence, last_hash, status, error_count, created_at, updated_at) "
            "VALUES ('singleton', 0, '', 'idle', 0, ?, ?)",
            (ts, ts),
        )
        row = backend.fetch_one(
            "SELECT * FROM _sync_state WHERE id = 'singleton'", ()
        )
        assert row is not None
        assert row["last_sequence"] == 0
