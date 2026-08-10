from pathlib import Path
import importlib.util
import os
from urllib.parse import quote, urlparse

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


def env_path(name, default):
    value = os.getenv(name, default).strip().strip("/")
    return value or default.strip("/")


def unique_apps(apps):
    return list(dict.fromkeys(apps))


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY") or "dev-only-change-me"
API_BASE_PATH = env_path("API_BASE_PATH", "api")
ADMIN_API_PATH = env_path("ADMIN_API_PATH", "admin")
ADMIN_FRONTEND_PATH = env_path("ADMIN_FRONTEND_PATH", "admin")

USE_DJANGO_TENANTS = env_bool("USE_DJANGO_TENANTS", False)
TENANT_BASE_DOMAIN = os.getenv("TENANT_BASE_DOMAIN", "expressnet.app")

SHARED_APPS = [
    "django_tenants",
    "core",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
]

TENANT_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "network",
    "management",
]

LEGACY_APPS = [
    "billing_api",
]

INSTALLED_APPS = (
    unique_apps([*SHARED_APPS, *TENANT_APPS, *LEGACY_APPS])
    if USE_DJANGO_TENANTS
    else unique_apps(app for app in [*SHARED_APPS, *TENANT_APPS, *LEGACY_APPS] if app != "django_tenants")
)

TENANT_MODEL = "core.Tenant"
TENANT_DOMAIN_MODEL = "core.Domain"
DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",) if USE_DJANGO_TENANTS else []

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
]
if importlib.util.find_spec("whitenoise"):
    MIDDLEWARE.append("whitenoise.middleware.WhiteNoiseMiddleware")
if USE_DJANGO_TENANTS:
    MIDDLEWARE.append("django_tenants.middleware.main.TenantMainMiddleware")
MIDDLEWARE += [
    "billing_api.middleware.SecurityHeadersMiddleware",
    "billing_api.middleware.SimpleRateLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "billing_saas_django.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "frontend" / "dist"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]
WSGI_APPLICATION = "billing_saas_django.wsgi.application"
ASGI_APPLICATION = "billing_saas_django.asgi.application"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/assets/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "frontend" / "dist" / "assets"]
if importlib.util.find_spec("whitenoise"):
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "billing_api.User"

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL") or EMAIL_HOST_USER or "no-reply@billing-saas.local"
ADMIN_NOTIFICATION_EMAILS = env_list("ADMIN_NOTIFICATION_EMAILS")
TENANT_LOGIN_2FA_ENABLED = env_bool("TENANT_LOGIN_2FA_ENABLED", True)

X_FRAME_OPTIONS = "DENY"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "UNAUTHENTICATED_TOKEN": None,
}

def railway_redis_url():
    host = os.getenv("REDISHOST")
    port = os.getenv("REDISPORT", "6379")
    if not host or "${{" in host or "}" in host or "${{" in port or "}" in port:
        return None
    user = os.getenv("REDISUSER", "default")
    password = os.getenv("REDISPASSWORD") or os.getenv("REDIS_PASSWORD") or ""
    return f"redis://{quote(user)}:{quote(password)}@{host}:{port}/0"


def usable_redis_url(value):
    if not value or "${{" in value or "}" in value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
        return None
    return value


REDIS_URL = usable_redis_url(os.getenv("REDIS_PUBLIC_URL")) or usable_redis_url(os.getenv("REDIS_URL")) or railway_redis_url() or "redis://localhost:6379/0"
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache"
        if importlib.util.find_spec("django_redis")
        else "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": REDIS_URL if importlib.util.find_spec("django_redis") else "billing-saas-local",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"}
        if importlib.util.find_spec("django_redis")
        else {},
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_BEAT_SCHEDULE = {
    "expire-tenant-subscriptions-hourly": {
        "task": "billing_api.tasks.expire_tenant_subscriptions",
        "schedule": 3600.0,
    },
    "expire-customer-access-every-five-minutes": {
        "task": "billing_api.tasks.expire_customer_access",
        "schedule": 300.0,
    },
}
