"""Tests for deployment data models."""

from scoped.deployments.models import (
    Deployment,
    DeploymentGate,
    DeploymentState,
    DeploymentTarget,
    GateType,
    TERMINAL_STATES,
)
from scoped.types import Lifecycle, now_utc


class TestDeploymentTarget:

    def test_snapshot(self):
        ts = now_utc()
        t = DeploymentTarget(
            id="t1", name="production", target_type="server",
            owner_id="alice", created_at=ts, config={"url": "https://example.com"},
        )
        snap = t.snapshot()
        assert snap["id"] == "t1"
        assert snap["name"] == "production"
        assert snap["target_type"] == "server"
        assert snap["config"] == {"url": "https://example.com"}
        assert snap["lifecycle"] == "ACTIVE"

    def test_is_active(self):
        t = DeploymentTarget(
            id="t1", name="T", target_type="server",
            owner_id="u", created_at=now_utc(),
        )
        assert t.is_active
        t.lifecycle = Lifecycle.ARCHIVED
        assert not t.is_active

    def test_default_config(self):
        t = DeploymentTarget(
            id="t1", name="T", target_type="server",
            owner_id="u", created_at=now_utc(),
        )
        assert t.config == {}


class TestDeployment:

    def test_snapshot(self):
        ts = now_utc()
        d = Deployment(
            id="d1", target_id="t1", deployed_by="alice",
            version=3, state=DeploymentState.DEPLOYED,
            object_id="obj1", scope_id="s1",
            deployed_at=ts, rollback_of="d0",
            metadata={"env": "prod"},
        )
        snap = d.snapshot()
        assert snap["id"] == "d1"
        assert snap["version"] == 3
        assert snap["state"] == "deployed"
        assert snap["object_id"] == "obj1"
        assert snap["rollback_of"] == "d0"
        assert snap["metadata"] == {"env": "prod"}

    def test_is_terminal(self):
        d = Deployment(id="d1", target_id="t1", deployed_by="u")
        assert not d.is_terminal  # PENDING
        d.state = DeploymentState.DEPLOYING
        assert not d.is_terminal
        d.state = DeploymentState.DEPLOYED
        assert d.is_terminal
        d.state = DeploymentState.FAILED
        assert d.is_terminal
        d.state = DeploymentState.ROLLED_BACK
        assert d.is_terminal

    def test_default_state(self):
        d = Deployment(id="d1", target_id="t1", deployed_by="u")
        assert d.state == DeploymentState.PENDING
        assert d.deployed_at is None
        assert d.rollback_of is None

    def test_snapshot_no_deployed_at(self):
        d = Deployment(id="d1", target_id="t1", deployed_by="u")
        snap = d.snapshot()
        assert snap["deployed_at"] is None


class TestDeploymentGate:

    def test_snapshot(self):
        ts = now_utc()
        g = DeploymentGate(
            id="g1", deployment_id="d1",
            gate_type=GateType.STAGE_CHECK,
            passed=True, checked_at=ts,
            details={"stage": "approved"},
        )
        snap = g.snapshot()
        assert snap["gate_type"] == "stage_check"
        assert snap["passed"] is True
        assert snap["details"] == {"stage": "approved"}

    def test_default_details(self):
        ts = now_utc()
        g = DeploymentGate(
            id="g1", deployment_id="d1",
            gate_type=GateType.APPROVAL,
            passed=False, checked_at=ts,
        )
        assert g.details == {}


class TestEnums:

    def test_deployment_states(self):
        assert DeploymentState.PENDING.value == "pending"
        assert DeploymentState.DEPLOYING.value == "deploying"
        assert DeploymentState.DEPLOYED.value == "deployed"
        assert DeploymentState.FAILED.value == "failed"
        assert DeploymentState.ROLLED_BACK.value == "rolled_back"

    def test_gate_types(self):
        assert GateType.STAGE_CHECK.value == "stage_check"
        assert GateType.RULE_CHECK.value == "rule_check"
        assert GateType.APPROVAL.value == "approval"
        assert GateType.CUSTOM.value == "custom"

    def test_terminal_states(self):
        assert DeploymentState.DEPLOYED in TERMINAL_STATES
        assert DeploymentState.FAILED in TERMINAL_STATES
        assert DeploymentState.ROLLED_BACK in TERMINAL_STATES
        assert DeploymentState.PENDING not in TERMINAL_STATES
        assert DeploymentState.DEPLOYING not in TERMINAL_STATES
