"""Tests for audit chain optimization and retention (P2 item 5D)."""

from __future__ import annotations

import pytest

from scoped.audit.query import AuditQuery, ChainVerification, VerificationEntry
from scoped.audit.retention import AuditRetention, RetentionPolicy, RetentionResult
from scoped.audit.writer import AuditWriter
from scoped.types import ActionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def writer(sqlite_backend):
    return AuditWriter(sqlite_backend)


@pytest.fixture
def query(sqlite_backend):
    return AuditQuery(sqlite_backend)


@pytest.fixture
def retention(sqlite_backend):
    return AuditRetention(sqlite_backend)


# ---------------------------------------------------------------------------
# Verify chain optimization tests
# ---------------------------------------------------------------------------

class TestVerifyColumnPruning:
    """verify_chain() should produce the same result with pruned columns."""

    def test_verify_column_pruning(self, writer, query):
        """Verify that the optimized verify_chain (pruned SELECT) still works."""
        for i in range(20):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
                before_state={"version": i},
                after_state={"version": i + 1},
                metadata={"extra": f"value-{i}"},
            )

        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 20
        assert result.first_sequence == 1
        assert result.last_sequence == 20

    def test_verify_still_detects_tampering(self, sqlite_backend, writer, query):
        """Ensure optimized verification still catches tampered hashes."""
        for i in range(5):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
                before_state={"v": i},
                after_state={"v": i + 1},
            )

        # Tamper with entry 3's hash
        sqlite_backend.execute(
            "UPDATE audit_trail SET hash = 'tampered' WHERE sequence = 3"
        )

        result = query.verify_chain()
        assert not result.valid
        assert result.broken_at_sequence == 3

    def test_verify_still_detects_broken_chain_link(self, sqlite_backend, writer, query):
        """Ensure optimized verification still catches broken chain links."""
        for i in range(5):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
            )

        # Break the chain link on entry 4
        sqlite_backend.execute(
            "UPDATE audit_trail SET previous_hash = 'wrong' WHERE sequence = 4"
        )

        result = query.verify_chain()
        assert not result.valid
        assert result.broken_at_sequence == 4


class TestConfigurableChunkSize:
    """Different chunk sizes should produce identical verification results."""

    def test_configurable_chunk_size(self, writer, query):
        """Chunk sizes 2, 5, 10, and default should all yield the same result."""
        for i in range(15):
            writer.record(
                actor_id="bob",
                action=ActionType.UPDATE,
                target_type="Task",
                target_id=f"task-{i}",
                before_state={"status": "open"},
                after_state={"status": "closed"},
            )

        for chunk in (2, 5, 10, 15, 5000):
            result = query.verify_chain(chunk_size=chunk)
            assert result.valid, f"Failed with chunk_size={chunk}"
            assert result.entries_checked == 15, f"Wrong count with chunk_size={chunk}"

    def test_chunk_size_one(self, writer, query):
        """Edge case: chunk_size=1 should still verify correctly."""
        for i in range(5):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
            )

        result = query.verify_chain(chunk_size=1)
        assert result.valid
        assert result.entries_checked == 5


class TestVerifyLargeChain:
    """Verify chain integrity with a larger number of entries."""

    def test_verify_10k_entries(self, writer, query):
        """Insert 10k entries via AuditWriter, verify chain integrity."""
        batch = [
            {
                "actor_id": f"user-{i % 10}",
                "action": ActionType.CREATE,
                "target_type": "Item",
                "target_id": f"item-{i}",
                "before_state": {"seq": i},
                "after_state": {"seq": i + 1},
            }
            for i in range(10_000)
        ]

        # Write in batches of 500 to keep transactions manageable
        for start in range(0, len(batch), 500):
            writer.record_batch(batch[start : start + 500])

        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 10_000
        assert result.first_sequence == 1
        assert result.last_sequence == 10_000

    def test_verify_10k_with_small_chunks(self, writer, query):
        """Verify the same 10k chain with a smaller chunk size."""
        batch = [
            {
                "actor_id": "sys",
                "action": ActionType.UPDATE,
                "target_type": "Obj",
                "target_id": f"o-{i}",
            }
            for i in range(10_000)
        ]
        for start in range(0, len(batch), 500):
            writer.record_batch(batch[start : start + 500])

        result = query.verify_chain(chunk_size=250)
        assert result.valid
        assert result.entries_checked == 10_000


# ---------------------------------------------------------------------------
# Retention tests
# ---------------------------------------------------------------------------

