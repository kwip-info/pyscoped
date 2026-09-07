"""Tests for manifest parser."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scoped.manifest.exceptions import ManifestParseError
from scoped.manifest.parser import parse_manifest

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseFromDict:
    def test_minimal(self):
        doc = parse_manifest({
            "scoped": {
                "version": "1.0",
                "namespace": "test",
                "principals": [{"name": "admin", "kind": "user"}],
            }
        })

        assert doc.version == "1.0"
        assert doc.namespace == "test"
        assert len(doc.principals) == 1
        assert doc.principals[0].name == "admin"

    def test_defaults(self):
        doc = parse_manifest({"principals": []})

        assert doc.version == "1.0"
        assert doc.namespace == "default"
        assert doc.principals == []
        assert doc.scopes == []

    def test_top_level_without_scoped_key(self):
        doc = parse_manifest({
            "namespace": "direct",
            "principals": [{"name": "user1", "kind": "user"}],
        })

        assert doc.namespace == "direct"
        assert len(doc.principals) == 1


class TestParseFromFile:
    def test_json_file(self):
        doc = parse_manifest(FIXTURES / "minimal.json")

        assert doc.namespace == "minimal"
        assert len(doc.principals) == 1
        assert doc.principals[0].display_name == "Admin User"

    def test_full_json_file(self):
        doc = parse_manifest(FIXTURES / "full.json")

        assert len(doc.principals) == 3
        assert len(doc.scopes) == 2
        assert len(doc.memberships) == 2
        assert len(doc.objects) == 2
        assert len(doc.rules) == 1
        assert len(doc.environments) == 1
        assert len(doc.pipelines) == 1
        assert len(doc.deployment_targets) == 1
        assert len(doc.secrets) == 1
        assert len(doc.plugins) == 1


class TestParseFromString:
    def test_json_string(self):
        raw = json.dumps({
            "scoped": {
                "principals": [{"name": "x", "kind": "user"}],
            }
        })
        doc = parse_manifest(raw)
        assert len(doc.principals) == 1

    def test_empty_string_raises(self):
        with pytest.raises(ManifestParseError, match="Empty"):
            parse_manifest("")


class TestParseSections:
    def test_principal_defaults(self):
        doc = parse_manifest({
            "principals": [{"name": "admin"}],
        })
        p = doc.principals[0]
        assert p.kind == "user"
        assert p.display_name == "admin"

    def test_scope_requires_owner(self):
        with pytest.raises(ManifestParseError, match="owner"):
            parse_manifest({
                "scopes": [{"name": "s1"}],
            })

    def test_object_requires_type_and_owner(self):
        with pytest.raises(ManifestParseError, match="type"):
            parse_manifest({
                "objects": [{"name": "o1", "owner": "admin"}],
            })

    def test_rule_requires_type_and_effect(self):
        with pytest.raises(ManifestParseError, match="rule_type"):
            parse_manifest({
                "rules": [{"name": "r1", "effect": "deny"}],
            })

    def test_pipeline_stages(self):
        doc = parse_manifest({
            "pipelines": [{
                "name": "p1",
                "owner": "admin",
                "stages": ["build", "test", "deploy"],
            }],
        })
        assert len(doc.pipelines[0].stages) == 3
        assert doc.pipelines[0].stages[0].name == "build"
        assert doc.pipelines[0].stages[2].order == 2

    def test_object_project_into_dict_form(self):
        doc = parse_manifest({
            "objects": [{
                "name": "doc",
                "type": "document",
                "owner": "admin",
                "project_into": [{"scope": "team"}],
            }],
        })
        assert doc.objects[0].project_into == ["team"]

    def test_scope_with_parent(self):
        doc = parse_manifest({
            "scopes": [
                {"name": "parent", "owner": "admin"},
                {"name": "child", "owner": "admin", "parent": "parent"},
            ],
        })
        assert doc.scopes[1].parent == "parent"

    def test_rule_bindings(self):
        doc = parse_manifest({
            "rules": [{
                "name": "r1",
                "rule_type": "access",
                "effect": "deny",
                "bind_to": [
                    {"target_type": "scope", "target": "org"},
                ],
            }],
        })
        assert len(doc.rules[0].bind_to) == 1
        assert doc.rules[0].bind_to[0].target_type == "scope"
