# PyScoped 2.0 development guidance

Read `docs/development/PLAN.md` and the active phase file before changing behavior.
PyScoped is now a free, MIT, Django-only integration library. `legacy/v1/` is frozen
historical source, excluded from wheels and sdists; its instructions and universal
framework claims do not govern 2.0. The new import is `pyscoped`.

- Existing Django tables and identities are authoritative. Add no fields to user
  models implicitly. All library tables use normal Django migrations.
- Actor attribution is explicit. Missing identity must never be replaced by owner
  identity. Enforced scope comes from trusted application authorization, not headers.
- Audit writes and application writes commit or roll back together on the same DB.
- Never imply ORM-level enforcement protects raw SQL, private APIs, other database
  writers, unregistered models, or data already returned to callers.
- Unsupported public mutation paths must fail clearly, not silently lose tracking.
- Write meaningful tests for each guarantee and regression, including real DB writes.
  Record phase evidence, limitations, and status before advancing the plan.
- No accounts, telemetry, cloud sync, paid gates, or external service dependency.
- Do not publish or deploy as an incidental part of local development.
