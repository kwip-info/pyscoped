"""Tests for deployment rollback."""

import pytest

from scoped.deployments.executor import DeploymentExecutor
from scoped.deployments.models import DeploymentState
from scoped.deployments.rollback import DeploymentRollbackManager
from scoped.exceptions import DeploymentRollbackError
from scoped.identity.principal import PrincipalStore
from scoped.objects.manager import ScopedManager


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def executor(sqlite_backend):
    return DeploymentExecutor(sqlite_backend)


@pytest.fixture
def objects(sqlite_backend):
    return ScopedManager(sqlite_backend)


@pytest.fixture
def rollbacks(sqlite_backend, executor):
    return DeploymentRollbackManager(sqlite_backend, executor)


@pytest.fixture
def deployed(executor, objects, principals):
    """A deployment in DEPLOYED state."""
    obj, _ = objects.create(object_type="Doc", owner_id=principals.id, data={"t": "x"})
    t = executor.create_target(name="prod", target_type="server", owner_id=principals.id)
    d = executor.create_deployment(
        target_id=t.id, deployed_by=principals.id, object_id=obj.id,
    )
    executor.transition_state(d.id, DeploymentState.DEPLOYING)
    executor.transition_state(d.id, DeploymentState.DEPLOYED)
    return executor.get_deployment(d.id)


class TestRollbackDeployment:

    def test_basic_rollback(self, rollbacks, deployed, principals):
        rb = rollbacks.rollback_deployment(deployed.id, actor_id=principals.id)
        assert rb.rollback_of == deployed.id
        assert rb.target_id == deployed.target_id
        assert rb.object_id == deployed.object_id
        assert rb.state == DeploymentState.PENDING

    def test_original_marked_rolled_back(self, rollbacks, executor, deployed, principals):
        rollbacks.rollback_deployment(deployed.id, actor_id=principals.id)
        original = executor.get_deployment(deployed.id)
        assert original.state == DeploymentState.ROLLED_BACK

    def test_rollback_increments_version(self, rollbacks, deployed, principals):
        rb = rollbacks.rollback_deployment(deployed.id, actor_id=principals.id)
        assert rb.version == deployed.version + 1

    def test_rollback_with_metadata(self, rollbacks, deployed, principals):
        rb = rollbacks.rollback_deployment(
            deployed.id, actor_id=principals.id,
            metadata={"reason": "regression found"},
        )
        assert rb.metadata == {"reason": "regression found"}

    def test_rollback_nonexistent(self, rollbacks, principals):
        with pytest.raises(DeploymentRollbackError, match="not found"):
            rollbacks.rollback_deployment("nonexistent", actor_id=principals.id)

    def test_rollback_pending(self, rollbacks, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        with pytest.raises(DeploymentRollbackError, match="pending"):
            rollbacks.rollback_deployment(d.id, actor_id=principals.id)

    def test_rollback_failed(self, rollbacks, executor, principals):
        t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
        d = executor.create_deployment(target_id=t.id, deployed_by=principals.id)
        executor.transition_state(d.id, DeploymentState.DEPLOYING)
        executor.transition_state(d.id, DeploymentState.FAILED)
        with pytest.raises(DeploymentRollbackError, match="failed"):
            rollbacks.rollback_deployment(d.id, actor_id=principals.id)


class TestRollbackChain:

    def test_single_deployment(self, rollbacks, deployed):
        chain = rollbacks.get_rollback_chain(deployed.id)
        assert len(chain) == 1
        assert chain[0].id == deployed.id

    def test_chain_with_rollback(self, rollbacks, deployed, principals):
        rb = rollbacks.rollback_deployment(deployed.id, actor_id=principals.id)
        chain = rollbacks.get_rollback_chain(deployed.id)
        assert len(chain) == 2
        assert chain[0].id == deployed.id
        assert chain[1].id == rb.id

    def test_deep_chain(self, rollbacks, executor, deployed, principals):
        """Deploy → rollback → execute rollback → rollback the rollback."""
        # First rollback
        rb1 = rollbacks.rollback_deployment(deployed.id, actor_id=principals.id)
        # Execute the rollback deployment
        executor.transition_state(rb1.id, DeploymentState.DEPLOYING)
        executor.transition_state(rb1.id, DeploymentState.DEPLOYED)
        # Rollback the rollback
        rb2 = rollbacks.rollback_deployment(rb1.id, actor_id=principals.id)

        # Chain from original: original → rb1 → rb2
        chain = rollbacks.get_rollback_chain(deployed.id)
        assert len(chain) == 3
        assert chain[0].id == deployed.id
        assert chain[1].id == rb1.id
        assert chain[2].id == rb2.id

    def test_chain_nonexistent(self, rollbacks):
        chain = rollbacks.get_rollback_chain("nonexistent")
        assert chain == []
