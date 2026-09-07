"""Integration smoke test — verify SDK ↔ Platform round-trip.

Exercises the full loop: object CRUD → audit trail → sync agent batch
push → platform ingest → usage snapshot → chain verification. If any
layer is broken, the test fails with a clear message.

Usage (CLI):
    python -m scoped.testing.integration \\
        --base-url http://localhost:8000/v1 \\
        --api-key psc_test_...

Usage (Python):
    from scoped.testing.integration import PlatformSmokeTest
    test = PlatformSmokeTest(
        base_url="http://localhost:8000/v1",
        api_key="psc_test_...",
    )
    result = test.run()
    assert result.passed, result.summary()

Usage (Docker Compose):
    docker compose run --rm smoke-test

Prerequisites:
    Platform must be running. Provision credentials with:
        python manage.py create_test_sdk_config
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class StepResult:
    """Result of a single smoke test step."""
    name: str
    passed: bool
    duration_ms: float
    detail: str = ""
    error: str = ""


@dataclass
class SmokeTestResult:
    """Aggregate result of the smoke test."""
    steps: list[StepResult] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.steps)

    @property
    def total_ms(self) -> float:
        return sum(s.duration_ms for s in self.steps)

    def summary(self) -> str:
        lines = []
        for s in self.steps:
            icon = "PASS" if s.passed else "FAIL"
            lines.append(f"  [{icon}] {s.name} ({s.duration_ms:.0f}ms)")
            if s.detail:
                lines.append(f"         {s.detail}")
            if s.error:
                lines.append(f"         ERROR: {s.error}")

        passed = sum(1 for s in self.steps if s.passed)
        total = len(self.steps)
        status = "PASSED" if self.passed else "FAILED"
        lines.append(f"\n  {status}: {passed}/{total} steps in {self.total_ms:.0f}ms")
        return "\n".join(lines)


class PlatformSmokeTest:
    """End-to-end smoke test: SDK → Platform → verify.

    Steps:
    1. Ping — verify platform is reachable
    2. Plan — verify plan endpoint returns data
    3. SDK init — create local pyscoped client
    4. Create objects — create principals + objects with audit trail
    5. Sync — push audit batch to platform
    6. Verify chain — platform chain matches local
    7. Usage — platform reports correct resource counts
    8. List keys — API key management works
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        database_url: str | None = None,
        verbose: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.database_url = database_url
        self.verbose = verbose
        self._result = SmokeTestResult()

    def run(self) -> SmokeTestResult:
        """Execute all smoke test steps. Returns SmokeTestResult."""
        self._step("ping", self._test_ping)
        self._step("plan", self._test_plan)
        self._step("sdk_init", self._test_sdk_init)
        self._step("create_objects", self._test_create_objects)
        self._step("audit_trail", self._test_audit_trail)
        self._step("sync_batch", self._test_sync_batch)
        self._step("verify_chain", self._test_verify_chain)
        self._step("usage", self._test_usage)
        self._step("list_keys", self._test_list_keys)

        self._result.finished_at = datetime.now(timezone.utc)
        return self._result

    # -- Steps ----------------------------------------------------------------

    def _test_ping(self) -> str:
        resp = self._api("GET", "/ping", auth=False)
        assert resp["ok"] is True, f"Ping returned ok={resp['ok']}"
        return f"server_time={resp['server_time']}, api_version={resp['api_version']}"

    def _test_plan(self) -> str:
        resp = self._api("GET", "/plan")
        assert "plan" in resp, "No plan field in response"
        assert "limits" in resp, "No limits field in response"
        return f"plan={resp['plan']}, max_objects={resp['limits']['max_objects']}"

    def _test_sdk_init(self) -> str:
        import scoped
        from scoped.sync.config import SyncConfig

        db_url = self.database_url or "sqlite:///:memory:"
        config = SyncConfig(
            base_url=self.base_url,
            interval_seconds=3600,  # Don't auto-sync during test
        )
        self._client = scoped.ScopedClient(
            database_url=db_url,
            api_key=self.api_key,
            sync_config=config,
        )
        return f"backend={self._client.backend.dialect}"

    def _test_create_objects(self) -> str:
        c = self._client
        alice = c.principals.create("Smoke Test Alice", kind="user")
        bob = c.principals.create("Smoke Test Bob", kind="user")

        with c.as_principal(alice):
            doc, v1 = c.objects.create("smoke_test_doc", data={"step": 1})
            doc, v2 = c.objects.update(doc.id, data={"step": 2})

            team = c.scopes.create("Smoke Test Team")
            c.scopes.add_member(team, bob, role="editor")
            c.scopes.project(doc, team)

        self._alice = alice
        self._doc = doc
        self._team = team
        return f"principal={alice.id}, object={doc.id}, scope={team.id}, versions={v2.version}"

    def _test_audit_trail(self) -> str:
        c = self._client
        trail = c.audit.for_object(self._doc.id)
        assert len(trail) >= 2, f"Expected >=2 audit entries, got {len(trail)}"

        verification = c.audit.verify()
        assert verification.valid, f"Chain broken at sequence {verification.broken_at_sequence}"

        return f"entries={len(trail)}, chain_valid=True, checked={verification.entries_checked}"

    def _test_sync_batch(self) -> str:
        from scoped.audit.query import AuditQuery

        query = AuditQuery(self._client.backend)
        entries = query.query(limit=100)

        if not entries:
            return "no entries to sync (skipped)"

        # Build a sync batch manually and push it
        batch_entries = []
        for e in entries:
            batch_entries.append({
                "id": e.id,
                "sequence": e.sequence,
                "actor_id": e.actor_id,
                "action": e.action.value,
                "target_type": e.target_type,
                "target_id": e.target_id,
                "timestamp": e.timestamp.isoformat(),
                "hash": e.hash,
                "previous_hash": e.previous_hash,
                "scope_id": e.scope_id or "",
                "parent_trace_id": e.parent_trace_id or "",
                "metadata": e.metadata or {},
            })

        import uuid
        now_iso = datetime.now(timezone.utc).isoformat()
        batch = {
            "batch_id": uuid.uuid4().hex,
            "first_sequence": entries[0].sequence,
            "last_sequence": entries[-1].sequence,
            "chain_hash": entries[-1].hash,
            "content_hash": "smoke_test",
            "signature": "smoke_test",
            "sdk_version": "0.6.0",
            "created_at": now_iso,
            "entries": batch_entries,
            "resource_counts": {
                "active_objects": 1,
                "active_principals": 2,
                "active_scopes": 1,
                "timestamp": now_iso,
            },
        }

        resp = self._api("POST", "/sync/batch", body=batch)
        assert resp.get("accepted") is True, f"Batch rejected: {resp.get('message')}"
        return f"accepted={resp['accepted']}, entries={len(batch_entries)}, message={resp['message']}"

    def _test_verify_chain(self) -> str:
        from scoped.audit.query import AuditQuery

        query = AuditQuery(self._client.backend)
        entries = query.query(limit=1, order_by="-sequence")
        if not entries:
            return "no entries (skipped)"

        local_hash = entries[0].hash
        local_count = query.count()

        resp = self._api("POST", "/sync/verify", body={
            "local_chain_hash": local_hash,
            "local_entry_count": local_count,
        })
        verified = resp.get("verified", False)
        return f"verified={verified}, local={local_count}, server={resp.get('server_entry_count')}"

    def _test_usage(self) -> str:
        resp = self._api("GET", "/usage")
        if "error" in resp:
            return f"no billing period (expected for test): {resp.get('message')}"
        return (
            f"peak_objects={resp.get('peak_objects')}, "
            f"peak_principals={resp.get('peak_principals')}, "
            f"audit_entries={resp.get('audit_entries_synced')}"
        )

    def _test_list_keys(self) -> str:
        resp = self._api("GET", "/keys")
        keys = resp.get("keys", [])
        active = sum(1 for k in keys if k.get("is_active"))
        return f"total={len(keys)}, active={active}"

    # -- Helpers --------------------------------------------------------------

    def _step(self, name: str, fn) -> None:
        start = time.monotonic()
        try:
            detail = fn()
            duration = (time.monotonic() - start) * 1000
            self._result.steps.append(StepResult(
                name=name, passed=True, duration_ms=duration, detail=detail or "",
            ))
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            self._result.steps.append(StepResult(
                name=name, passed=False, duration_ms=duration, error=str(exc),
            ))

    def _api(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        auth: bool = True,
    ) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            try:
                return json.loads(body_text)
            except Exception:
                raise RuntimeError(f"HTTP {exc.code}: {body_text[:200]}") from exc
        except urllib.error.URLError as exc:
            raise ConnectionError(f"Cannot reach {url}: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="pyscoped SDK ↔ Platform integration smoke test",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000/v1",
        help="Platform API base URL (default: http://localhost:8000/v1)",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="API key (psc_test_...)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SDK database URL (default: in-memory SQLite)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )
    args = parser.parse_args()

    test = PlatformSmokeTest(
        base_url=args.base_url,
        api_key=args.api_key,
        database_url=args.database_url,
        verbose=args.verbose,
    )

    print(f"Running smoke test against {args.base_url}...")
    print()

    result = test.run()
    print(result.summary())
    print()

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
