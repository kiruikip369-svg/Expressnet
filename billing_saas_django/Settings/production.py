import os

import dj_database_url

from .base import *


DEBUG = False
RATE_LIMIT_ENABLED = env_bool("RATE_LIMIT_ENABLED", True)

if not os.getenv("EMAIL_BACKEND"):
    EMAIL_BACKEND = "billing_api.email_backend.IPv4EmailBackend"

if not os.getenv("DJANGO_SECRET_KEY"):
    raise RuntimeError("DJANGO_SECRET_KEY is required in production — refusing to start with the dev fallback key.")

if TENANT_LOGIN_2FA_ENABLED:
    required_email_settings = {
        "EMAIL_HOST": EMAIL_HOST,
        "EMAIL_HOST_USER": EMAIL_HOST_USER,
        "EMAIL_HOST_PASSWORD": EMAIL_HOST_PASSWORD,
        "DEFAULT_FROM_EMAIL": DEFAULT_FROM_EMAIL,
    }
    missing_email_settings = [name for name, value in required_email_settings.items() if not str(value or "").strip()]
    if missing_email_settings:
        raise RuntimeError(
            "Tenant login two-step verification is enabled, but production email is not fully configured: "
            + ", ".join(missing_email_settings)
            + ". Set these variables or disable TENANT_LOGIN_2FA_ENABLED."
        )
    if EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":
        raise RuntimeError(
            "Tenant login two-step verification is enabled, but EMAIL_BACKEND is the console backend. "
            "Use billing_api.email_backend.IPv4EmailBackend or another real email backend in production."
        )

RAILWAY_ALLOWED_HOSTS = [
    "web-production-b9d86.up.railway.app",
    ".railway.app",
    "stumpiest-caudally-eloy.ngrok-free.dev",
    "expressnetbilling.com",
]
ALLOWED_HOSTS = sorted({*RAILWAY_ALLOWED_HOSTS, *env_list("ALLOWED_HOSTS", "localhost,127.0.0.1")})
CSRF_TRUSTED_ORIGINS = sorted(
    {
        "https://web-production-b9d86.up.railway.app",
        "https://stumpiest-caudally-eloy.ngrok-free.dev",
        "https://expressnetbilling.com",
        *env_list("CSRF_TRUSTED_ORIGINS"),
    }
)


def production_public_url(name, default=""):
    value = (os.getenv(name) or "").strip().rstrip("/")
    if value and "localhost" not in value and "127.0.0.1" not in value:
        return value
    return default


PUBLIC_APP_URL = production_public_url("PUBLIC_APP_URL", "https://web-production-b9d86.up.railway.app")
PAYSTACK_CALLBACK_BASE_URL = production_public_url("PAYSTACK_CALLBACK_BASE_URL", PUBLIC_APP_URL)

DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required in production.")

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
# Ensure connection reuse so the 30s agent-poll burst (many sequential
# snapshot fetches) does not exhaust Railway's Postgres proxy pool.
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", True)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
