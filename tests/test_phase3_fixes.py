"""Tests for Phase 3: webhook delivery, job execution, connector federation."""

from datetime import datetime, timedelta, timezone

import pytest

from scoped.connector.bridge import ConnectorManager
from scoped.connector.models import ConnectorDirection, ConnectorState, TrafficStatus
from scoped.events.models import DeliveryStatus, Event, EventType, WebhookEndpoint
from scoped.events.webhooks import WebhookDelivery
from scoped.identity.principal import PrincipalStore
from scoped.scheduling.models import JobState
from scoped.scheduling.queue import JobQueue
from scoped.scheduling.scheduler import Scheduler
from scoped.types import Lifecycle


@pytest.fixture
def principal(sqlite_backend, registry):
    """Create a test principal for FK constraints."""
    store = PrincipalStore(sqlite_backend)
    return store.create_principal(kind="user", display_name="Tester", principal_id="u1")


# =============================================================================
# 1. Webhook delivery — HTTP transport + backoff
# =============================================================================


class TestWebhookHTTPTransport:

    def test_http_transport_exists(self):
        assert callable(WebhookDelivery.http_transport)

    def test_http_transport_with_mock(self, sqlite_backend, principal):
        """Use a mock transport that simulates HTTP behavior."""
        call_log = []

        def mock_transport(endpoint, event):
            call_log.append((endpoint.url, event.id))
            return (200, '{"ok": true}')

        delivery = WebhookDelivery(sqlite_backend, transport=mock_transport)

        # Create an event and endpoint
        from scoped.types import generate_id, now_utc
        event_id = generate_id()
        endpoint_id = generate_id()

        sqlite_backend.execute(
            "INSERT INTO events (id, event_type, actor_id, target_type, target_id, "
            "timestamp, data_json, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, "object_created", "u1", "Doc", "d1",
             now_utc().isoformat(), '{}', "ACTIVE"),
        )
        sqlite_backend.execute(
            "INSERT INTO webhook_endpoints (id, name, owner_id, url, config_json, "
            "created_at, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (endpoint_id, "test-hook", "u1", "https://example.com/hook",
             '{}', now_utc().isoformat(), "ACTIVE"),
        )
        sqlite_backend.execute(
            "INSERT INTO webhook_deliveries (id, event_id, webhook_endpoint_id, "
            "subscription_id, status, attempt_number, attempted_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?)",
            (generate_id(), event_id, endpoint_id, "sub-test", now_utc().isoformat()),
        )

        attempts = delivery.deliver_pending()
        assert len(attempts) == 1
        assert attempts[0].status == DeliveryStatus.DELIVERED
        assert len(call_log) == 1
        assert call_log[0][0] == "https://example.com/hook"

    def test_failed_delivery_records_error(self, sqlite_backend, principal):
        def fail_transport(endpoint, event):
            return (500, "Internal Server Error")

        delivery = WebhookDelivery(sqlite_backend, transport=fail_transport)

        from scoped.types import generate_id, now_utc
        event_id = generate_id()
        endpoint_id = generate_id()

        sqlite_backend.execute(
            "INSERT INTO events (id, event_type, actor_id, target_type, target_id, "
            "timestamp, data_json, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, "object_created", "u1", "Doc", "d1",
             now_utc().isoformat(), '{}', "ACTIVE"),
        )
        sqlite_backend.execute(
            "INSERT INTO webhook_endpoints (id, name, owner_id, url, config_json, "
            "created_at, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (endpoint_id, "test-hook", "u1", "https://example.com/hook",
             '{}', now_utc().isoformat(), "ACTIVE"),
        )
        sqlite_backend.execute(
            "INSERT INTO webhook_deliveries (id, event_id, webhook_endpoint_id, "
            "subscription_id, status, attempt_number, attempted_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?)",
            (generate_id(), event_id, endpoint_id, "sub-test", now_utc().isoformat()),
        )

        attempts = delivery.deliver_pending()
        assert len(attempts) == 1
        assert attempts[0].status == DeliveryStatus.FAILED
        assert attempts[0].response_status == 500


