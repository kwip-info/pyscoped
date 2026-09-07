"""Tests for ManifestLoader — end-to-end loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from scoped.manifest.exceptions import ManifestLoadError, ManifestValidationError
from scoped.manifest.loader import ManifestLoader

FIXTURES = Path(__file__).parent / "fixtures"


class TestMinimalLoad:
    def test_load_minimal(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(FIXTURES / "minimal.json")

        assert result.ok
        assert ("principals", "admin") in result.created
        assert ("scopes", "default-scope") in result.created
        assert len(result.errors) == 0

    def test_resolver_has_ids(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(FIXTURES / "minimal.json")

        assert result.resolver.has("principals", "admin")
        assert result.resolver.has("scopes", "default-scope")

        # Verify IDs actually exist in DB
        pid = result.resolver.resolve("principals", "admin")
        row = sqlite_backend.fetch_one(
            "SELECT id FROM principals WHERE id = ?", (pid,)
        )
        assert row is not None


class TestFullLoad:
    def test_load_full(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={"db-password": "s3cret"},
        )

        assert result.ok, f"Errors: {result.errors}"

        # Verify counts
        created_sections = [s for s, _ in result.created]
        assert created_sections.count("principals") == 3
        assert created_sections.count("scopes") == 2
        assert created_sections.count("objects") == 2
        assert created_sections.count("rules") == 1
        assert created_sections.count("environments") == 1
        assert created_sections.count("pipelines") == 1
        assert created_sections.count("deployment_targets") == 1
        assert created_sections.count("secrets") == 1
        assert created_sections.count("plugins") == 1

    def test_memberships_created(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={"db-password": "s3cret"},
        )

        created_names = [n for s, n in result.created if s == "memberships"]
        assert "org/alice" in created_names
        assert "team/bob" in created_names

    def test_object_projected(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={"db-password": "s3cret"},
        )

        # doc-1 should be projected into org
        obj_id = result.resolver.resolve("objects", "doc-1")
        scope_id = result.resolver.resolve("scopes", "org")
        row = sqlite_backend.fetch_one(
            "SELECT id FROM scope_projections WHERE scope_id = ? AND object_id = ?",
            (scope_id, obj_id),
        )
        assert row is not None

    def test_pipeline_has_stages(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={"db-password": "s3cret"},
        )

        pipeline_id = result.resolver.resolve("pipelines", "deploy-pipeline")
        rows = sqlite_backend.fetch_all(
            "SELECT name FROM stages WHERE pipeline_id = ? ORDER BY ordinal",
            (pipeline_id,),
        )
        assert len(rows) == 3
        assert rows[0]["name"] == "build"
        assert rows[2]["name"] == "deploy"

    def test_rule_bound_to_scope(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={"db-password": "s3cret"},
        )

        rule_id = result.resolver.resolve("rules", "deny-external")
        scope_id = result.resolver.resolve("scopes", "org")
        row = sqlite_backend.fetch_one(
            "SELECT id FROM rule_bindings WHERE rule_id = ? AND target_id = ?",
            (rule_id, scope_id),
        )
        assert row is not None

    def test_scope_parent_hierarchy(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={"db-password": "s3cret"},
        )

        org_id = result.resolver.resolve("scopes", "org")
        team_id = result.resolver.resolve("scopes", "team")

        row = sqlite_backend.fetch_one(
            "SELECT parent_scope_id FROM scopes WHERE id = ?", (team_id,)
        )
        assert row["parent_scope_id"] == org_id


class TestDictLoad:
    def test_load_from_dict(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load({
            "principals": [{"name": "admin", "kind": "user"}],
        })

        assert result.ok
        assert len(result.created) == 1


class TestDryRun:
    def test_dry_run_creates_nothing(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(FIXTURES / "minimal.json", dry_run=True)

        assert result.ok
        assert len(result.created) == 0

        # Verify nothing in DB
        rows = sqlite_backend.fetch_all("SELECT id FROM principals", ())
        assert len(rows) == 0


class TestValidation:
    def test_validate_returns_errors(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        errors = loader.validate(FIXTURES / "invalid_ref.json")

        assert len(errors) >= 1
        assert any("nonexistent" in e for e in errors)

    def test_load_invalid_ref_raises(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)

        with pytest.raises(ManifestValidationError):
            loader.load(FIXTURES / "invalid_ref.json")


class TestSecretsMissing:
    def test_missing_secret_value_errors(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        result = loader.load(
            FIXTURES / "full.json",
            secret_values={},  # no db-password provided
        )

        assert not result.ok
        assert any("db-password" in e for e in result.errors)


class TestIdempotency:
    def test_load_twice_skips_duplicates(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        manifest = FIXTURES / "minimal.json"

        result1 = loader.load(manifest)
        assert len(result1.created) == 2  # admin + default-scope

        result2 = loader.load(manifest)
        assert len(result2.skipped) == 2  # admin + default-scope
        assert len(result2.created) == 0

    def test_idempotent_principal(self, sqlite_backend):
        loader = ManifestLoader(sqlite_backend)
        manifest = {
            "principals": [{"name": "admin", "kind": "user", "display_name": "Admin User"}],
        }

        loader.load(manifest)
        result = loader.load(manifest)

        assert ("principals", "admin") in result.skipped

        # Only one principal in DB
        rows = sqlite_backend.fetch_all(
            "SELECT id FROM principals WHERE display_name = 'Admin User'", ()
        )
        assert len(rows) == 1
