# Changelog

## 2.0.0 — unreleased

Breaking Django-only redesign for incremental adoption in existing applications.
The import is `pyscoped`. Existing models, primary keys, scope fields, and auth remain
authoritative; the old universal object-store API is not shipped.

- Explicit model decorator/registration, optional field-free base class, scoped manager.
- Trusted actor/scope context, audit-only or enforced adoption modes.
- Transactional per-resource audit history, actual persisted-value snapshots.
- Scoped queries and guarded queryset updates; clear rejection of unsafe operations.
- Sync/async request and service integration, including context-safe streaming.
- Idempotent baseline capture and conflict-checked scalar restoration.
- Native Django migrations, SQLite/PostgreSQL tests, executable existing-app example.
- No hosted sync, accounts, metering, paid tiers, cloud credentials, or telemetry.

1.8.1 source and changelog are preserved in `legacy/v1/`, excluded from artifacts.
