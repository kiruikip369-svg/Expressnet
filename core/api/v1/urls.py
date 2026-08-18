from django.conf import settings
from django.urls import path

from core.api.v1 import views


urlpatterns = [
    path("health", views.health),
    path("auth/register", views.auth_register),
    path("auth/login", views.auth_login),
    path("public/site", views.public_site),
    path("public/stats", views.public_stats),
    path("subscription/status", views.tenant_subscription_status),
    path(f"{settings.ADMIN_API_PATH}/auth/login", views.admin_login),
    path(f"{settings.ADMIN_API_PATH}/tenants", views.admin_tenants),
    path(f"{settings.ADMIN_API_PATH}/tenants/stats/summary", views.admin_stats),
    path(f"{settings.ADMIN_API_PATH}/system/stats", views.admin_system_stats),
    path(f"{settings.ADMIN_API_PATH}/system", views.admin_system),
    path(f"{settings.ADMIN_API_PATH}/system/migrations", views.admin_system_migrations),
    path(f"{settings.ADMIN_API_PATH}/subscriptions", views.admin_subscriptions),
    path(f"{settings.ADMIN_API_PATH}/subscriptions/revenue-chart", views.admin_revenue_chart),
    path(f"{settings.ADMIN_API_PATH}/subscriptions/<int:subscription_id>", views.admin_subscriptions),
    path(f"{settings.ADMIN_API_PATH}/subscriptions/<int:subscription_id>/payments", views.admin_subscription_payments),
    path(f"{settings.ADMIN_API_PATH}/tenants/<str:tenant_id>/subscription", views.admin_tenant_subscription),
    path(f"{settings.ADMIN_API_PATH}/tenants/<str:tenant_id>/subscription/remind", views.admin_subscription_remind),
    path(f"{settings.ADMIN_API_PATH}/tenants/<str:tenant_id>/mikrotik/test", views.admin_mikrotik_test),
    path(f"{settings.ADMIN_API_PATH}/tenants/audit/logs", views.admin_audit_logs),
    path(f"{settings.ADMIN_API_PATH}/tenants/<str:tenant_id>", views.admin_tenants),
    path(f"{settings.ADMIN_API_PATH}/tenants/<str:tenant_id>/<str:child>", views.admin_tenants),
    path(f"{settings.ADMIN_API_PATH}/site", views.admin_site),
    path(f"{settings.ADMIN_API_PATH}/users", views.admin_users),
    path(f"{settings.ADMIN_API_PATH}/users/<str:tenant_id>/<str:customer_id>", views.admin_users),
    path(f"{settings.ADMIN_API_PATH}/users/<str:tenant_id>/<str:customer_id>/<str:action>", views.admin_users),
]

