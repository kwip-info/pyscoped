"""Tests for contracts & schema validation."""

import pytest

from scoped.exceptions import ContractNotFoundError, ContractValidationError
from scoped.identity.principal import PrincipalStore
from scoped.registry.contracts import (
    Contract,
    ContractConstraint,
    ContractField,
    ContractStore,
    ContractVersion,
    FieldType,
    ValidationResult,
    contract_from_row,
    contract_version_from_row,
    diff_contracts,
    validate_against_version,
)
from scoped.types import Lifecycle


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return alice


@pytest.fixture
def contract_store(sqlite_backend):
    return ContractStore(sqlite_backend)


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestContractField:

    def test_snapshot(self):
        f = ContractField(
            name="title", field_type=FieldType.STRING,
            required=True, constraints={"max_length": 255},
        )
        snap = f.snapshot()
        assert snap["name"] == "title"
        assert snap["field_type"] == "string"
        assert snap["constraints"]["max_length"] == 255

    def test_from_dict(self):
        f = ContractField.from_dict({
            "name": "count",
            "field_type": "integer",
            "required": False,
            "default": 0,
        })
        assert f.name == "count"
        assert f.field_type == FieldType.INTEGER
        assert not f.required
        assert f.default == 0

    def test_roundtrip(self):
        original = ContractField(
            name="tags", field_type=FieldType.LIST,
            description="Item tags",
        )
        restored = ContractField.from_dict(original.snapshot())
        assert restored.name == original.name
        assert restored.field_type == original.field_type


class TestContractConstraint:

    def test_snapshot(self):
        c = ContractConstraint(
            name="either_email_or_phone",
            constraint_type="at_least_one",
            config={"fields": ["email", "phone"]},
        )
        snap = c.snapshot()
        assert snap["constraint_type"] == "at_least_one"
        assert snap["config"]["fields"] == ["email", "phone"]

    def test_from_dict(self):
        c = ContractConstraint.from_dict({
            "name": "dep",
            "constraint_type": "depends_on",
            "config": {"field": "zip", "depends_on": "country"},
        })
        assert c.constraint_type == "depends_on"


class TestContractVersion:

    def test_field_names(self):
        cv = ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=(
                ContractField(name="title", field_type=FieldType.STRING),
                ContractField(name="count", field_type=FieldType.INTEGER),
            ),
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            created_by="alice",
        )
        assert cv.field_names == frozenset({"title", "count"})

    def test_required_fields(self):
        cv = ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=(
                ContractField(name="title", field_type=FieldType.STRING, required=True),
                ContractField(name="note", field_type=FieldType.STRING, required=False),
            ),
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            created_by="alice",
        )
        assert cv.required_fields == frozenset({"title"})

    def test_get_field(self):
        cv = ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=(ContractField(name="title", field_type=FieldType.STRING),),
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            created_by="alice",
        )
        assert cv.get_field("title") is not None
        assert cv.get_field("nope") is None


class TestContract:

    def test_is_active(self):
        from datetime import datetime, timezone
        c = Contract(
            id="c1", name="test", object_type="Document",
            owner_id="alice", created_at=datetime.now(timezone.utc),
        )
        assert c.is_active
        c.lifecycle = Lifecycle.ARCHIVED
        assert not c.is_active

    def test_snapshot(self):
        from datetime import datetime, timezone
        c = Contract(
            id="c1", name="test", object_type="Document",
            owner_id="alice", created_at=datetime.now(timezone.utc),
            metadata={"key": "val"},
        )
        snap = c.snapshot()
        assert snap["object_type"] == "Document"
        assert snap["metadata"] == {"key": "val"}