class TestWebhookBackoffRetry:

    def test_backoff_skips_recent_failures(self, sqlite_backend, principal):
        """Failures that happened recently should not be retried yet."""
        delivery = WebhookDelivery(sqlite_backend, max_retries=3)

        from scoped.types import generate_id, now_utc
        event_id = generate_id()
        endpoint_id = generate_id()

        sqlite_backend.execute(
            "INSERT INTO events (id, event_type, actor_id, target_type, target_id, "
            "timestamp, data_json, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, "object_created", "u1", "Doc", "d1",
             now_utc().isoformat(), '{}', "ACTIVE"),
        )
        sqlite_backend.execute(
            "INSERT INTO webhook_endpoints (id, name, owner_id, url, config_json, "
            "created_at, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (endpoint_id, "test-hook", "u1", "https://example.com/hook",
             '{}', now_utc().isoformat(), "ACTIVE"),
        )
        # Insert a recently failed delivery (1 attempt, just now)
        sqlite_backend.execute(
            "INSERT INTO webhook_deliveries (id, event_id, webhook_endpoint_id, "
            "subscription_id, status, attempt_number, attempted_at) VALUES (?, ?, ?, ?, 'failed', 1, ?)",
            (generate_id(), event_id, endpoint_id, "sub-test", now_utc().isoformat()),
        )

        # With backoff_base=60, first retry needs 60s — should skip
        attempts = delivery.retry_failed(backoff_base=60)
        assert len(attempts) == 0

    def test_backoff_retries_old_failures(self, sqlite_backend, principal):
        """Failures old enough should be retried."""
        call_log = []

        def mock_transport(endpoint, event):
            call_log.append(True)
            return (200, "ok")

        delivery = WebhookDelivery(
            sqlite_backend, transport=mock_transport, max_retries=3,
        )

        from scoped.types import generate_id, now_utc
        event_id = generate_id()
        endpoint_id = generate_id()

        sqlite_backend.execute(
            "INSERT INTO events (id, event_type, actor_id, target_type, target_id, "
            "timestamp, data_json, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, "object_created", "u1", "Doc", "d1",
             now_utc().isoformat(), '{}', "ACTIVE"),
        )
        sqlite_backend.execute(
            "INSERT INTO webhook_endpoints (id, name, owner_id, url, config_json, "
            "created_at, lifecycle) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (endpoint_id, "test-hook", "u1", "https://example.com/hook",
             '{}', now_utc().isoformat(), "ACTIVE"),
        )
        # Insert a failed delivery from 2 hours ago
        old_time = (now_utc() - timedelta(hours=2)).isoformat()
        sqlite_backend.execute(
            "INSERT INTO webhook_deliveries (id, event_id, webhook_endpoint_id, "
            "subscription_id, status, attempt_number, attempted_at) VALUES (?, ?, ?, ?, 'failed', 1, ?)",
            (generate_id(), event_id, endpoint_id, "sub-test", old_time),
        )

        attempts = delivery.retry_failed(backoff_base=60)
        assert len(attempts) == 1
        assert attempts[0].status == DeliveryStatus.DELIVERED


# =============================================================================
# 2. Scheduler → JobQueue bridge (process_due_actions)
# =============================================================================