class TestRetentionEstimate:
    """Estimate should report how many entries would be deleted."""

    def test_retention_estimate_max_entries(self, writer, retention):
        """Estimate with max_entries reports correct excess count."""
        for i in range(20):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
            )

        # Keep only 15 -> 5 should be deleted
        policy = RetentionPolicy(max_entries=15)
        estimate = retention.estimate(policy)
        assert estimate == 5

    def test_retention_estimate_no_excess(self, writer, retention):
        """Estimate returns 0 when entries are within limits."""
        for i in range(5):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
            )

        policy = RetentionPolicy(max_entries=10)
        assert retention.estimate(policy) == 0

    def test_retention_estimate_empty_trail(self, retention):
        """Estimate on empty trail returns 0."""
        policy = RetentionPolicy(max_entries=10)
        assert retention.estimate(policy) == 0


class TestRetentionApply:
    """Apply should delete entries and return correct counts."""

    def test_apply_max_entries(self, sqlite_backend, writer, retention, query):
        """Apply max_entries deletes oldest entries, keeps newest."""
        for i in range(20):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
            )

        policy = RetentionPolicy(max_entries=10)
        result = retention.apply(policy)
        assert result.deleted == 10

        # Verify that the remaining entries are the newest 10
        remaining = query.query(limit=100)
        assert len(remaining) == 10
        sequences = [e.sequence for e in remaining]
        assert min(sequences) == 11
        assert max(sequences) == 20


class TestRetentionCompact:
    """Compaction nulls state columns without breaking hashes."""

    def test_retention_compact(self, sqlite_backend, writer, retention):
        """Compact state columns, verify they become null."""
        for i in range(10):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
                before_state={"version": i},
                after_state={"version": i + 1},
            )

        compacted = retention.compact(compact_before=True, compact_after=True)
        assert compacted == 10

        # Verify that state columns are null
        rows = sqlite_backend.fetch_all(
            "SELECT before_state, after_state FROM audit_trail ORDER BY sequence"
        )
        for row in rows:
            assert row["before_state"] is None
            assert row["after_state"] is None

    def test_compact_before_sequence(self, sqlite_backend, writer, retention):
        """Compact only entries before a specific sequence."""
        for i in range(10):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
                before_state={"v": i},
                after_state={"v": i + 1},
            )

        compacted = retention.compact(
            before_sequence=6,
            compact_before=True,
            compact_after=True,
        )
        assert compacted == 5  # Sequences 1-5

        # Entries 1-5 should be compacted
        for seq in range(1, 6):
            row = sqlite_backend.fetch_one(
                "SELECT before_state, after_state FROM audit_trail WHERE sequence = ?",
                (seq,),
            )
            assert row["before_state"] is None
            assert row["after_state"] is None

        # Entries 6-10 should still have state
        for seq in range(6, 11):
            row = sqlite_backend.fetch_one(
                "SELECT before_state, after_state FROM audit_trail WHERE sequence = ?",
                (seq,),
            )
            assert row["before_state"] is not None
            assert row["after_state"] is not None

    def test_compact_no_values_returns_zero(self, retention):
        """Compact with both flags False returns 0."""
        assert retention.compact(compact_before=False, compact_after=False) == 0


class TestVerifyAfterCompaction:
    """Hash chain should remain valid after compacting state columns."""

    def test_verify_after_compaction(self, writer, query, retention):
        """Compact state, then verify chain still passes.

        Hashes are computed from id, sequence, actor_id, action,
        target_type, target_id, timestamp, and previous_hash -- NOT
        from before_state or after_state. So compaction should not
        break verification.
        """
        for i in range(25):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Doc",
                target_id=f"doc-{i}",
                before_state={"old": i},
                after_state={"new": i + 1},
            )

        # Compact all entries
        compacted = retention.compact(compact_before=True, compact_after=True)
        assert compacted == 25

        # Chain should still verify
        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 25

    def test_verify_after_partial_compaction(self, writer, query, retention):
        """Partial compaction (only before_state) preserves chain."""
        for i in range(15):
            writer.record(
                actor_id="bob",
                action=ActionType.UPDATE,
                target_type="Task",
                target_id=f"task-{i}",
                before_state={"status": "open"},
                after_state={"status": "closed"},
            )

        retention.compact(
            before_sequence=10,
            compact_before=True,
            compact_after=False,
        )

        result = query.verify_chain()
        assert result.valid
        assert result.entries_checked == 15

    def test_verify_after_policy_compact(self, writer, query, retention):
        """Apply a policy with compaction flags, then verify chain."""
        for i in range(20):
            writer.record(
                actor_id="alice",
                action=ActionType.CREATE,
                target_type="Invoice",
                target_id=f"inv-{i}",
                before_state={"amount": i * 100},
                after_state={"amount": (i + 1) * 100},
            )

        policy = RetentionPolicy(
            compact_before_state=True,
            compact_after_state=True,
        )
        result = retention.apply(policy)
        assert result.compacted == 20
        assert result.deleted == 0

        # Chain integrity preserved
        verification = query.verify_chain()
        assert verification.valid
        assert verification.entries_checked == 20
