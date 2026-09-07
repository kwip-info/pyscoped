> Historical pre-cutover inventory. Hosted retirement and stable cutover are now
> authorized under PHASE_5_STABLE_RELEASE.md.

# kwip.tech compatibility and future cutover

Status: inventory only; no application, deployment, data, or billing change made.

The production repository's `requirements.txt` pins `pyscoped[django]==1.8.1`.
Its development requirements include `-e ../pyscoped`; that editable path must not
be used against this 2.0 branch for the legacy app. Use a separate 1.8.1 checkout
or install the pinned released dependency until the website overhaul is ready.

The 2.0 wheel contains `pyscoped`, not `scoped`, and does not ship 1.x APIs or schemas.
It is not a drop-in dependency upgrade or an automatic migration of 1.x history.

## Verified source dependencies

- `plane/settings.py`: 1.x Django app initialization and platform context middleware.
- `plane/core/models.py`: organization principal/scope sync, application child scopes,
  roles/rules, membership projection and revocation; application rows are already
  Django models with additive SDK sync.
- `plane/middleware.py`, `plane/api/principal_resolver.py`, `plane/api/auth.py`:
  principal resolution and existing API-key/auth integration.
- `plane/api/v1/views.py`, `plane/dashboard/scoped.py`, dashboard services/analytics:
  management-plane SDK endpoints and scoped metadata queries.
- `plane/billing/*`: paid-plan, Stripe, and metering behavior belonging to the
  existing website product, not the new free library.
- `plane/public/management/commands/sync_pyscoped_docs.py`, public views/URLs:
  documentation and onboarding references to the 1.x product.

## Before a future production cutover

1. Decide the redesigned site's purpose and retained user/data/billing obligations.
   The report of no external SDK adoption is not proof that every associated
   website record or payment can be removed without review.
2. Back up application and 1.x SDK tables; test restoration into an isolated database.
3. Replace/remove each SDK dependency deliberately; retain authenticated identity
   and ownership semantics for any retained user-facing records.
4. Stage the new free-library documentation and installation instructions. Keep
   production URLs and auth/deployment integration intact until cutover verification.
5. Test migration, integrity, access boundaries, and a rollback to the pinned 1.8.1
   deployment. Record the actual cutover under enterprise `operations/`.

No hosting, Stripe, Clerk, DNS, public routes, or existing SDK data were changed by
this library overhaul. Publication/cutover needs its own explicit current instruction.