class TestRowMapping:

    def test_contract_from_row(self):
        row = {
            "id": "c1", "name": "Doc Contract", "description": "Test",
            "object_type": "Document", "owner_id": "alice",
            "current_version": 2, "created_at": "2026-01-01T00:00:00+00:00",
            "lifecycle": "ACTIVE", "metadata_json": "{}",
        }
        c = contract_from_row(row)
        assert c.name == "Doc Contract"
        assert c.current_version == 2

    def test_contract_version_from_row(self):
        import json
        fields = [{"name": "title", "field_type": "string", "required": True}]
        constraints = [{"name": "c1", "constraint_type": "at_least_one", "config": {"fields": ["a"]}}]
        row = {
            "id": "v1", "contract_id": "c1", "version": 1,
            "fields_json": json.dumps(fields),
            "constraints_json": json.dumps(constraints),
            "created_at": "2026-01-01T00:00:00+00:00",
            "created_by": "alice", "change_reason": "",
        }
        cv = contract_version_from_row(row)
        assert len(cv.fields) == 1
        assert cv.fields[0].name == "title"
        assert len(cv.constraints) == 1


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidation:

    def _make_version(self, fields, constraints=()):
        from datetime import datetime, timezone
        return ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=tuple(fields), constraints=tuple(constraints),
            created_at=datetime.now(timezone.utc), created_by="alice",
        )

    def test_valid_data(self):
        cv = self._make_version([
            ContractField(name="title", field_type=FieldType.STRING),
            ContractField(name="count", field_type=FieldType.INTEGER),
        ])
        result = validate_against_version({"title": "Hello", "count": 5}, cv)
        assert result.valid
        assert result.errors == ()

    def test_missing_required_field(self):
        cv = self._make_version([
            ContractField(name="title", field_type=FieldType.STRING, required=True),
        ])
        result = validate_against_version({}, cv)
        assert not result.valid
        assert "Missing required" in result.errors[0]

    def test_optional_field_missing_ok(self):
        cv = self._make_version([
            ContractField(name="note", field_type=FieldType.STRING, required=False),
        ])
        result = validate_against_version({}, cv)
        assert result.valid

    def test_wrong_type(self):
        cv = self._make_version([
            ContractField(name="count", field_type=FieldType.INTEGER),
        ])
        result = validate_against_version({"count": "not a number"}, cv)
        assert not result.valid
        assert "expected integer" in result.errors[0]

    def test_float_accepts_int(self):
        cv = self._make_version([
            ContractField(name="price", field_type=FieldType.FLOAT),
        ])
        result = validate_against_version({"price": 42}, cv)
        assert result.valid

    def test_unknown_field(self):
        cv = self._make_version([
            ContractField(name="title", field_type=FieldType.STRING),
        ])
        result = validate_against_version({"title": "ok", "extra": True}, cv)
        assert not result.valid
        assert "Unknown field" in result.errors[0]

    def test_min_length_constraint(self):
        cv = self._make_version([
            ContractField(
                name="name", field_type=FieldType.STRING,
                constraints={"min_length": 3},
            ),
        ])
        result = validate_against_version({"name": "ab"}, cv)
        assert not result.valid
        assert "min_length" in result.errors[0]

    def test_max_length_constraint(self):
        cv = self._make_version([
            ContractField(
                name="name", field_type=FieldType.STRING,
                constraints={"max_length": 5},
            ),
        ])
        result = validate_against_version({"name": "toolong"}, cv)
        assert not result.valid

    def test_min_value_constraint(self):
        cv = self._make_version([
            ContractField(
                name="age", field_type=FieldType.INTEGER,
                constraints={"min_value": 0},
            ),
        ])
        result = validate_against_version({"age": -1}, cv)
        assert not result.valid

    def test_max_value_constraint(self):
        cv = self._make_version([
            ContractField(
                name="rating", field_type=FieldType.INTEGER,
                constraints={"max_value": 5},
            ),
        ])
        result = validate_against_version({"rating": 6}, cv)
        assert not result.valid

    def test_pattern_constraint(self):
        cv = self._make_version([
            ContractField(
                name="email", field_type=FieldType.STRING,
                constraints={"pattern": r"^[\w.]+@[\w.]+$"},
            ),
        ])
        assert validate_against_version({"email": "a@b.com"}, cv).valid
        assert not validate_against_version({"email": "invalid"}, cv).valid

    def test_choices_constraint(self):
        cv = self._make_version([
            ContractField(
                name="status", field_type=FieldType.STRING,
                constraints={"choices": ["draft", "active", "archived"]},
            ),
        ])
        assert validate_against_version({"status": "draft"}, cv).valid
        assert not validate_against_version({"status": "deleted"}, cv).valid

    def test_at_least_one_constraint(self):
        cv = self._make_version(
            [
                ContractField(name="email", field_type=FieldType.STRING, required=False),
                ContractField(name="phone", field_type=FieldType.STRING, required=False),
            ],
            [ContractConstraint(
                name="contact_required",
                constraint_type="at_least_one",
                config={"fields": ["email", "phone"]},
            )],
        )
        assert not validate_against_version({}, cv).valid
        assert validate_against_version({"email": "a@b.com"}, cv).valid
        assert validate_against_version({"phone": "555-1234"}, cv).valid

    def test_depends_on_constraint(self):
        cv = self._make_version(
            [
                ContractField(name="zip", field_type=FieldType.STRING, required=False),
                ContractField(name="country", field_type=FieldType.STRING, required=False),
            ],
            [ContractConstraint(
                name="zip_needs_country",
                constraint_type="depends_on",
                config={"field": "zip", "depends_on": "country"},
            )],
        )
        assert validate_against_version({"zip": "12345", "country": "US"}, cv).valid
        assert not validate_against_version({"zip": "12345"}, cv).valid
        assert validate_against_version({"country": "US"}, cv).valid

    def test_mutually_exclusive_constraint(self):
        cv = self._make_version(
            [
                ContractField(name="ssn", field_type=FieldType.STRING, required=False),
                ContractField(name="passport", field_type=FieldType.STRING, required=False),
            ],
            [ContractConstraint(
                name="one_id",
                constraint_type="mutually_exclusive",
                config={"fields": ["ssn", "passport"]},
            )],
        )
        assert validate_against_version({"ssn": "123"}, cv).valid
        assert not validate_against_version({"ssn": "123", "passport": "456"}, cv).valid

    def test_raise_if_invalid(self):
        result = ValidationResult(valid=False, errors=("bad field",))
        with pytest.raises(ContractValidationError, match="bad field"):
            result.raise_if_invalid()

    def test_raise_if_valid_noop(self):
        result = ValidationResult(valid=True)
        result.raise_if_invalid()  # should not raise

    def test_required_with_default_not_missing(self):
        cv = self._make_version([
            ContractField(
                name="status", field_type=FieldType.STRING,
                required=True, default="draft",
            ),
        ])
        # Has a default, so missing is OK even though required=True
        result = validate_against_version({}, cv)
        assert result.valid


