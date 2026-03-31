"""Tests for deployment gate checks."""

import pytest

from scoped.deployments.executor import DeploymentExecutor
from scoped.deployments.gates import GateChecker
from scoped.deployments.models import GateType
from scoped.identity.principal import PrincipalStore


@pytest.fixture
def principals(sqlite_backend, registry):
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Alice", principal_id="alice")


@pytest.fixture
def executor(sqlite_backend):
    return DeploymentExecutor(sqlite_backend)


@pytest.fixture
def checker(sqlite_backend):
    return GateChecker(sqlite_backend)


@pytest.fixture
def deployment(executor, principals):
    t = executor.create_target(name="T", target_type="api", owner_id=principals.id)
    return executor.create_deployment(target_id=t.id, deployed_by=principals.id)


class TestRecordGate:

    def test_record_passing_gate(self, checker, deployment):
        g = checker.record_gate(
            deployment_id=deployment.id,
            gate_type=GateType.STAGE_CHECK,
            passed=True,
            details={"stage": "approved"},
        )
        assert g.passed is True
        assert g.gate_type == GateType.STAGE_CHECK
        assert g.details == {"stage": "approved"}

    def test_record_failing_gate(self, checker, deployment):
        g = checker.record_gate(
            deployment_id=deployment.id,
            gate_type=GateType.APPROVAL,
            passed=False,
            details={"approver": "bob", "status": "denied"},
        )
        assert g.passed is False

    def test_record_multiple_gates(self, checker, deployment):
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.STAGE_CHECK, passed=True,
        )
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.RULE_CHECK, passed=True,
        )
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.APPROVAL, passed=True,
        )
        gates = checker.get_gates(deployment.id)
        assert len(gates) == 3

    def test_custom_gate(self, checker, deployment):
        g = checker.record_gate(
            deployment_id=deployment.id,
            gate_type=GateType.CUSTOM,
            passed=True,
            details={"check": "security_scan", "result": "clean"},
        )
        assert g.gate_type == GateType.CUSTOM


class TestGetGates:

    def test_get_gates(self, checker, deployment):
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.STAGE_CHECK, passed=True,
        )
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.APPROVAL, passed=False,
        )
        gates = checker.get_gates(deployment.id)
        assert len(gates) == 2

    def test_get_gates_empty(self, checker, deployment):
        gates = checker.get_gates(deployment.id)
        assert gates == []


class TestCheckAllPassed:

    def test_all_passed(self, checker, deployment):
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.STAGE_CHECK, passed=True,
        )
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.APPROVAL, passed=True,
        )
        result = checker.check_all_passed(deployment.id)
        assert result.all_passed is True
        assert result.failed_count == 0
        assert len(result.gates) == 2

    def test_some_failed(self, checker, deployment):
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.STAGE_CHECK, passed=True,
        )
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.APPROVAL, passed=False,
        )
        result = checker.check_all_passed(deployment.id)
        assert result.all_passed is False
        assert result.failed_count == 1

    def test_no_gates_means_not_passed(self, checker, deployment):
        """check_all_passed requires at least one gate."""
        result = checker.check_all_passed(deployment.id)
        assert result.all_passed is False

    def test_no_gates_passes_with_or_none(self, checker, deployment):
        """check_all_passed_or_none passes when no gates exist."""
        result = checker.check_all_passed_or_none(deployment.id)
        assert result.all_passed is True

    def test_or_none_fails_with_failed_gate(self, checker, deployment):
        checker.record_gate(
            deployment_id=deployment.id, gate_type=GateType.APPROVAL, passed=False,
        )
        result = checker.check_all_passed_or_none(deployment.id)
        assert result.all_passed is False
        assert result.failed_count == 1
