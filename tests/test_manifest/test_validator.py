"""Tests for manifest validator."""

from __future__ import annotations

import pytest

from scoped.manifest.exceptions import ManifestValidationError
from scoped.manifest.parser import parse_manifest
from scoped.manifest.validator import validate_manifest, validate_or_raise


class TestValidReferences:
    def test_minimal_valid(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "scopes": [{"name": "org", "owner": "admin"}],
        })
        errors = validate_manifest(doc)
        assert errors == []

    def test_full_valid(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}, {"name": "alice"}],
            "scopes": [
                {"name": "org", "owner": "admin"},
                {"name": "team", "owner": "admin", "parent": "org"},
            ],
            "memberships": [
                {"scope": "org", "principal": "alice", "granted_by": "admin"},
            ],
            "objects": [
                {"name": "doc", "type": "document", "owner": "admin", "project_into": ["org"]},
            ],
            "rules": [
                {"name": "r1", "rule_type": "access", "effect": "deny", "created_by": "admin",
                 "bind_to": [{"target_type": "scope", "target": "org"}]},
            ],
            "environments": [{"name": "staging", "owner": "admin"}],
            "pipelines": [{"name": "p1", "owner": "admin"}],
            "deployment_targets": [{"name": "prod", "target_type": "k8s", "owner": "admin"}],
            "secrets": [{"name": "key", "owner": "admin"}],
            "plugins": [{"name": "plug", "owner": "admin", "scope": "org"}],
        })
        errors = validate_manifest(doc)
        assert errors == []


class TestDanglingReferences:
    def test_scope_owner_missing(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "scopes": [{"name": "org", "owner": "nonexistent"}],
        })
        errors = validate_manifest(doc)
        assert any("nonexistent" in e for e in errors)

    def test_scope_parent_missing(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "scopes": [{"name": "child", "owner": "admin", "parent": "missing"}],
        })
        errors = validate_manifest(doc)
        assert any("missing" in e for e in errors)

    def test_membership_refs_missing(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "scopes": [{"name": "org", "owner": "admin"}],
            "memberships": [{"scope": "org", "principal": "ghost"}],
        })
        errors = validate_manifest(doc)
        assert any("ghost" in e for e in errors)

    def test_object_owner_missing(self):
        doc = parse_manifest({
            "objects": [{"name": "doc", "type": "document", "owner": "nobody"}],
        })
        errors = validate_manifest(doc)
        assert any("nobody" in e for e in errors)

    def test_object_project_into_missing(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "objects": [{"name": "doc", "type": "document", "owner": "admin", "project_into": ["nope"]}],
        })
        errors = validate_manifest(doc)
        assert any("nope" in e for e in errors)

    def test_rule_binding_missing(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "rules": [{"name": "r1", "rule_type": "access", "effect": "deny",
                        "created_by": "admin",
                        "bind_to": [{"target_type": "scope", "target": "gone"}]}],
        })
        errors = validate_manifest(doc)
        assert any("gone" in e for e in errors)

    def test_plugin_scope_missing(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "plugins": [{"name": "plug", "owner": "admin", "scope": "nowhere"}],
        })
        errors = validate_manifest(doc)
        assert any("nowhere" in e for e in errors)


class TestDuplicates:
    def test_duplicate_principal_names(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}, {"name": "admin"}],
        })
        errors = validate_manifest(doc)
        assert any("duplicate" in e.lower() for e in errors)

    def test_duplicate_scope_names(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "scopes": [
                {"name": "org", "owner": "admin"},
                {"name": "org", "owner": "admin"},
            ],
        })
        errors = validate_manifest(doc)
        assert any("duplicate" in e.lower() for e in errors)


class TestCycles:
    def test_scope_parent_cycle(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
            "scopes": [
                {"name": "a", "owner": "admin", "parent": "b"},
                {"name": "b", "owner": "admin", "parent": "a"},
            ],
        })
        errors = validate_manifest(doc)
        assert any("cycle" in e.lower() for e in errors)


class TestValidateOrRaise:
    def test_raises_on_errors(self):
        doc = parse_manifest({
            "scopes": [{"name": "org", "owner": "nobody"}],
        })
        with pytest.raises(ManifestValidationError) as exc_info:
            validate_or_raise(doc)
        assert len(exc_info.value.errors) >= 1

    def test_passes_when_valid(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
        })
        validate_or_raise(doc)  # Should not raise
