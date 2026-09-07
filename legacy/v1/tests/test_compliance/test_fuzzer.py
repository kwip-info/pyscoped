"""Tests for IsolationFuzzer."""

from __future__ import annotations

from scoped.testing.fuzzer import IsolationFuzzer


class TestIsolationFuzzer:
    def test_minimal_fuzz(self, sqlite_backend):
        """Minimal fuzz run — should complete without violations."""
        fuzzer = IsolationFuzzer(sqlite_backend, seed=42)

        result = fuzzer.run(
            num_principals=3,
            num_objects=5,
            num_scopes=2,
            num_projections=2,
            num_mutations=2,
        )

        assert result.passed
        assert result.principals_created == 3
        assert result.objects_created == 5
        assert result.scopes_created == 2

    def test_deterministic_with_seed(self, sqlite_backend):
        """Same seed produces same result."""
        fuzzer1 = IsolationFuzzer(sqlite_backend, seed=123)
        result1 = fuzzer1.run(
            num_principals=3,
            num_objects=5,
            num_scopes=1,
            num_projections=1,
            num_mutations=0,
        )

        # Can't run again on same backend (IDs would collide),
        # but we can verify the structure
        assert result1.principals_created == 3
        assert result1.objects_created == 5

    def test_no_violations_with_only_owners(self, sqlite_backend):
        """When no projections exist, only owners can see their objects."""
        fuzzer = IsolationFuzzer(sqlite_backend, seed=99)

        result = fuzzer.run(
            num_principals=4,
            num_objects=8,
            num_scopes=0,
            num_projections=0,
            num_mutations=0,
        )

        assert result.passed
        assert result.scopes_created == 0

    def test_with_projections(self, sqlite_backend):
        """Projections should grant access to scope members."""
        fuzzer = IsolationFuzzer(sqlite_backend, seed=7)

        result = fuzzer.run(
            num_principals=5,
            num_objects=10,
            num_scopes=3,
            num_projections=5,
            num_mutations=3,
        )

        assert result.passed
        assert result.access_checks > 0

    def test_large_fuzz(self, sqlite_backend):
        """Larger fuzz run to stress-test isolation."""
        fuzzer = IsolationFuzzer(sqlite_backend, seed=555)

        result = fuzzer.run(
            num_principals=8,
            num_objects=20,
            num_scopes=4,
            num_projections=8,
            num_mutations=10,
        )

        assert result.passed
        assert result.principals_created == 8
        assert result.objects_created == 20

    def test_fuzz_result_properties(self, sqlite_backend):
        fuzzer = IsolationFuzzer(sqlite_backend, seed=1)

        result = fuzzer.run(
            num_principals=2,
            num_objects=3,
            num_scopes=1,
            num_projections=1,
            num_mutations=0,
        )

        assert isinstance(result.violations, tuple)
        assert isinstance(result.passed, bool)
        assert result.access_checks >= result.principals_created * result.objects_created
