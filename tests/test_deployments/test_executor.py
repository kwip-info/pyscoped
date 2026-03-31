"""Tests for deployment executor."""

import pytest

from scoped.deployments.executor import DeploymentExecutor
from scoped.deployments.gates import GateChecker
from scoped.deployments.models import DeploymentState, GateType
from scoped.exceptions import DeploymentError, DeploymentGateFailedError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    alice = store.create_principal(kind="user", display_name="Alice", principal_id="alice")
    return alice


@pytest.fixture
def executor(sqlite_backend):
    return DeploymentExecutor(sqlite_backend)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def gate_checker(sqlite_backend):
    return GateChecker(sqlite_backend)


class TestTargetCRUD:

    def test_create_target(self, executor, principals):
        t = executor.create_target(
            name="production", target_type="server", owner_id=principals.id,
            config={"url": "https://prod.example.com"},
        )
        assert t.name == "production"
        assert t.target_type == "server"
        assert t.config == {"url": "https://prod.example.com"}
        assert t.is_active

    def test_get_target(self, executor, principals):
        t = executor.create_target(
            name="T", target_type="api", owner_id=principals.id,
        )
        fetched = executor.get_target(t.id)
        assert fetched is not None
        assert fetched.id == t.id
        assert fetched.name == "T"

    def test_get_nonexistent(self, executor):
        assert executor.get_target("nonexistent") is None

    def test_list_targets(self, executor, principals):
        executor.create_target(name="T1", target_type="api", owner_id=principals.id)
        executor.create_target(name="T2", target_type="api", owner_id=principals.id)
        result = executor.list_targets()
        assert len(result) == 2

    def test_list_by_owner(self, executor, principals, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        bob = store.create_principal(kind="user", display_name="Bob", principal_id="bob")
        executor.create_target(name="T1", target_type="api", owner_id=principals.id)
        executor.create_target(name="T2", target_type="api", owner_id=bob.id)
        result = executor.list_targets(owner_id=principals.id)
        assert len(result) == 1

    def test_archive_target(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        executor.archive_target(t.id)
        result = executor.list_targets(active_only=True)
        assert len(result) == 0


class TestDeploymentCRUD:

    def test_create_deployment(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        assert d.target_id == t.id
        assert d.state == DeploymentState.PENDING
        assert d.version == 1

    def test_auto_version_increment(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d1 = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        d2 = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        assert d1.version == 1
        assert d2.version == 2

    def test_create_with_object(self, executor, objects, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        obj, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"t": "x"})
        d = executor.create_deployment(
            target_id=t.id, deployed_by=principals.id,
            object_id=obj.id, metadata={"env": "staging"},
        )
        assert d.object_id == obj.id
        assert d.metadata == {"env": "staging"}

    def test_get_deployment(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        fetched = executor.get_deployment(d.id)
        assert fetched is not None
        assert fetched.id == d.id

    def test_get_nonexistent(self, executor):
        assert executor.get_deployment("nonexistent") is None

    def test_list_deployments(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        result = executor.list_deployments(target_id=t.id)
        assert len(result) == 2

    def test_list_by_state(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d1 = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.transition_state(d1.id, DeploymentState.DEPLOYING)
        result = executor.list_deployments(state=DeploymentState.PENDING)
        assert len(result) == 1


class TestTransitionState:

    def test_pending_to_deploying(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        d = executor.transition_state(d.id, DeploymentState.DEPLOYING)
        assert d.state == DeploymentState.DEPLOYING

    def test_deploying_to_deployed(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.transition_state(d.id, DeploymentState.DEPLOYING)
        d = executor.transition_state(d.id, DeploymentState.DEPLOYED)
        assert d.state == DeploymentState.DEPLOYED
        assert d.deployed_at is not None

    def test_deploying_to_failed(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.transition_state(d.id, DeploymentState.DEPLOYING)
        d = executor.transition_state(d.id, DeploymentState.FAILED)
        assert d.state == DeploymentState.FAILED

    def test_terminal_state_blocks_transition(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.transition_state(d.id, DeploymentState.DEPLOYING)
        executor.transition_state(d.id, DeploymentState.DEPLOYED)
        with pytest.raises(DeploymentError, match="terminal"):
            executor.transition_state(d.id, DeploymentState.DEPLOYING)

    def test_nonexistent_deployment(self, executor):
        with pytest.raises(DeploymentError, match="not found"):
            executor.transition_state("nonexistent", DeploymentState.DEPLOYING)


class TestExecuteDeployment:

    def test_execute_success(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        result = executor.execute_deployment(
            d.id, actor_id=principals.id,
            deploy_fn=lambda dep: True,
        )
        assert result.state == DeploymentState.DEPLOYED

    def test_execute_failure(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        result = executor.execute_deployment(
            d.id, actor_id=principals.id,
            deploy_fn=lambda dep: False,
        )
        assert result.state == DeploymentState.FAILED

    def test_execute_exception(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)

        def raise_error(dep):
            raise RuntimeError("deploy failed")

        result = executor.execute_deployment(
            d.id, actor_id=principals.id, deploy_fn=raise_error,
        )
        assert result.state == DeploymentState.FAILED

    def test_execute_no_fn(self, executor, principals):
        """Without a deploy_fn, just transitions through to deployed."""
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        result = executor.execute_deployment(d.id, actor_id=principals.id)
        assert result.state == DeploymentState.DEPLOYED

    def test_execute_not_pending(self, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.transition_state(d.id, DeploymentState.DEPLOYING)
        with pytest.raises(DeploymentError, match="PENDING"):
            executor.execute_deployment(d.id, actor_id=principals.id)

    def test_execute_blocked_by_gate(self, executor, gate_checker, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        gate_checker.record_gate(
            deployment_id=d.id, gate_type=GateType.APPROVAL, passed=False,
        )
        with pytest.raises(DeploymentGateFailedError):
            executor.execute_deployment(d.id, actor_id=principals.id)

    def test_execute_passes_with_gates(self, executor, gate_checker, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        gate_checker.record_gate(
            deployment_id=d.id, gate_type=GateType.STAGE_CHECK, passed=True,
        )
        gate_checker.record_gate(
            deployment_id=d.id, gate_type=GateType.APPROVAL, passed=True,
        )
        result = executor.execute_deployment(d.id, actor_id=principals.id)
        assert result.state == DeploymentState.DEPLOYED

    def test_execute_nonexistent(self, executor, principals):
        with pytest.raises(DeploymentError, match="not found"):
            executor.execute_deployment("nonexistent", actor_id=principals.id)
