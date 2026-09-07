# Adopt an existing Django invoice table

From this directory, with PyScoped installed:

```sh
python manage.py migrate
python manage.py demo
```

Use a fresh SQLite database for each demo (set `PYSCOPED_EXAMPLE_DB` to its path).
Migration 0002 represents the existing application's invoice, before adoption.
The demo preserves that row's ID and private note, baselines history, uses a custom
queryset, updates and restores selected fields, and checks cross-scope denial.

The model demonstrates the decorator plus explicit manager approach; no base-class
change or extra columns are necessary. `billing/auth.py` shows how an existing
Django user and membership table can supply trusted request context. Add
`pyscoped.middleware.ScopedMiddleware` after your session/authentication middleware
when integrating that resolver into a web app. The demo is not a deployable website.
