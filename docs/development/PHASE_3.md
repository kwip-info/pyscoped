# Phase 3 — incremental adoption and operations

Status: complete.

Provide scoped, resumable baseline capture for existing rows with no schema or ID
conversion. Default command execution is dry-run; applying creates only missing
baseline events and commits one row at a time so retries do not duplicate history.
No historical events are fabricated. Existing read/write behavior is demonstrated
in an executable example with a real existing Django table and custom auth resolver.

Provide scoped history retrieval and chain verification. Restore only a live row's
selected non-relational fields, require an expected revision, compare its current
snapshot to the history head, and append a new restore event. Refuse deleted-row
resurrection, PK/scope/FK restoration, stale revisions, and history divergence.
Pin history resources to their original scope so PK reuse cannot cross tenants.

Add sync/async request middleware with an explicitly configured trusted resolver;
no caller-supplied actor header. Context must cover rendering/stream consumption
or explicitly reject scoped work after the handler. Add a service decorator for
existing functions to attach context from a trusted argument resolver. Preserve
existing authentication; auth/membership checks stay in application code.

Tests: baseline dry-run and idempotency; existing table/ID preservation; history
scope boundaries and tampering; restore conflict, rollback, and unchanged secret
fields; middleware errors/streaming/async cleanup; service decorator execution.

Evidence (2026-09-07): 60 initial adoption/integration tests passed on SQLite and
PostgreSQL, subsequently expanded in phase 4. Executable example preserves existing
invoice ID 501 and its unselected private note through baseline, update, and restore.
Fresh wheel installation also executes migrations and the demo outside the checkout.
The test harness was corrected to resolve macOS /var versus /private/var paths and
remove inherited PYTHONPATH before verifying the isolated install.
