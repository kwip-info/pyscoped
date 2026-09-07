# Contributing

PyScoped is MIT licensed and fully free. Contributions should improve gradual Django
adoption or substantiate a documented guarantee. Read AGENTS.md and the development
plan. Legacy source is reference material, not a second actively developed framework.

Use a virtual environment and `pip install -e '.[dev,postgres]'`. Run pytest, Ruff,
Django checks, and migration drift checks as shown in the README. Include meaningful
regression tests with real database writes for enforcement/audit changes.

For PostgreSQL tests, set `PYSCOPED_POSTGRES=1`, `PGHOST`, `PGPORT`, `PGUSER`, and
`PGPASSWORD` for a **disposable test server**; `PGDATABASE` defaults to `pyscoped_test`.
Tests create/drop Django test databases (including an `other` alias) and must never
point at production. PostgreSQL-specific concurrency tests skip on SQLite. CI runs
both engines. No provider account is required.

Build with `python -m build`; validate metadata with
`twine check dist/*.whl dist/*.tar.gz`. Run
`python tests/packaging_smoke.py dist/pyscoped-2.0.0a1-py3-none-any.whl` to install
into a clean temporary environment and exercise the included adoption example.

Please report reproducible issues with Django/Python/database versions and a minimal
example. Do not include real credentials, customer data, or unredacted audit payloads.
For sensitive reports use the repository's private vulnerability reporting facility
if enabled; do not assume public issues are private.
