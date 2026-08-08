# Expressnet v5

Modular Django monolith for ISP billing, network provisioning, RADIUS, MikroTik,
Paystack, and M-Pesa flows.

## Run Locally

```powershell
.\venv\Scripts\Activate.ps1
python manage.py check
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
```

`makemigrations` is plural. A compatibility alias named `makemigration` is also
available for the common typo.

## Tenant Mode

Local SQLite mode is the default for development. PostgreSQL is required for
schema-per-tenant mode:

```powershell
$env:USE_DJANGO_TENANTS = "true"
$env:DATABASE_URL = "postgresql://user:password@host:5432/dbname"
python manage.py migrate_schemas --shared
python manage.py migrate_schemas --tenant
```

See [docs/tenant_runbook.md](docs/tenant_runbook.md) for onboarding, local
subdomain testing, and per-tenant backup/restore commands.
