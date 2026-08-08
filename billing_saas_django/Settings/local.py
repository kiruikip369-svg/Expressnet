from .base import *

import dj_database_url
from urllib.parse import quote, unquote, urlparse


DEBUG = env_bool("DJANGO_DEBUG", True)
RATE_LIMIT_ENABLED = env_bool("RATE_LIMIT_ENABLED", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

RAILWAY_TCP_PROXY_DOMAIN = os.getenv("RAILWAY_TCP_PROXY_DOMAIN")
RAILWAY_TCP_PROXY_PORT = os.getenv("RAILWAY_TCP_PROXY_PORT")
if RAILWAY_TCP_PROXY_DOMAIN and RAILWAY_TCP_PROXY_PORT:
    private_database_url = os.getenv("DATABASE_URL", "")
    private_database = urlparse(private_database_url) if private_database_url else None
    railway_user = (
        os.getenv("PGUSER")
        or os.getenv("POSTGRES_USER")
        or (unquote(private_database.username) if private_database and private_database.username else None)
        or "postgres"
    )
    railway_password = (
        os.getenv("PGPASSWORD")
        or os.getenv("POSTGRES_PASSWORD")
        or (unquote(private_database.password) if private_database and private_database.password else "")
    )
    railway_database = (
        os.getenv("PGDATABASE")
        or os.getenv("POSTGRES_DB")
        or (private_database.path.lstrip("/") if private_database and private_database.path else None)
        or "railway"
    )
    RAILWAY_PUBLIC_DATABASE_URL = (
        f"postgresql://{quote(railway_user)}:{quote(railway_password)}@"
        f"{RAILWAY_TCP_PROXY_DOMAIN}:{RAILWAY_TCP_PROXY_PORT}/{quote(railway_database)}"
    )
else:
    RAILWAY_PUBLIC_DATABASE_URL = None

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or RAILWAY_PUBLIC_DATABASE_URL or os.getenv("DATABASE_URL")
if DATABASE_URL:
    database_ssl_default = "postgres.railway.internal" not in DATABASE_URL
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=env_bool("DATABASE_SSL_REQUIRE", database_ssl_default),
        )
    }
    if USE_DJANGO_TENANTS:
        DATABASES["default"]["ENGINE"] = "django_tenants.postgresql_backend"
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "CONN_MAX_AGE": 60,
        }
    }
    if USE_DJANGO_TENANTS:
        raise RuntimeError("USE_DJANGO_TENANTS requires PostgreSQL; set DATABASE_URL.")

SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", False)