class TestProcessDueActions:

    def test_one_shot_action_enqueued_and_archived(self, sqlite_backend, registry, principal):
        scheduler = Scheduler(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        action = scheduler.create_action(
            name="one-shot",
            owner_id=principal.id,
            action_type="run_report",
            action_config={"format": "pdf"},
            next_run_at=now_utc() - timedelta(minutes=5),
        )

        jobs = scheduler.process_due_actions(queue)
        assert len(jobs) == 1
        assert jobs[0].action_type == "run_report"
        assert jobs[0].scheduled_action_id == action.id

        # One-shot should be archived
        updated = scheduler.get_action(action.id)
        assert updated.lifecycle == Lifecycle.ARCHIVED

    def test_recurring_action_advanced(self, sqlite_backend, registry, principal):
        scheduler = Scheduler(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        schedule = scheduler.create_schedule(
            name="every-hour",
            owner_id=principal.id,
            interval_seconds=3600,
        )
        past = now_utc() - timedelta(minutes=5)
        action = scheduler.create_action(
            name="hourly-task",
            owner_id=principal.id,
            action_type="cleanup",
            next_run_at=past,
            schedule_id=schedule.id,
        )

        jobs = scheduler.process_due_actions(queue)
        assert len(jobs) == 1

        # Action should be advanced, not archived
        updated = scheduler.get_action(action.id)
        assert updated.lifecycle == Lifecycle.ACTIVE
        assert updated.next_run_at > past

    def test_future_actions_not_processed(self, sqlite_backend, registry, principal):
        scheduler = Scheduler(sqlite_backend)
        queue = JobQueue(sqlite_backend)

        scheduler.create_action(
            name="future-task",
            owner_id=principal.id,
            action_type="noop",
            next_run_at=now_utc() + timedelta(hours=1),
        )

        jobs = scheduler.process_due_actions(queue)
        assert len(jobs) == 0

    def test_enqueued_jobs_are_runnable(self, sqlite_backend, registry, principal):
        """Jobs created by process_due_actions can be executed by JobQueue."""
        results = {}

        def executor(action_type, config):
            results[action_type] = config
            return {"status": "done"}

        scheduler = Scheduler(sqlite_backend)
        queue = JobQueue(sqlite_backend, executor=executor)

        scheduler.create_action(
            name="test-job",
            owner_id=principal.id,
            action_type="send_email",
            action_config={"to": "alice@example.com"},
            next_run_at=now_utc() - timedelta(minutes=1),
        )

        scheduler.process_due_actions(queue)
        completed = queue.run_all()

        assert len(completed) == 1
        assert completed[0].state == JobState.COMPLETED
        assert results["send_email"]["to"] == "alice@example.com"


# =============================================================================
# 3. Connector federation — transport + failure handling
# =============================================================================


class TestConnectorTransport:

    @pytest.fixture
    def connector_setup(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="org", display_name="Org1", principal_id="org1")

        mgr = ConnectorManager(sqlite_backend)
        connector = mgr.propose(
            name="test-conn",
            local_org_id="org1",
            remote_org_id="org2",
            remote_endpoint="https://remote.example.com/sync",
            created_by="admin",
            direction=ConnectorDirection.OUTBOUND,
        )
        mgr.submit_for_approval(connector.id, actor_id="admin")
        mgr.approve(connector.id, actor_id="admin")
        return mgr, connector

    def test_sync_without_transport_succeeds(self, connector_setup):
        """Without transport, sync records success (local-only mode)."""
        mgr, connector = connector_setup
        traffic = mgr.sync_object(
            connector.id, object_type="Doc", object_id="d1",
        )
        assert traffic.status == TrafficStatus.SUCCESS

    def test_sync_with_transport_calls_remote(self, sqlite_backend, registry):
        call_log = []
        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="org", display_name="Org1", principal_id="org1")

        def mock_transport(url, payload):
            call_log.append((url, payload))
            return (200, '{"received": true}')

        mgr = ConnectorManager(sqlite_backend, transport=mock_transport)
        connector = mgr.propose(
            name="test-conn",
            local_org_id="org1",
            remote_org_id="org2",
            remote_endpoint="https://remote.example.com/sync",
            created_by="admin",
            direction=ConnectorDirection.OUTBOUND,
        )
        mgr.submit_for_approval(connector.id, actor_id="admin")
        mgr.approve(connector.id, actor_id="admin")

        traffic = mgr.sync_object(
            connector.id, object_type="Doc", object_id="d1",
        )
        assert traffic.status == TrafficStatus.SUCCESS
        assert len(call_log) == 1
        assert call_log[0][0] == "https://remote.example.com/sync"
        assert call_log[0][1]["object_type"] == "Doc"

    def test_sync_transport_failure_records_failed(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="org", display_name="Org1", principal_id="org1")

        def fail_transport(url, payload):
            return (500, "error")

        mgr = ConnectorManager(sqlite_backend, transport=fail_transport)
        connector = mgr.propose(
            name="test-conn",
            local_org_id="org1",
            remote_org_id="org2",
            remote_endpoint="https://remote.example.com/sync",
            created_by="admin",
            direction=ConnectorDirection.OUTBOUND,
        )
        mgr.submit_for_approval(connector.id, actor_id="admin")
        mgr.approve(connector.id, actor_id="admin")

        traffic = mgr.sync_object(
            connector.id, object_type="Doc", object_id="d1",
        )
        assert traffic.status == TrafficStatus.FAILED

    def test_sync_transport_exception_records_failed(self, sqlite_backend, registry):
        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="org", display_name="Org1", principal_id="org1")

        def crash_transport(url, payload):
            raise ConnectionError("Connection refused")

        mgr = ConnectorManager(sqlite_backend, transport=crash_transport)
        connector = mgr.propose(
            name="test-conn",
            local_org_id="org1",
            remote_org_id="org2",
            remote_endpoint="https://remote.example.com/sync",
            created_by="admin",
            direction=ConnectorDirection.OUTBOUND,
        )
        mgr.submit_for_approval(connector.id, actor_id="admin")
        mgr.approve(connector.id, actor_id="admin")

        traffic = mgr.sync_object(
            connector.id, object_type="Doc", object_id="d1",
        )
        assert traffic.status == TrafficStatus.FAILED

    def test_http_transport_static_method_exists(self):
        assert callable(ConnectorManager.http_transport)

    def test_inbound_sync_skips_transport(self, sqlite_backend, registry):
        """Inbound syncs should not call the outbound transport."""
        store = PrincipalStore(sqlite_backend)
        store.create_principal(kind="org", display_name="Org1", principal_id="org1")
        call_log = []

        def mock_transport(url, payload):
            call_log.append(True)
            return (200, "ok")

        mgr = ConnectorManager(sqlite_backend, transport=mock_transport)
        connector = mgr.propose(
            name="test-conn",
            local_org_id="org1",
            remote_org_id="org2",
            remote_endpoint="https://remote.example.com/sync",
            created_by="admin",
            direction=ConnectorDirection.BIDIRECTIONAL,
        )
        mgr.submit_for_approval(connector.id, actor_id="admin")
        mgr.approve(connector.id, actor_id="admin")

        traffic = mgr.sync_object(
            connector.id, object_type="Doc", direction="inbound",
        )
        assert traffic.status == TrafficStatus.SUCCESS
        assert len(call_log) == 0  # Transport not called for inbound


# Helper
def now_utc():
    return datetime.now(timezone.utc)