# ---------------------------------------------------------------------------
# ContractStore tests
# ---------------------------------------------------------------------------

class TestContractStore:

    def test_create_contract(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Document Schema",
            object_type="Document",
            owner_id=alice.id,
            fields=[
                ContractField(name="title", field_type=FieldType.STRING),
                ContractField(name="body", field_type=FieldType.STRING, required=False),
            ],
        )
        assert c.name == "Document Schema"
        assert c.object_type == "Document"
        assert c.current_version == 1

    def test_get_contract(self, contract_store, principals):
        alice = principals
        created = contract_store.create_contract(
            name="Test", object_type="Test",
            owner_id=alice.id, fields=[],
        )
        fetched = contract_store.get_contract(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_nonexistent(self, contract_store):
        assert contract_store.get_contract("nope") is None

    def test_get_or_raise(self, contract_store):
        with pytest.raises(ContractNotFoundError):
            contract_store.get_contract_or_raise("nope")

    def test_get_contract_for_type(self, contract_store, principals):
        alice = principals
        contract_store.create_contract(
            name="Doc", object_type="Document",
            owner_id=alice.id,
            fields=[ContractField(name="title", field_type=FieldType.STRING)],
        )
        c = contract_store.get_contract_for_type("Document")
        assert c is not None
        assert c.object_type == "Document"

    def test_get_contract_for_type_none(self, contract_store):
        assert contract_store.get_contract_for_type("Unknown") is None

    def test_list_contracts(self, contract_store, principals):
        alice = principals
        contract_store.create_contract(
            name="A", object_type="A", owner_id=alice.id, fields=[],
        )
        contract_store.create_contract(
            name="B", object_type="B", owner_id=alice.id, fields=[],
        )
        result = contract_store.list_contracts()
        assert len(result) == 2

    def test_list_by_owner(self, contract_store, principals):
        alice = principals
        contract_store.create_contract(
            name="A", object_type="A", owner_id=alice.id, fields=[],
        )
        result = contract_store.list_contracts(owner_id=alice.id)
        assert len(result) == 1

    def test_list_by_type(self, contract_store, principals):
        alice = principals
        contract_store.create_contract(
            name="A", object_type="Doc", owner_id=alice.id, fields=[],
        )
        contract_store.create_contract(
            name="B", object_type="Report", owner_id=alice.id, fields=[],
        )
        result = contract_store.list_contracts(object_type="Doc")
        assert len(result) == 1

    def test_get_version(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Test", object_type="Test",
            owner_id=alice.id,
            fields=[ContractField(name="x", field_type=FieldType.STRING)],
        )
        v = contract_store.get_version(c.id)
        assert v is not None
        assert v.version == 1
        assert len(v.fields) == 1

    def test_get_specific_version(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Test", object_type="Test",
            owner_id=alice.id,
            fields=[ContractField(name="x", field_type=FieldType.STRING)],
        )
        v = contract_store.get_version(c.id, version=1)
        assert v is not None
        assert v.version == 1

    def test_update_contract(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Test", object_type="Test",
            owner_id=alice.id,
            fields=[ContractField(name="x", field_type=FieldType.STRING)],
        )
        v2 = contract_store.update_contract(
            c.id,
            fields=[
                ContractField(name="x", field_type=FieldType.STRING),
                ContractField(name="y", field_type=FieldType.INTEGER),
            ],
            actor_id=alice.id,
            change_reason="Added y field",
        )
        assert v2.version == 2
        assert len(v2.fields) == 2

        # Contract version number updated
        updated = contract_store.get_contract(c.id)
        assert updated.current_version == 2

    def test_get_all_versions(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Test", object_type="Test",
            owner_id=alice.id,
            fields=[ContractField(name="x", field_type=FieldType.STRING)],
        )
        contract_store.update_contract(
            c.id, fields=[ContractField(name="y", field_type=FieldType.INTEGER)],
            actor_id=alice.id,
        )
        versions = contract_store.get_all_versions(c.id)
        assert len(versions) == 2
        assert versions[0].version == 1
        assert versions[1].version == 2

    def test_deprecate(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Old", object_type="Old",
            owner_id=alice.id, fields=[],
        )
        deprecated = contract_store.deprecate(c.id)
        assert deprecated.lifecycle == Lifecycle.DEPRECATED

    def test_archive(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Gone", object_type="Gone",
            owner_id=alice.id, fields=[],
        )
        archived = contract_store.archive(c.id)
        assert archived.lifecycle == Lifecycle.ARCHIVED

    def test_archived_not_in_active_list(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Gone", object_type="Gone",
            owner_id=alice.id, fields=[],
        )
        contract_store.archive(c.id)
        result = contract_store.list_contracts(active_only=True)
        assert len(result) == 0

    def test_validate(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Doc", object_type="Document",
            owner_id=alice.id,
            fields=[
                ContractField(name="title", field_type=FieldType.STRING),
                ContractField(name="count", field_type=FieldType.INTEGER, required=False),
            ],
        )
        result = contract_store.validate({"title": "Hello"}, c.id)
        assert result.valid

    def test_validate_invalid(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Doc", object_type="Document",
            owner_id=alice.id,
            fields=[
                ContractField(name="title", field_type=FieldType.STRING),
            ],
        )
        result = contract_store.validate({}, c.id)
        assert not result.valid

    def test_validate_for_type(self, contract_store, principals):
        alice = principals
        contract_store.create_contract(
            name="Doc", object_type="Document",
            owner_id=alice.id,
            fields=[
                ContractField(name="title", field_type=FieldType.STRING),
            ],
        )
        result = contract_store.validate_for_type({"title": "Hi"}, "Document")
        assert result.valid

    def test_validate_for_type_no_contract(self, contract_store):
        result = contract_store.validate_for_type({"anything": True}, "Unknown")
        assert result.valid  # no contract = no constraints

    def test_validate_nonexistent_contract(self, contract_store):
        with pytest.raises(ContractNotFoundError):
            contract_store.validate({"x": 1}, "nonexistent")

    def test_create_with_constraints(self, contract_store, principals):
        alice = principals
        c = contract_store.create_contract(
            name="Contact", object_type="Contact",
            owner_id=alice.id,
            fields=[
                ContractField(name="email", field_type=FieldType.STRING, required=False),
                ContractField(name="phone", field_type=FieldType.STRING, required=False),
            ],
            constraints=[
                ContractConstraint(
                    name="need_contact",
                    constraint_type="at_least_one",
                    config={"fields": ["email", "phone"]},
                ),
            ],
        )
        v = contract_store.get_version(c.id)
        assert len(v.constraints) == 1

        # Validate with constraint
        result = contract_store.validate({}, c.id)
        assert not result.valid

        result = contract_store.validate({"email": "a@b.com"}, c.id)
        assert result.valid


# ---------------------------------------------------------------------------
# Diff tests
# ---------------------------------------------------------------------------

class TestContractDiff:

    def test_diff_added_field(self):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc)
        old = ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=(ContractField(name="title", field_type=FieldType.STRING),),
            created_at=ts, created_by="alice",
        )
        new = ContractVersion(
            id="v2", contract_id="c1", version=2,
            fields=(
                ContractField(name="title", field_type=FieldType.STRING),
                ContractField(name="body", field_type=FieldType.STRING),
            ),
            created_at=ts, created_by="alice",
        )
        diff = diff_contracts(old, new)
        assert diff["added_fields"] == ["body"]
        assert diff["removed_fields"] == []
        assert diff["modified_fields"] == []

    def test_diff_removed_field(self):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc)
        old = ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=(
                ContractField(name="title", field_type=FieldType.STRING),
                ContractField(name="body", field_type=FieldType.STRING),
            ),
            created_at=ts, created_by="alice",
        )
        new = ContractVersion(
            id="v2", contract_id="c1", version=2,
            fields=(ContractField(name="title", field_type=FieldType.STRING),),
            created_at=ts, created_by="alice",
        )
        diff = diff_contracts(old, new)
        assert diff["added_fields"] == []
        assert diff["removed_fields"] == ["body"]

    def test_diff_modified_field(self):
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc)
        old = ContractVersion(
            id="v1", contract_id="c1", version=1,
            fields=(ContractField(name="title", field_type=FieldType.STRING, required=True),),
            created_at=ts, created_by="alice",
        )
        new = ContractVersion(
            id="v2", contract_id="c1", version=2,
            fields=(ContractField(name="title", field_type=FieldType.STRING, required=False),),
            created_at=ts, created_by="alice",
        )
        diff = diff_contracts(old, new)
        assert diff["modified_fields"] == ["title"]
