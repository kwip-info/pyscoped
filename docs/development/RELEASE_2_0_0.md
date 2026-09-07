# PyScoped 2.0.0 — completed stable release

Released 2026-09-07. Free, MIT licensed, Django-only. No hosted ingestion or paid tiers.

- Release commit: `74cb0a8cd9f86481c9e839bf7a218989bc336f6a`.
- Stable tag: `v2.0.0`.
- [GitHub release](https://github.com/kwip-info/pyscoped/releases/tag/v2.0.0).
- [PyPI release](https://pypi.org/project/pyscoped/2.0.0/).
- [Matrix CI](https://github.com/kwip-info/pyscoped/actions/runs/34143112612):
  all eight supported Python/Django combinations, SQLite 83 tests / two skips,
  PostgreSQL 85 tests, lint, drift, and isolated distribution validation passed.
- [Publish workflow](https://github.com/kwip-info/pyscoped/actions/runs/34143293437):
  tag/commit/CI validation, build, isolated wheel adoption, and PyPI OIDC succeeded.
- Wheel SHA256: `e657c04347daafa444e92f29e8f90bb81e7df3cc6be08f9d2e6b17ad27da90d2`.
- Sdist SHA256: `24261012843dfd7f24aea46468fbef471397db35f9e9d04bfc098ca7438e8d57`.

The public KWIP site now uses the published 2.0.0 dependency. Its 102 tests pass
on SQLite/PostgreSQL and both supported Django series, including actual production
URL configuration. A restored existing database needed only the additive PyScoped
migration. A scoped mapping of an existing table passed baseline, audited update,
chain verification, and cross-scope denial without changing the application schema;
trial writes were rolled back. This is initial integration evidence, not long-term
production soak or a benchmark of every application workload.

The former site repository is public MIT [kwip-info/kwiptech](https://github.com/kwip-info/kwiptech).
kwip.tech retains Digest product information and pilot intake. Hosted PyScoped
API/ingestion, dashboard, account provisioning, and billing are retired with HTTP 410.
Historical data stays private; no automatic v1 schema/history conversion is claimed.

All five development/release phases are complete. Earlier alpha phase records
are historical. The initial stable release retains the documented limitations in
[guarantees](../guarantees.md); stability does not promise unsupported ORM paths.
