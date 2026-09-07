# Phase 5 — stable release and platform retirement

Authorized by Trevor on 2026-09-07. This phase supersedes the local-alpha-only
release boundary recorded in phases 0–4. Earlier alpha evidence remains historical.

## Scope and gates

- Retain the documented supported API/ORM contract; no feature expansion solely
  to justify the stable label. Record unsupported paths as explicit limitations.
- Validate an existing pre-cutover application database with additive migrations;
  test the production site routes with the new library installed, preserved pilot
  intake, and zero-query retirement responses. Do not convert old hosted records.
- Run SDK tests on SQLite/PostgreSQL, the supported Python/Django CI matrix,
  migration drift checks, and isolated wheel/example checks for the stable version.
- Publish only a stable tag on main whose commit passed CI. Build from that tag,
  validate the distribution, and use the dedicated PyPI trusted publishing environment.
- Release and deployment are authorized. Keep a production backup and rollback
  release, make the site source MIT/public, and record external receipts.

The initial stable API does not imply long-term production soak or universal ORM
coverage. Application workload/performance and authorization review remain necessary.
Hosted v1 ingestion/support ends; old local versions/source stay available for migration.

Status: platform has 102 passing tests and migration drift checks; stable SDK and
remote release validation in progress. See enterprise operations for production receipts.
