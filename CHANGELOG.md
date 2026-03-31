# Changelog

## 0.1.1 (2026-03-31)

### Fixed
- ScopedManager now initialized with audit_writer for proper hash-chained audit trails
- Workflow transitions use ScopedManager.update() instead of raw SQL for correct versioning
- Removed duplicate audit entries from runtime data API
- Import pipeline resolves principal FK constraints by importing identities first
- Scope and object creation APIs resolve 'system' owner to real principal for FK compliance

### Added
- Runtime workflow engine: state machine transitions with validation
- Compliance test suite: 16 tests covering versioning, audit integrity, isolation, and rule enforcement
- Scope filtering on data list and widget APIs

### Documentation
- Comprehensive PyPI README with correct code examples
- Getting Started guide with end-to-end walkthrough
- API Reference covering all 16 layers
- Framework Adapters guide (Django, FastAPI, Flask, MCP)

## 0.1.0 (2026-03-28)

### Added
- Initial release
- 16 layers: Registry through Scheduling
- 9 extensions: Migrations through Import/Export
- 4 framework adapters: Django, FastAPI, Flask, MCP
- Compliance engine with 17+ invariant checks
- 1,493 tests
