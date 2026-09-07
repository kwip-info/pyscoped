"""Tests for 8C: URN __post_init__ validation."""

import pytest
from scoped.types import URN


class TestURNValidation:
    """Ensure invalid URNs are rejected at construction time."""

    def test_valid_urn(self):
        urn = URN(kind="model", namespace="myapp", name="User", version=1)
        assert str(urn) == "scoped:model:myapp:User:1"

    def test_valid_urn_high_version(self):
        urn = URN(kind="model", namespace="ns", name="X", version=99)
        assert urn.version == 99

    def test_empty_kind_rejected(self):
        with pytest.raises(ValueError, match="kind must be non-empty"):
            URN(kind="", namespace="ns", name="X")

    def test_empty_namespace_rejected(self):
        with pytest.raises(ValueError, match="namespace must be non-empty"):
            URN(kind="model", namespace="", name="X")

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match="name must be non-empty"):
            URN(kind="model", namespace="ns", name="")

    def test_zero_version_rejected(self):
        with pytest.raises(ValueError, match="version must be >= 1"):
            URN(kind="model", namespace="ns", name="X", version=0)

    def test_negative_version_rejected(self):
        with pytest.raises(ValueError, match="version must be >= 1"):
            URN(kind="model", namespace="ns", name="X", version=-1)

    def test_parse_still_works(self):
        urn = URN.parse("scoped:model:myapp:User:1")
        assert urn.kind == "model"
        assert urn.namespace == "myapp"
        assert urn.name == "User"
        assert urn.version == 1

    def test_parse_with_colon_in_name(self):
        urn = URN.parse("scoped:model:myapp:user:abc123:2")
        assert urn.name == "user:abc123"
        assert urn.version == 2

    def test_parse_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid URN"):
            URN.parse("not-a-urn")

    def test_parse_invalid_prefix(self):
        with pytest.raises(ValueError, match="Invalid URN"):
            URN.parse("wrong:model:ns:name:1")

    def test_frozen(self):
        urn = URN(kind="model", namespace="ns", name="X")
        with pytest.raises(AttributeError):
            urn.kind = "other"  # type: ignore[misc]

    def test_hashable(self):
        urn1 = URN(kind="model", namespace="ns", name="X")
        urn2 = URN(kind="model", namespace="ns", name="X")
        assert urn1 == urn2
        assert hash(urn1) == hash(urn2)
        assert len({urn1, urn2}) == 1
