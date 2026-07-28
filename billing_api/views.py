import json
import html
import os
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from urllib.parse import urlparse

import jwt
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import close_old_connections, connection
from django.db.utils import OperationalError
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view,permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from .auth import admin_required, tenant_required
from .models import AdminUser, Customer, InternetPackage, Payment, SubscriptionPayment, Tenant, TenantSubscription, Ticket, User
from .services import (
    admin_token,
    check_password,
    create_hotspot_profile,
    create_ppp_profile,
    configure_router_port,
    create_paystack_subaccount,
    tenant_uses_daraja,
    initiate_daraja_payment,
    make_daraja_callback_token,
    verify_daraja_callback_token,
    _build_port_command_script,
    delete_router_customer,
    captive_portal_url,
    ensure_hotspot_captive_portal,
    find_child_by_field,
    has_mikrotik_credentials,
    hash_password,
    hotspot_alogin_redirect_html,
    hotspot_error_redirect_html,
    hotspot_login_redirect_html,
    hotspot_redirect_html,
    routeros_hotspot_fetch_script,
    initiate_paystack_payment,
    initiate_daraja_stk,
    iso_now,
    firebase_backup_configured,
    list_children,
    mikrotik_managed_bridge_name,
    normalize_public_url,
    normalize_phone,
    normalize_rate_limit,
    package_service_type,
    PaymentProviderError,
    ref,
    _rsc_escape,
    _get_jwt_secret,
    router_connect,
    router_interface_status,
    router_items,
    send_whatsapp_message,
    set_customer_enabled,
    tenant_token,
    upsert_customer_access,
    utcnow,
    verify_paystack_signature,
    verify_paystack_transaction,
    write_audit_log,
)


DEFAULT_SITE = {
    "brand_name": "Billing SaaS",
    "headline": "Internet billing built for hotspot businesses",
    "subheadline": "Sell packages, collect Paystack payments, and activate MikroTik users automatically.",
    "about": "We help hotspot operators manage customers, packages, payments, and access control from one secure platform.",
    "phone": "+254 701396967/+254 729 281669",
    "email": "support@example.com",
    "location": "Thika , Kenya",
    "address": "Nairobi, Kenya",
    "cta_label": "Register your business",
    "cta_url": "/register",
}
MASKED = "••••••••"
SENSITIVE_FIELDS = {"password", "mikrotik_pass", "paystack_secret_key"}


def body(request):
    if hasattr(request, "data"):
        if hasattr(request.data, "dict"):
            return request.data.dict()
        return request.data if isinstance(request.data, dict) else dict(request.data)
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def ok(data=None, status=200):
    return Response(data if data is not None else {}, status=status)


def err(message, status=400):
    return Response({"error": message}, status=status)


def admin_notification_recipients():
    configured = list(getattr(settings, "ADMIN_NOTIFICATION_EMAILS", []))
    firebase_admins = [admin.get("email") for admin in list_children("admins") if admin.get("email")]
    django_admins = list(User.objects.filter(is_staff=True, is_active=True).values_list("email", flat=True))
    return sorted({email for email in [*configured, *firebase_admins, *django_admins] if email})


def send_system_email(subject, message, recipients):
    recipients = [email for email in recipients if email]
    if not recipients:
        return 0
    try:
        return send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=True)
    except Exception:
        return 0


def notify_admins_tenant_signup(tenant_id, tenant):
    default_dashboard_url = f"/{settings.ADMIN_FRONTEND_PATH}/tenants"
    dashboard_url = os.getenv("ADMIN_TENANTS_URL", default_dashboard_url)
    send_system_email(
        "New tenant account pending activation",
        (
            f"A new tenant account is waiting for activation.\n\n"
            f"Business: {tenant.get('business_name')}\n"
            f"Owner: {tenant.get('owner_name')}\n"
            f"Email: {tenant.get('email')}\n"
            f"Phone: {tenant.get('phone')}\n"
            f"Tenant ID: {tenant_id}\n\n"
            f"Review and activate it here: {dashboard_url}"
        ),
        admin_notification_recipients(),
    )


def notify_tenant_activated(tenant):
    send_system_email(
        "Your Billing SaaS account is active",
        (
            f"Hello {tenant.get('owner_name') or tenant.get('business_name')},\n\n"
            f"Your {tenant.get('business_name') or 'Billing SaaS'} account has been activated. "
            "You can now sign in and finish setting up your workspace.\n\n"
            "Login: /login"
        ),
        [tenant.get("email")],
    )


def method(request, *allowed):
    return request.method.upper() in allowed


def parse_page(request):
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(200, max(1, int(request.GET.get("page_size", 50))))
    except (TypeError, ValueError):
        page_size = 50
    return page, page_size


def paginate_items(request, items):
    page, page_size = parse_page(request)
    paginator = Paginator(list(items), page_size)
    current = paginator.get_page(page)
    path = request.path
    next_url = f"{path}?page={current.next_page_number()}&page_size={page_size}" if current.has_next() else None
    prev_url = f"{path}?page={current.previous_page_number()}&page_size={page_size}" if current.has_previous() else None
    return {"results": list(current.object_list), "count": paginator.count, "pages": paginator.num_pages, "next": next_url, "previous": prev_url}


def as_collection_response(request, items):
    if request.GET.get("all") == "1" or request.GET.get("format") == "legacy":
        return ok(list(items))
    return ok(paginate_items(request, items))


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def payment_date(payment):
    return parse_date(payment.get("paid_at") or payment.get("initiated_at") or payment.get("created_at"))


def package_duration_delta(package):
    unit = str((package or {}).get("duration_unit") or "").strip().lower()
    hours = (package or {}).get("duration_hours")
    if hours not in {None, ""}:
        try:
            return timedelta(hours=float(hours))
        except (TypeError, ValueError):
            pass
    if unit in {"hour", "hours"}:
        try:
            return timedelta(hours=float((package or {}).get("duration_value") or (package or {}).get("duration_days") or 1))
        except (TypeError, ValueError):
            return timedelta(hours=1)
    try:
        return timedelta(days=int((package or {}).get("duration_days") or 1))
    except (TypeError, ValueError):
        return timedelta(days=1)


def normalized_package_payload(data):
    service_type = str((data or {}).get("service_type") or "hotspot").strip().lower()
    if service_type not in {"hotspot", "pppoe"}:
        service_type = "hotspot"
    duration_unit = "hours" if str((data or {}).get("duration_unit") or "").lower().startswith("hour") else "days"
    if service_type == "pppoe":
        duration_unit = "days"
    duration_value = float((data or {}).get("duration_value") or (data or {}).get("duration_hours") or (data or {}).get("duration_days") or 1)
    if service_type == "pppoe" and duration_value < 1:
        duration_value = 1
    duration_days = 1 if duration_unit == "hours" else int(duration_value)
    duration_hours = duration_value if duration_unit == "hours" else duration_value * 24
    return {
        "service_type": service_type,
        "duration_unit": duration_unit,
        "duration_value": duration_value,
        "duration_days": duration_days,
        "duration_hours": duration_hours,
    }


def sync_package_profile(tenant, package):
    service_type = package_service_type(package)
    if service_type == "pppoe":
        return create_ppp_profile(tenant, package.get("name"), package.get("speed"))
    return create_hotspot_profile(tenant, package.get("name"), package.get("speed"))


def _package_sync_script_for_request(request, package):
    script = _package_profile_script(package)
    if script and package_service_type(package) == "hotspot":
        script = _hotspot_captive_file_script(
            {"id": request.tenant["id"], **request.tenant},
            public_base_url(request).rstrip("/"),
        ) + script
    return script


def package_duration_label(package):
    delta = package_duration_delta(package)
    total_seconds = int(delta.total_seconds())
    if total_seconds < 86400:
        hours = max(1, round(total_seconds / 3600))
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = max(1, round(total_seconds / 86400))
    return f"{days} day{'s' if days != 1 else ''}"


def normalize_mac(value):
    raw = "".join(ch for ch in str(value or "").upper() if ch in "0123456789ABCDEF")
    if len(raw) != 12:
        return ""
    return ":".join(raw[index : index + 2] for index in range(0, 12, 2))


def format_money(value):
    return float(Decimal(str(value or 0)))


def health_payload():
    checks = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = "error"
        checks["dbError"] = f"{exc.__class__.__name__}: {str(exc)[:240]}"
    try:
        import redis
        from django.conf import settings as django_settings
        redis.Redis.from_url(django_settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5).ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = "error"
        checks["redisError"] = f"{exc.__class__.__name__}: {str(exc)[:160]}"
    try:
        checks["firebase"] = "ok" if not firebase_backup_configured() else "ok"
    except Exception:
        checks["firebase"] = "error"
    checks["status"] = "healthy" if checks["db"] == "ok" and checks["redis"] == "ok" else "degraded"
    return checks


def ensure_subscription(tenant, plan="basic"):
    plan_amounts = {"basic": 1500, "pro": 3500, "enterprise": 8000}
    now = timezone.now()
    subscription, _ = TenantSubscription.objects.get_or_create(
        tenant=tenant,
        defaults={
            "plan": plan if plan in plan_amounts else "basic",
            "amount": plan_amounts.get(plan, 1500),
            "started_at": now,
            "expires_at": now + timedelta(days=30),
        },
    )
    return subscription


def subscription_payload(subscription, include_payments=False):
    data = subscription.as_dict()
    if include_payments:
        data["payments"] = [payment.as_dict() for payment in subscription.payments.order_by("-paid_at")]
    return data


def record_subscription_payment(subscription, data, admin_email=""):
    now = timezone.now()
    current_expiry = subscription.expires_at if subscription.expires_at and subscription.expires_at > now else now
    period_start = current_expiry
    period_end = period_start + timedelta(days=subscription.billing_cycle_days)
    payment = SubscriptionPayment.objects.create(
        subscription=subscription,
        amount=Decimal(str(data.get("amount") or subscription.amount or 0)),
        currency=data.get("currency") or subscription.currency,
        method=data.get("method") or "manual",
        reference=data.get("reference") or "",
        notes=data.get("notes") or "",
        period_start=period_start,
        period_end=period_end,
        recorded_by=admin_email or "",
    )
    subscription.last_paid_at = payment.paid_at
    subscription.expires_at = period_end
    subscription.save(update_fields=["last_paid_at", "expires_at", "updated_at"])
    if subscription.tenant.status == "suspended":
        subscription.tenant.status = "active"
        subscription.tenant.save(update_fields=["status", "updated_at"])
    return payment


def react_app(request):
    index = Path(settings.BASE_DIR) / "frontend" / "dist" / "index.html"
    if not index.exists():
        raise Http404("Build the React app first with npm --prefix frontend run build")
    return FileResponse(index.open("rb"), content_type="text/html")


def react_asset(request, asset_path):
    assets_dir = (Path(settings.BASE_DIR) / "frontend" / "dist" / "assets").resolve()
    requested = (assets_dir / asset_path).resolve()
    if assets_dir not in requested.parents or not requested.exists() or not requested.is_file():
        raise Http404("Asset not found")
    content_types = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".map": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
    }
    return FileResponse(requested.open("rb"), content_type=content_types.get(requested.suffix.lower(), "application/octet-stream"))


def public_base_url(request):
    host = request.get_host()
    if "://" in host:
        host = host.split("://", 1)[1]
    forwarded_proto = (request.META.get("HTTP_X_FORWARDED_PROTO") or request.scheme or "https").split(",")[0].strip()
    request_url = normalize_public_url(f"{forwarded_proto}://{host}")

    candidates = [
        os.getenv("PUBLIC_APP_URL"),
        os.getenv("PAYSTACK_CALLBACK_BASE_URL"),
        getattr(settings, "PUBLIC_APP_URL", ""),
        getattr(settings, "PAYSTACK_CALLBACK_BASE_URL", ""),
    ]
    if request_url and "localhost" not in request_url and "127.0.0.1" not in request_url:
        candidates.insert(0, request_url)
    if not settings.DEBUG:
        candidates = [item for item in candidates if item and "localhost" not in item and "127.0.0.1" not in item]
    configured = next((item for item in candidates if item), "")
    return normalize_public_url(configured or request_url)


def tenant_theme_payload(tenant):
    return {
        "business_name": tenant.get("business_name") or "",
        "owner_name": tenant.get("owner_name") or "",
        "phone": tenant.get("phone") or "",
        "support_email": tenant.get("support_email") or tenant.get("email") or "",
        "theme_color": tenant.get("theme_color") or "#fa8200",
        "font": tenant.get("font") or "Work Sans",
        "dark_mode": bool(tenant.get("dark_mode")),
        "theme_mode": tenant.get("theme_mode") or ("dark" if tenant.get("dark_mode") else "light"),
        "business_number": tenant.get("business_number") or "",
        "bank_code": tenant.get("bank_code") or "",
        "bank_name": tenant.get("bank_name") or "",
        "bank_account_number": tenant.get("bank_account_number") or "",
        "payment_methods": tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else ["bank"],
        "daraja_consumer_key": tenant.get("daraja_consumer_key") or "",
        "daraja_consumer_secret": tenant.get("daraja_consumer_secret") or "",
        "daraja_shortcode": tenant.get("daraja_shortcode") or "",
        "daraja_passkey": tenant.get("daraja_passkey") or "",
        "daraja_till_number": tenant.get("daraja_till_number") or "",
        "daraja_shortcode_type": tenant.get("daraja_shortcode_type") or "CustomerBuyGoodsOnline",
        "daraja_environment": tenant.get("daraja_environment") or "sandbox",
        "paystack_subaccount_code": tenant.get("paystack_subaccount_code") or "",
        "paystack_subaccount_status": tenant.get("paystack_subaccount_status") or "not_created",
        "paystack_platform_percentage": tenant.get("paystack_platform_percentage") or os.getenv("PAYSTACK_PLATFORM_PERCENTAGE", "1"),
    }


def create_or_update_tenant_subaccount(tenant_id, tenant_data, data):
    bank_code = str(data.get("bank_code") or tenant_data.get("bank_code") or "").strip()
    account_number = str(data.get("bank_account_number") or tenant_data.get("bank_account_number") or "").strip()
    if not bank_code or not account_number:
        return {"paystack_subaccount_status": "missing_bank_details"}

    subaccount = create_paystack_subaccount(
        {"id": tenant_id, **tenant_data, **data},
        bank_code,
        account_number,
        business_number=data.get("business_number") or tenant_data.get("business_number"),
        percentage_charge=data.get("paystack_platform_percentage") or tenant_data.get("paystack_platform_percentage"),
    )
    return {
        "paystack_subaccount_code": subaccount.get("subaccount_code"),
        "paystack_subaccount_id": subaccount.get("id"),
        "paystack_subaccount_status": "active",
        "paystack_subaccount_created_at": iso_now(),
    }


@csrf_exempt
@api_view(["GET"])
def health(request):
    checks = health_payload()
    return ok(
        {
            "ok": True,
            "service": "billing-saas-django",
            "cronEnabled": os.getenv("ENABLE_CRON") == "true",
            "config": {
                "nodeEnv": os.getenv("NODE_ENV", "development"),
                "databaseEngine": settings.DATABASES["default"]["ENGINE"],
                "databaseName": str(settings.DATABASES["default"]["NAME"]),
                "firebaseBackupEnabled": firebase_backup_configured(),
                "jwtSecretSet": bool(os.getenv("JWT_SECRET")),
                "adminJwtSecretSet": bool(os.getenv("ADMIN_JWT_SECRET")),
            },
            **checks,
        }
    )


@csrf_exempt
@api_view(["POST"])
def auth_register(request):
    data = body(request)
    missing = [field for field in ["business_name", "owner_name", "email", "phone", "password"] if not data.get(field)]
    if missing:
        return ok({"message": f"Missing fields: {', '.join(missing)}"}, 400)
    email = data["email"].lower().strip()
    if find_child_by_field("tenants", "email", email):
        return ok({"message": "Email already registered"}, 400)

    tenant_ref = ref("tenants").push(
        {
            "business_name": data["business_name"],
            "owner_name": data["owner_name"],
            "email": email,
            "phone": data["phone"],
            "password": hash_password(data["password"]),
            "business_number": str(data.get("business_number") or "").strip(),
            "bank_code": str(data.get("bank_code") or "").strip(),
            "bank_name": str(data.get("bank_name") or "").strip(),
            "bank_account_number": str(data.get("bank_account_number") or "").strip(),
            "mikrotik_host": "",
            "mikrotik_user": "",
            "mikrotik_pass": "",
            "mikrotik_port": 8728,
            "paystack_secret_key": "",
            "paystack_subaccount_code": "",
            "paystack_bearer": "subaccount",
            "paystack_currency": os.getenv("PAYSTACK_CURRENCY", "KES"),
            "paystack_platform_percentage": os.getenv("PAYSTACK_PLATFORM_PERCENTAGE", "1"),
            "paystack_subaccount_status": "not_created",
            "theme_color": data.get("theme_color") or "#fa8200",
            "dark_mode": False,
            "status": "pending_setup",
            "created_at": iso_now(),
        }
    )
    tenant_data = ref(f"tenants/{tenant_ref.key}").get() or {}
    subaccount_status = {"paystack_subaccount_status": "missing_bank_details"}
    if tenant_data.get("bank_code") and tenant_data.get("bank_account_number"):
        try:
            subaccount_status = create_or_update_tenant_subaccount(tenant_ref.key, tenant_data, data)
        except PaymentProviderError as exc:
            subaccount_status = {"paystack_subaccount_status": "failed", "paystack_subaccount_error": exc.detail}
        ref(f"tenants/{tenant_ref.key}").update(subaccount_status)
    notify_admins_tenant_signup(tenant_ref.key, {**tenant_data, **subaccount_status})
    return ok({"success": True, "message": "Business registered successfully. An admin will activate your account before you can sign in.", "tenantId": tenant_ref.key, **subaccount_status})


@csrf_exempt
@api_view(["POST"])
def auth_login(request):
    data = body(request)
    if not data.get("email") or not data.get("password"):
        return ok({"message": "Email and password are required"}, 400)
    email = str(data["email"]).lower().strip()
    tenant_obj = Tenant.objects.filter(email__iexact=email).first()
    if not tenant_obj:
        return ok({"message": "Business not found"}, 404)
    if not check_password(str(data["password"]).strip(), tenant_obj.password):
        return ok({"message": "Wrong password"}, 401)
    tenant = tenant_obj.as_dict(include_id=True)
    if tenant.get("status") != "active":
        return ok({"message": "Your account is pending admin activation. You will receive an email once it is active."}, 403)
    try:
        token = tenant_token(tenant["id"])
    except Exception as exc:
        return ok({"message": f"Server configuration error: {exc}"}, 500)
    return ok(
        {
            "success": True,
            "token": token,
            "tenant": {"id": tenant["id"], "business_name": tenant.get("business_name"), "email": tenant.get("email"), **tenant_theme_payload(tenant)},
        }
    )


@csrf_exempt
@api_view(["GET"])
def public_site(request):
    return ok({**DEFAULT_SITE, **(ref("site_settings").get() or {})})


@csrf_exempt
@api_view(["GET"])
def public_stats(request):
    tenants = list_children("tenants")
    active_tenants = [tenant for tenant in tenants if tenant.get("status") == "active"]
    customers = []
    for tenant in tenants:
        customers.extend(list_children(f"tenants/{tenant['id']}/customers"))
    active_customers = [customer for customer in customers if customer.get("status") == "active"]
    return ok(
        {
            "totalTenants": len(tenants),
            "activeTenants": len(active_tenants),
            "totalCustomers": len(customers),
            "activeCustomers": len(active_customers),
        }
    )


@csrf_exempt
@api_view(["GET"])
def public_tenant(request, tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return ok({"message": "Tenant not found"}, 404)
    return ok({"id": tenant_id, "business_name": tenant.get("business_name"), "phone": tenant.get("phone"), "status": tenant.get("status"), "logo_url": tenant.get("logo_url") or ""})


@csrf_exempt
@api_view(["GET"])
def public_packages(request, tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return ok({"message": "Tenant not found"}, 404)
    if tenant.get("status") == "suspended":
        return ok({"message": "Tenant is not accepting payments"}, 403)
    requested_service = str(request.GET.get("service_type") or "").strip().lower()
    packages = _public_packages_for_tenant(tenant_id, requested_service)
    return ok(sorted(packages, key=lambda item: float(item.get("price") or 0)))


@csrf_exempt
@api_view(["GET"])
def public_pppoe_profile(request, tenant_id):
    username = str(request.GET.get("username") or "").strip()
    if not username:
        return ok({"message": "PPPoE username is required"}, 400)
    tenant = ref(f"tenants/{tenant_id}").get()
    customer = next((item for item in list_children(f"tenants/{tenant_id}/customers") if str(item.get("username") or "").lower() == username.lower()), None)
    if not tenant or not customer or str(customer.get("service_type") or "pppoe").lower() != "pppoe":
        return ok({"message": "PPPoE customer profile not found"}, 404)
    usage_bytes = 0
    active_sessions = 0
    try:
        from .models import RadiusSession as RadiusSessionModel
        from django.db.models import Sum
        sessions = RadiusSessionModel.objects.filter(tenant_id=tenant_id, customer__username__iexact=username)
        usage_bytes = int(sessions.aggregate(total=Sum("input_octets") + Sum("output_octets")).get("total") or 0)
        active_sessions = sessions.filter(stopped_at__isnull=True).count()
    except Exception:
        pass
    return ok({"tenant": {"id": tenant_id, "business_name": tenant.get("business_name"), "logo_url": tenant.get("logo_url") or ""}, "customer": {"name": customer.get("name"), "username": customer.get("username"), "phone": customer.get("phone"), "package": customer.get("package"), "status": customer.get("status"), "expiry_date": customer.get("expiry_date")}, "usage": {"bytes": usage_bytes, "megabytes": round(usage_bytes / 1048576, 2), "active_sessions": active_sessions}})


def _public_package_payload(pkg):
    return {
        **{key: pkg.get(key) for key in ["id", "name", "speed", "duration_days", "duration_unit", "duration_value", "duration_hours", "price", "service_type"]},
        "service_type": package_service_type(pkg),
        "duration_label": package_duration_label(pkg),
    }


def _public_packages_for_tenant(tenant_id, requested_service=""):
    return [
        _public_package_payload(pkg)
        for pkg in list_children(f"tenants/{tenant_id}/packages")
        if pkg.get("is_active") is not False and (requested_service not in {"hotspot", "pppoe"} or package_service_type(pkg) == requested_service)
    ]


def _captive_packages(tenant_id):
    hotspot_packages = _public_packages_for_tenant(tenant_id, "hotspot")
    if hotspot_packages:
        return hotspot_packages
    return _public_packages_for_tenant(tenant_id)


def _html_page(title, body, status=200):
    return HttpResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    body{{margin:0;font-family:Arial,sans-serif;background:#f3f6fb;color:#0f172a}}
    header{{background:#183b60;color:white;padding:24px 18px}}
    main{{max-width:860px;margin:0 auto;padding:18px}}
    .card{{background:white;border:1px solid #dbe4f0;border-radius:8px;padding:16px;margin:12px 0;box-shadow:0 2px 10px rgba(15,23,42,.06)}}
    .pkg{{display:grid;gap:10px;grid-template-columns:1fr;align-items:end}}
    @media(min-width:720px){{.pkg{{grid-template-columns:1.2fr .7fr .8fr auto}}}}
    input,button{{font:inherit;border-radius:6px;border:1px solid #cbd5e1;padding:10px 12px}}
    button{{background:#f97316;color:white;border-color:#f97316;font-weight:700;cursor:pointer}}
    .muted{{color:#64748b;font-size:14px}} .price{{font-weight:800;color:#0f172a}}
    .alert{{background:#fff7ed;border:1px solid #fed7aa;color:#9a3412;border-radius:8px;padding:12px;margin:12px 0}}
  </style>
</head>
<body>{body}</body>
</html>""",
        status=status,
        content_type="text/html",
    )


def _router_is_agent_linked(tenant):
    return bool((tenant or {}).get("mikrotik_last_seen_at") or (tenant or {}).get("mikrotik_router_snapshot") or (tenant or {}).get("mikrotik_provisioning_status") in {"script_downloaded", "completed"})


def _limited_router_status_payload(snapshot, assignments=None, source="provisioning_snapshot", message="Showing the latest configuration reported by the router agent."):
    payload = {**_empty_router_snapshot(), **(snapshot or {})}
    def port_rank(item):
        name = str(item.get("name") or "").lower()
        if name.startswith("ether"):
            return (0, name)
        if name.startswith(("wlan", "wifi")):
            return (1, name)
        return (2, name)

    interfaces = sorted(
        [
            item
            for item in list(payload.get("interfaces") or [])
            if (
                str(item.get("type") or "").lower() in {"ether", "wlan", "wifi"}
                or str(item.get("name") or "").lower().startswith(("ether", "wlan", "wifi"))
            )
            and "bridge" not in str(item.get("name") or "").lower()
        ],
        key=port_rank,
    )
    payload["interfaces"] = interfaces[:6]
    payload["assignments"] = assignments or {}
    payload["source"] = source
    payload["message"] = message
    return payload


def _linked_router_from_tenant(tenant):
    snapshot = (tenant or {}).get("mikrotik_router_snapshot") or {}
    device = snapshot.get("device") or {}
    return {
        "id": "primary",
        "board_name": device.get("board_name") or (tenant or {}).get("mikrotik_detected_board") or "MikroTik Router",
        "identity": (tenant or {}).get("mikrotik_detected_identity") or "",
        "version": device.get("version") or (tenant or {}).get("mikrotik_detected_version") or "",
        "status": "suspended" if (tenant or {}).get("mikrotik_router_suspended") else "online" if (tenant or {}).get("mikrotik_last_seen_at") else "offline",
        "provisioning_status": (tenant or {}).get("mikrotik_provisioning_status") or "",
        "last_seen_at": (tenant or {}).get("mikrotik_last_seen_at") or "",
        "last_seen_ip": (tenant or {}).get("mikrotik_last_seen_ip") or "",
        "cpu_load": device.get("cpu_load"),
        "free_memory": device.get("free_memory"),
        "interface_count": len((snapshot or {}).get("interfaces") or []),
    }


def _update_linked_router(tenant_id, tenant_data, **updates):
    router = {**_linked_router_from_tenant(tenant_data), **updates}
    existing = dict((tenant_data or {}).get("linked_routers") or {})
    identity = str(router.get("identity") or "").strip().lower()
    ip = str(router.get("last_seen_ip") or "").strip()
    key = next((name for name, item in existing.items() if identity and str(item.get("identity") or "").strip().lower() == identity), None)
    key = key or next((name for name, item in existing.items() if ip and item.get("last_seen_ip") == ip), None)
    if not key:
        key = "primary" if not existing else f"router-{len(existing) + 1}"
    existing[key] = {**existing.get(key, {}), **router, "id": key}
    ref(f"tenants/{tenant_id}").update({"linked_routers": existing})
    return existing[key]


@csrf_exempt
@api_view(["GET"])
def captive_portal_page(request, tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return _html_page("Portal unavailable", "<main><div class='alert'>Tenant not found.</div></main>", 404)
    if tenant.get("status") == "suspended":
        return _html_page("Portal unavailable", "<main><div class='alert'>This provider is not accepting payments.</div></main>", 403)

    reference = request.GET.get("reference") or request.GET.get("trxref")
    payment_notice = ""
    if reference:
        _, _, payment = find_payment_by_paystack_reference(reference, tenant_id=tenant_id)
        if payment and payment.get("status") == "success":
            router_ip = payment.get("router_ip") or request.GET.get("ip") or ""
            username = payment.get("access_username") or payment.get("username") or ""
            password = payment.get("access_password") or ""
            login_url = f"http://{router_ip}/login?username={username}&password={password}" if router_ip and username and password else ""
            auto_redirect = f"<script>setTimeout(function(){{ location.href = {json.dumps(login_url)}; }}, 1200);</script>" if login_url else ""
            payment_notice = f"""
              <div class="card">
                <strong>Payment successful. Internet access is ready.</strong>
                <p class="muted">Package: {html.escape(str(payment.get('package_name') or ''))}</p>
                <p>Username: <strong>{html.escape(str(username))}</strong></p>
                <p>Password: <strong>{html.escape(str(password))}</strong></p>
                {f"<p><a href='{html.escape(login_url)}'>Connect now</a></p>" if login_url else ""}
              </div>
              {auto_redirect}
            """
        else:
            payment_notice = "<div class='alert'>Payment is not confirmed yet. If you have paid, wait a moment and refresh this page.</div>"

    packages = sorted(_captive_packages(tenant_id), key=lambda item: float(item.get("price") or 0))
    hidden = "".join(
        f"<input type='hidden' name='{html.escape(key)}' value='{html.escape(str(request.GET.get(key) or ''))}'>"
        for key in ["ip", "mac", "error"]
        if request.GET.get(key)
    )
    if packages:
        configured_methods = tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else []
        selected_daraja_method = next((str(item).strip().lower() for item in configured_methods if str(item).strip().lower() in {"daraja_paybill", "daraja_buygoods"}), "")
        package_html = "".join(
            f"""
            <form class="card pkg" method="post" action="/api/captive/{html.escape(str(tenant_id))}/pay">
              <input type="hidden" name="package_id" value="{html.escape(str(pkg.get('id')))}">
              <input type="hidden" name="service_type" value="{html.escape(str(pkg.get('service_type') or 'hotspot'))}">
              {f"<input type='hidden' name='payment_method' value='{html.escape(selected_daraja_method)}'>" if selected_daraja_method else ''}
              {hidden}
              <div>
                <strong>{html.escape(str(pkg.get('name') or 'Package'))}</strong>
                <div class="muted">{html.escape(str(pkg.get('speed') or ''))} · {html.escape(str(pkg.get('duration_label') or ''))}</div>
              </div>
              <div class="price">KES {html.escape(str(pkg.get('price') or 0))}</div>
              {('<input name="username" required placeholder="PPPoE username">' if pkg.get('service_type') == 'pppoe' else '')}
              <input name="phone" inputmode="tel" required placeholder="M-Pesa/phone number">
              <button type="submit">Buy</button>
            </form>"""
            for pkg in packages
        )
    else:
        total_packages = len(list_children(f"tenants/{tenant_id}/packages"))
        if total_packages:
            package_html = "<div class='alert'>Packages exist, but none are active. Please contact the provider.</div>"
        else:
            package_html = "<div class='alert'>No packages are configured yet. Please contact the provider.</div>"

    voucher_html = f"""
      <div class="card">
        <strong>Use a voucher</strong>
        <p class="muted">Enter the voucher code provided by your provider.</p>
        <form method="post" action="/api/public/{html.escape(str(tenant_id))}/voucher-login">
          {hidden}
          <input name="code" required placeholder="Voucher code">
          <button type="submit">Login with voucher</button>
        </form>
        <p class="muted">Already bought a package? Sign in with the username and password sent to you.</p>
        <form method="post" action="/api/public/{html.escape(str(tenant_id))}/voucher-login">
          {hidden}
          <input name="username" required placeholder="Username">
          <input name="password" required type="password" placeholder="Password">
          <button type="submit">Sign in</button>
        </form>
      </div>
    """
    body_html = f"""
      <header><h1>{html.escape(str(tenant.get('business_name') or 'Internet packages'))}</h1><p>Choose a package and pay to access the internet.</p></header>
      <main>
        <div class="card">
          <strong>Captive portal</strong>
          <p class="muted">You are connected to the billing network. Internet access is enabled after successful payment.</p>
        </div>
        {payment_notice}
        {voucher_html}
        {package_html}
      </main>
    """
    response = _html_page(f"{tenant.get('business_name') or 'Hotspot'} packages", body_html)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


@csrf_exempt
@api_view(["GET"])
def captive_hotspot_file(request, tenant_id, page):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return HttpResponse("Not found", status=404, content_type="text/plain")

    portal_url = captive_portal_url({"id": tenant_id, **tenant}, public_base_url(request).rstrip("/"))
    redirect_html = hotspot_redirect_html(portal_url)
    files = {
        "login.html": hotspot_login_redirect_html(portal_url),
        "alogin.html": hotspot_alogin_redirect_html(portal_url),
        "redirect.html": redirect_html,
        "error.html": hotspot_error_redirect_html(portal_url),
        "status.html": redirect_html,
        "rlogin.html": redirect_html,
        "radvert.html": redirect_html,
    }
    content = files.get(str(page or "").strip().lower())
    if not content:
        return HttpResponse("Not found", status=404, content_type="text/plain")
    response = HttpResponse(content, content_type="text/html")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


@csrf_exempt
@api_view(["POST"])
def captive_portal_pay(request, tenant_id):
    data = body(request)
    response = public_pay(request, tenant_id)
    payload = getattr(response, "data", {}) or {}
    if response.status_code >= 400:
        return _html_page("Payment unavailable", f"<main><div class='alert'>{html.escape(str(payload.get('message') or payload.get('error') or 'Could not start payment'))}</div><p><a href='/api/captive/{html.escape(str(tenant_id))}'>Back to packages</a></p></main>", response.status_code)
    authorization_url = payload.get("authorizationUrl")
    if authorization_url:
        return redirect(authorization_url)
    return _html_page("Payment unavailable", "<main><div class='alert'>Payment checkout was not returned. Please try again.</div></main>", 502)


@csrf_exempt
@api_view(["POST"])
def public_pay(request, tenant_id):
    data = body(request)
    if not data.get("package_id") or not data.get("phone"):
        return ok({"message": "Package and phone number are required"}, 400)
    tenant_data = ref(f"tenants/{tenant_id}").get()
    if not tenant_data:
        return ok({"message": "Tenant not found"}, 404)
    if tenant_data.get("status") == "suspended":
        return ok({"message": "Tenant is not accepting payments"}, 403)
    pkg = ref(f"tenants/{tenant_id}/packages/{data['package_id']}").get()
    if not pkg or pkg.get("is_active") is False:
        return ok({"message": "Package not found"}, 404)
    tenant = {"id": tenant_id, **tenant_data}
    phone = normalize_phone(data["phone"])
    router_ip = str(data.get("ip") or data.get("router_ip") or "").strip()
    router_mac = str(data.get("mac") or data.get("router_mac") or "").strip()
    service_type = str(data.get("service_type") or "hotspot").strip().lower()
    if service_type not in {"hotspot", "pppoe", "tv"}:
        return ok({"message": "Invalid service type"}, 400)
    package_type = package_service_type(pkg)
    if service_type in {"hotspot", "pppoe"} and service_type != package_type:
        return ok({"message": f"This package is only available for {package_type.upper()} customers"}, 400)
    if service_type == "tv" and package_type != "hotspot":
        return ok({"message": "TV MAC access is only available for hotspot packages"}, 400)
    customer = None
    mac_address = ""
    if service_type == "pppoe":
        username = str(data.get("username") or "").strip()
        if not username:
            return ok({"message": "PPPoE username is required"}, 400)
        customer = next(
            (
                item
                for item in list_children(f"tenants/{tenant_id}/customers")
                if str(item.get("username") or "").lower() == username.lower()
            ),
            None,
        )
        if not customer:
            return ok({"message": "PPPoE account not found. Please contact your ISP."}, 404)
        phone = normalize_phone(data.get("phone") or customer.get("phone"))
    elif service_type == "tv":
        mac_address = normalize_mac(data.get("mac_address"))
        if not mac_address:
            return ok({"message": "Enter a valid TV MAC address"}, 400)
    configured_methods = tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else []
    uses_daraja = tenant_uses_daraja(tenant)
    payment_ref = ref(f"tenants/{tenant_id}/payments").push(
        {
            "customer_id": customer.get("id") if customer else None,
            "customer_name": customer.get("name") if customer else None,
            "package_id": data["package_id"],
            "package_name": pkg.get("name"),
            "amount": float(pkg.get("price") or 0),
            "payment_code": None,
            "phone": phone,
            "status": "pending",
            "paid_at": None,
            "initiated_at": iso_now(),
            "service_type": service_type,
            "username": customer.get("username") if customer else None,
            "mac_address": mac_address,
            "router_ip": router_ip,
            "router_mac": router_mac,
            "source": "customer_portal",
            "provider": "mpesa" if uses_daraja else "paystack",
        }
    )
    try:
        if uses_daraja:
            daraja_method = str(data.get("payment_method") or next((item for item in configured_methods if str(item).startswith("daraja_")), "")).strip().lower()
            if daraja_method not in {"daraja_paybill", "daraja_buygoods"}:
                daraja_method = "daraja_buygoods" if tenant.get("daraja_till_number") else "daraja_paybill"
            checkout = initiate_daraja_payment(
                tenant,
                payment_ref.key,
                pkg.get("price"),
                phone,
                description=f"{pkg.get('name')} internet package",
                metadata={
                    "package_id": data["package_id"],
                    "package_name": pkg.get("name"),
                    "service_type": service_type,
                    "username": customer.get("username") if customer else None,
                    "mac_address": mac_address,
                    "router_ip": router_ip,
                    "router_mac": router_mac,
                },
                payment_method=daraja_method,
            )
            payment_ref.update({"daraja_checkout_request_id": checkout.get("checkout_request_id"), "daraja_merchant_request_id": checkout.get("merchant_request_id")})
            return ok({
                "success": True,
                "message": checkout.get("customer_message") or "Check your phone and enter your M-Pesa PIN to complete payment.",
                "paymentId": payment_ref.key,
                "provider": "mpesa",
                "checkoutRequestId": checkout.get("checkout_request_id"),
            }, 201)
        checkout = initiate_paystack_payment(
            tenant,
            payment_ref.key,
            pkg.get("price"),
            email=data.get("email"),
            phone=phone,
            description=f"{pkg.get('name')} internet package",
            metadata={
                "package_id": data["package_id"],
                "package_name": pkg.get("name"),
                "service_type": service_type,
                "username": customer.get("username") if customer else None,
                "mac_address": mac_address,
                "router_ip": router_ip,
                "router_mac": router_mac,
            },
        )
    except PaymentProviderError as exc:
        payment_ref.update({"status": "failed", "failed_at": iso_now(), "callback_result_desc": exc.detail})
        return ok({"success": False, "message": exc.public_message, "paymentId": payment_ref.key}, exc.status_code)
    except Exception as exc:
        payment_ref.update({"status": "failed", "failed_at": iso_now(), "callback_result_desc": str(exc)})
        return ok({"success": False, "message": "Payment gateway is temporarily unavailable. Please try again later.", "paymentId": payment_ref.key}, 503)
    payment_ref.update(
        {
            "paystack_reference": checkout.get("reference"),
            "paystack_access_code": checkout.get("access_code"),
            "paystack_authorization_url": checkout.get("authorization_url"),
            "paystack_customer_email": checkout.get("customer_email"),
            "currency": checkout.get("currency"),
            "checkout_requested_at": iso_now(),
        }
    )
    return ok({"success": True, "message": "Redirecting to Paystack checkout", "paymentId": payment_ref.key, "reference": checkout.get("reference"), "authorizationUrl": checkout.get("authorization_url")})


@csrf_exempt
@api_view(["POST"])
def public_redeem(request, tenant_id):
    receipt_code = body(request).get("receipt_code") or body(request).get("payment_code")
    if not receipt_code:
        return ok({"message": "Payment reference is required"}, 400)
    payment = None
    for item in list_children(f"tenants/{tenant_id}/payments"):
        candidates = [item.get("payment_code"), item.get("paystack_reference")]
        if any(str(candidate or "").upper() == str(receipt_code).strip().upper() for candidate in candidates):
            payment = item
            break
    if not payment or payment.get("status") != "success":
        return ok({"message": "Paid transaction not found"}, 404)
    if not payment.get("access_expires_at") or str(payment["access_expires_at"]) <= iso_now():
        return ok({"message": "This package has expired"}, 410)
    tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})}
    if payment.get("access_username"):
        set_customer_enabled(tenant, payment["access_username"], payment.get("service_type", "hotspot"), True)
    return ok(
        {
            "success": True,
            "package_name": payment.get("package_name"),
            "service_type": payment.get("service_type"),
            "phone": payment.get("phone"),
            "username": payment.get("access_username"),
            "password": payment.get("access_password"),
            "mac_address": payment.get("access_mac_address") or payment.get("mac_address"),
            "router_ip": payment.get("router_ip"),
            "router_mac": payment.get("router_mac"),
            "expires_at": payment.get("access_expires_at"),
        }
    )


@csrf_exempt
@api_view(["POST"])
def public_voucher_login(request, tenant_id):
    data = body(request)
    code = str(data.get("code") or "").strip()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "").strip()
    if not code and (not username or not password):
        return ok({"message": "Voucher code is required"}, 400)
    voucher = next((item for item in list_children(f"tenants/{tenant_id}/vouchers") if str(item.get("code") or "").lower() == (code or username).lower()), None)
    if not voucher or voucher.get("status") != "active" or (not code and str(voucher.get("password") or "") != password):
        return ok({"message": "Invalid or inactive voucher credentials"}, 401)
    tenant = ref(f"tenants/{tenant_id}").get() or {}
    return ok({"success": True, "username": voucher.get("username"), "password": voucher.get("password"), "router_ip": data.get("router_ip") or tenant.get("mikrotik_last_seen_ip") or "", "package_name": voucher.get("package"), "router_status": voucher.get("router_status")})


@csrf_exempt
@api_view(["GET"])
def public_verify(request, tenant_id):
    reference = request.GET.get("reference") or request.GET.get("trxref")
    if not reference:
        return ok({"message": "Payment reference is required"}, 400)
    found_tenant_id, payment_id, payment = find_payment_by_paystack_reference(reference, tenant_id=tenant_id)
    if found_tenant_id != str(tenant_id) or not payment:
        return ok({"message": "Payment not found"}, 404)
    if payment.get("status") != "success":
        tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})}
        try:
            verified = verify_paystack_transaction(tenant, reference)
            if verified.get("status") == "success":
                if not complete_paystack_payment(verified):
                    return ok({"success": False, "status": "failed", "message": "The paid amount did not match the package amount."}, 400)
                payment = ref(f"tenants/{tenant_id}/payments/{payment_id}").get() or payment
            else:
                return ok({"success": False, "status": "failed", "message": verified.get("gateway_response") or "Payment was not successful"}, 400)
        except Exception:
            return ok({"success": False, "status": payment.get("status") or "pending", "message": "Payment verification is still pending. Please contact your ISP if this continues."}, 202)
    return ok(
        {
            "success": payment.get("status") == "success",
            "status": payment.get("status"),
            "package_name": payment.get("package_name"),
            "service_type": payment.get("service_type"),
            "phone": payment.get("phone"),
            "username": payment.get("access_username"),
            "password": payment.get("access_password"),
            "mac_address": payment.get("access_mac_address") or payment.get("mac_address"),
            "router_ip": payment.get("router_ip"),
            "router_mac": payment.get("router_mac"),
            "expires_at": payment.get("access_expires_at"),
            "paymentId": payment_id,
        }
    )


@csrf_exempt
@api_view(["GET", "PATCH", "DELETE"])
@tenant_required
def customers(request, customer_id=None):
    tenant = request.tenant
    if method(request, "GET") and not customer_id:
        return as_collection_response(request, list_children(f"tenants/{tenant['id']}/customers"))
    if method(request, "GET") and customer_id:
        customer = ref(f"tenants/{tenant['id']}/customers/{customer_id}").get()
        if not customer:
            return ok({"message": "Customer not found"}, 404)
        return ok({"id": customer_id, **customer})
    if method(request, "PATCH") and customer_id:
        customer = ref(f"tenants/{tenant['id']}/customers/{customer_id}").get()
        if not customer:
            return ok({"message": "Customer not found"}, 404)
        data = body(request)
        allowed = ["name", "phone", "username", "package", "service_type", "status", "expiry_date", "auto_reconnect"]
        updates = {field: data[field] for field in allowed if field in data}
        if not updates:
            return ok({"message": "No customer fields provided"}, 400)
        updates["updated_at"] = iso_now()
        ref(f"tenants/{tenant['id']}/customers/{customer_id}").update(updates)
        return ok({"success": True, "message": "Customer updated", "customer": {"id": customer_id, **customer, **updates}})
    if method(request, "DELETE") and customer_id:
        customer = ref(f"tenants/{tenant['id']}/customers/{customer_id}").get()
        if not customer:
            return ok({"message": "Customer not found"}, 404)
        try:
            delete_router_customer(tenant, customer.get("username"), customer.get("service_type") or "pppoe")
        except Exception:
            pass
        ref(f"tenants/{tenant['id']}/customers/{customer_id}").delete()
        return ok({"success": True, "message": "Customer deleted"})
    return ok({"message": "Method not allowed"}, 405)


@csrf_exempt
@api_view(["POST"])
@tenant_required
def customer_add(request):
    data = body(request)
    required = ["name", "phone", "username", "password", "package_name"]
    if any(not data.get(field) for field in required):
        return ok({"message": "Name, phone, username, password, and package are required"}, 400)
    if any(str(c.get("username", "")).lower() == str(data["username"]).lower() for c in list_children(f"tenants/{request.tenant['id']}/customers")):
        return ok({"message": "A customer with this username already exists"}, 409)
    service_type = str(data.get("service_type") or "pppoe").strip().lower()
    if service_type not in {"pppoe", "hotspot"}:
        return ok({"message": "Customer service type must be PPPoE or Hotspot"}, 400)
    provision = data.get("provision_mikrotik", True)
    if provision:
        if not has_mikrotik_credentials(request.tenant):
            return ok({"message": "Configure MikroTik credentials before provisioning customers"}, 400)
        pkg = find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", data["package_name"])
        if not pkg:
            return ok({"message": f"Package \"{data['package_name']}\" was not found"}, 404)
        try:
            if service_type == "pppoe":
                create_ppp_profile(request.tenant, pkg["name"], pkg.get("speed"))
            else:
                create_hotspot_profile(request.tenant, pkg["name"], pkg.get("speed"))
            upsert_customer_access(request.tenant, {**data, "service_type": service_type}, disabled=True)
        except (TimeoutError, OSError):
            _queue_router_command(request, {
                "type": "sync_secrets",
                "script": _customer_secret_script({**data, "package": data["package_name"], "speed": pkg.get("speed"), "service_type": service_type, "status": "inactive"}),
            })
    new_ref = ref(f"tenants/{request.tenant['id']}/customers").push(
        {
            "name": data["name"],
            "phone": data["phone"],
            "username": data["username"],
            "password": data["password"],
            "package": data["package_name"],
            "service_type": service_type,
            "provisioning_status": "provisioned" if provision else "not_requested",
            "provisioning_message": f"{service_type.upper()} access created on MikroTik and kept disabled until payment" if provision else None,
            "status": "inactive",
            "expiry_date": None,
            "auto_reconnect": True,
            "created_at": iso_now(),
        }
    )
    # Sync to Postgres + RADIUS if tenant has RADIUS enabled
    if request.tenant.get("radius_enabled"):
        try:
            from .radius_provisioning import upsert_pg_customer, sync_radius_customer
            from .models import Tenant as TenantModel
            tenant_obj = TenantModel.objects.get(pk=request.tenant["id"])
            pg_customer = upsert_pg_customer(tenant_obj, {**data, "service_type": service_type})
            if pg_customer:
                sync_radius_customer(tenant_obj, pg_customer)
        except Exception:
            pass
    return ok({"success": True, "message": "Customer added", "customerId": new_ref.key})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def customer_provision(request, customer_id):
    customer = ref(f"tenants/{request.tenant['id']}/customers/{customer_id}").get()
    if not customer:
        return ok({"message": "Customer not found"}, 404)
    if not has_mikrotik_credentials(request.tenant):
        return ok({"message": "Configure MikroTik credentials before provisioning customers"}, 400)
    service_type = customer.get("service_type") or "pppoe"
    pkg = find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", customer.get("package"))
    try:
        if pkg:
            if service_type == "pppoe":
                create_ppp_profile(request.tenant, pkg["name"], pkg.get("speed"))
            elif service_type == "hotspot":
                create_hotspot_profile(request.tenant, pkg["name"], pkg.get("speed"))
        upsert_customer_access(request.tenant, {**customer, "package_name": customer.get("package"), "service_type": service_type}, disabled=customer.get("status") != "active")
    except (TimeoutError, OSError):
        _queue_router_command(request, {
            "type": "sync_secrets",
            "script": _customer_secret_script({**customer, "package_name": customer.get("package"), "speed": (pkg or {}).get("speed"), "service_type": service_type}),
        })
    ref(f"tenants/{request.tenant['id']}/customers/{customer_id}").update(
        {"provisioning_status": "provisioned", "service_type": service_type, "auto_reconnect": True, "provisioning_message": f"{service_type.upper()} access synced on MikroTik", "provisioned_at": iso_now()}
    )
    # Sync to Postgres + RADIUS if tenant has RADIUS enabled
    if request.tenant.get("radius_enabled"):
        try:
            from .radius_provisioning import upsert_pg_customer, sync_radius_customer
            from .models import Tenant as TenantModel
            tenant_obj = TenantModel.objects.get(pk=request.tenant["id"])
            pg_customer = upsert_pg_customer(tenant_obj, customer)
            if pg_customer:
                sync_radius_customer(tenant_obj, pg_customer)
        except Exception:
            pass
    return ok({"success": True, "message": "Customer provisioned on MikroTik"})


@csrf_exempt
@api_view(["GET"])
@tenant_required
def customer_hotspot_portal(request):
    tenant_id = request.tenant["id"]
    tenant = {"id": tenant_id, **request.tenant}
    portal_url = captive_portal_url(tenant, public_base_url(request).rstrip("/"))
    return ok(
        {
            "tenant_id": tenant_id,
            "portal_url": portal_url,
            "fallback_portal_url": portal_url,
            "hotspot_url": portal_url,
            "hotspot_profile": "billing-saas-captive",
            "description": "Assign the customer-facing router port as Hotspot. The billing-saas-captive profile redirects unpaid users to this portal so they can select a package and pay before access is activated.",
        }
    )


@csrf_exempt
@api_view(["GET", "PATCH", "DELETE"])
@tenant_required
def packages(request, package_id=None):
    tenant_id = request.tenant["id"]
    if method(request, "GET") and not package_id:
        return as_collection_response(request, list_children(f"tenants/{tenant_id}/packages"))
    if method(request, "PATCH") and package_id:
        data = body(request)
        updates = {key: data[key] for key in ["name", "speed", "duration_days", "duration_unit", "duration_value", "duration_hours", "price", "is_active", "service_type"] if key in data}
        if not updates:
            return ok({"message": "No package fields provided"}, 400)
        if "price" in updates:
            updates["price"] = float(updates["price"])
        if "is_active" in updates:
            updates["is_active"] = bool(updates["is_active"])
        if any(key in data for key in ["service_type", "duration_unit", "duration_value", "duration_days", "duration_hours"]):
            updates.update(normalized_package_payload({**data, **updates}))
        existing = ref(f"tenants/{tenant_id}/packages/{package_id}").get()
        if not existing:
            return ok({"message": "Package not found"}, 404)
        router_updates = {"updated_at": iso_now()}
        if has_mikrotik_credentials(request.tenant):
            package_for_router = {"id": package_id, **existing, **updates}
            if request.tenant.get("mikrotik_provisioning_status") in {"script_downloaded", "completed"} or request.tenant.get("mikrotik_last_seen_at"):
                script = _package_sync_script_for_request(request, package_for_router)
                if script:
                    _queue_router_command(request, {"type": "sync_packages", "script": script, "package_ids": [package_id]})
                    router_updates.update({"ppp_profile_status": "queued", "ppp_profile_queued_at": iso_now(), "ppp_profile_error": None})
            else:
                try:
                    if package_service_type(package_for_router) == "hotspot":
                        ensure_hotspot_captive_portal({"id": tenant_id, **request.tenant}, public_base_url(request).rstrip("/"))
                    sync_package_profile(request.tenant, package_for_router)
                    router_updates.update({"ppp_profile_status": "synced", "ppp_profile_synced_at": iso_now(), "ppp_profile_error": None})
                except Exception as exc:
                    router_updates.update({"ppp_profile_status": "failed", "ppp_profile_error": str(exc)})
        else:
            router_updates.update({"ppp_profile_status": "pending"})
        ref(f"tenants/{tenant_id}/packages/{package_id}").update({**updates, **router_updates})
        return ok({"success": True, "message": "Package and MikroTik hotspot profile updated"})
    if method(request, "DELETE") and package_id:
        ref(f"tenants/{tenant_id}/packages/{package_id}").delete()
        return ok({"success": True, "message": "Package deleted"})
    return ok({"message": "Method not allowed"}, 405)


@csrf_exempt
@api_view(["POST"])
@tenant_required
def package_add(request):
    data = body(request)
    if any(not data.get(field) for field in ["name", "speed", "price"]):
        return ok({"message": "All package fields are required"}, 400)
    package_payload = normalized_package_payload(data)
    if find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", data["name"]):
        return ok({"message": "A package with this name already exists"}, 409)
    router_synced = False
    router_queued = False
    router_error = None
    if has_mikrotik_credentials(request.tenant):
        if request.tenant.get("mikrotik_provisioning_status") in {"script_downloaded", "completed"} or request.tenant.get("mikrotik_last_seen_at"):
            router_queued = True
        else:
            try:
                if package_service_type({**data, **package_payload}) == "hotspot":
                    ensure_hotspot_captive_portal({"id": request.tenant["id"], **request.tenant}, public_base_url(request).rstrip("/"))
                sync_package_profile(request.tenant, {**data, **package_payload})
                router_synced = True
            except Exception as exc:
                router_error = str(exc)
    new_ref = ref(f"tenants/{request.tenant['id']}/packages").push(
        {
            "name": data["name"],
            "speed": data["speed"],
            **package_payload,
            "price": float(data["price"]),
            "is_active": data.get("is_active") is not False,
            "ppp_profile_status": "synced" if router_synced else "queued" if router_queued else "pending",
            "ppp_profile_synced_at": iso_now() if router_synced else "",
            "ppp_profile_queued_at": iso_now() if router_queued else "",
            "ppp_profile_error": router_error,
            "created_at": iso_now(),
        }
    )
    if router_queued:
        _queue_router_command(request, {"type": "sync_packages", "script": _package_sync_script_for_request(request, {"id": new_ref.key, **data, **package_payload}), "package_ids": [new_ref.key]})
    message = "Package and MikroTik profile created" if router_synced else "Package created and queued for MikroTik sync" if router_queued else "Package created. Sync router after MikroTik is connected."
    return ok({"success": True, "message": message, "packageId": new_ref.key}, 201)


@csrf_exempt
@api_view(["GET", "POST"])
@tenant_required
def vouchers(request):
    tenant_id = request.tenant["id"]
    voucher_path = f"tenants/{tenant_id}/vouchers"
    if method(request, "GET"):
        return ok(list_children(voucher_path))
    data = body(request)
    package_id = str(data.get("package_id") or "")
    package = ref(f"tenants/{tenant_id}/packages/{package_id}").get() if package_id else None
    if not package or package_service_type(package) != "hotspot":
        return ok({"message": "Select a valid Hotspot package"}, 400)
    code = secrets.token_hex(4).upper()
    while find_child_by_field(voucher_path, "code", code):
        code = secrets.token_hex(4).upper()
    voucher = {"code": code, "username": code, "password": code, "package": package.get("name"), "package_id": package_id, "price": float(package.get("price") or 0), "status": "active", "service_type": "hotspot", "router_status": "pending", "created_at": iso_now()}
    if not has_mikrotik_credentials(request.tenant) and not _router_is_agent_linked(request.tenant):
        return ok({"message": "Configure MikroTik credentials before creating vouchers"}, 400)
    try:
        create_hotspot_profile(request.tenant, package.get("name"), package.get("speed"))
        api = router_connect(request.tenant)
        try:
            api.path("ip", "hotspot", "user").add(name=code, password=code, profile=package.get("name"), disabled="no", comment="billing-saas-voucher")
        finally:
            api.close()
        voucher["router_status"] = "provisioned"
    except Exception as exc:
        # Live push failed (e.g. WireGuard tunnel temporarily down) — still
        # save the voucher and queue the router-side creation for the
        # agent's next ~30s poll, instead of losing the voucher entirely.
        voucher["router_status"] = "queued"
        voucher["router_error"] = str(exc)
        script = (
            f':if ([:len [/ip hotspot user find name="{_rsc_escape(code)}"]] = 0) do={{'
            f' /ip hotspot user add name="{_rsc_escape(code)}" password="{_rsc_escape(code)}" '
            f'profile="{_rsc_escape(package.get("name") or "")}" disabled=no comment="billing-saas-voucher" }};'
        )
        _queue_router_command(request, {"type": "sync_voucher", "script": script})
    saved = ref(voucher_path).push(voucher)
    message = "Hotspot voucher created" if voucher["router_status"] == "provisioned" else "Voucher created and queued — it will be active on the router within about 30 seconds."
    return ok({"success": True, "message": message, "voucher": {"id": saved.key, **voucher}}, 201)


@csrf_exempt
@api_view(["GET"])
@tenant_required
def router_profiles(request):
    if not has_mikrotik_credentials(request.tenant):
        return ok({"message": "Configure MikroTik credentials before viewing router profiles"}, 400)
    profiles = router_items(request.tenant, "ppp", "profile")
    return ok([{"id": p.get(".id"), "name": p.get("name"), "rate_limit": p.get("rate-limit"), "local_address": p.get("local-address"), "remote_address": p.get("remote-address")} for p in profiles])


@csrf_exempt
@api_view(["GET", "POST"])
@tenant_required
def router_status(request):
    tenant = {**request.tenant, **body(request)} if method(request, "POST") else request.tenant
    assignments = request.tenant.get("router_port_assignments") or {}
    snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
    if not has_mikrotik_credentials(tenant):
        if _router_is_agent_linked(request.tenant) and snapshot:
            return ok(_limited_router_status_payload(snapshot, assignments, "agent_report", "Showing the latest live configuration reported by the linked MikroTik agent."))
        return ok({"message": "Live MikroTik status requires reachable RouterOS API credentials. Stored provisioning data is not being shown as live status."}, 503)
    try:
        status = router_interface_status(tenant)
        return ok(_limited_router_status_payload(status, assignments, "routeros_api", "Router configuration loaded from the live RouterOS API."))
    except (TimeoutError, OSError) as exc:
        if _router_is_agent_linked(request.tenant) and snapshot:
            return ok(_limited_router_status_payload(snapshot, assignments, "agent_report", f"Showing the latest live configuration reported by the linked MikroTik agent. Direct RouterOS API is unavailable: {exc}"))
        return ok({"message": f"Unable to pull live MikroTik status on port {tenant.get('mikrotik_port') or 8728}: {exc}"}, 503)
    except Exception as exc:
        if _router_is_agent_linked(request.tenant) and snapshot:
            return ok(_limited_router_status_payload(snapshot, assignments, "agent_report", f"Showing the latest live configuration reported by the linked MikroTik agent. Direct RouterOS API failed: {exc}"))
        return ok({"message": f"Unable to pull live MikroTik status: {exc}"}, 503)


@csrf_exempt
@api_view(["POST"])
@tenant_required
def router_ports(request):
    if not has_mikrotik_credentials(request.tenant) and not _router_is_agent_linked(request.tenant):
        return ok({"message": "Run the MikroTik provisioning command before assigning router ports"}, 400)
    data = body(request)
    interface_name = str(data.get("interface") or "").strip()
    service_type = str(data.get("service_type") or "").strip().lower()
    profile_name = str(data.get("profile") or "default").strip() or "default"
    if not interface_name:
        return ok({"message": "Router interface is required"}, 400)
    if service_type not in {"pppoe", "hotspot", "both"}:
        return ok({"message": "Port service must be PPPoE, Hotspot, or both"}, 400)
    if service_type == "both" or (_router_is_agent_linked(request.tenant) and not has_mikrotik_credentials(request.tenant)):
        return _queue_router_port_command(request, interface_name, service_type, profile_name)
    try:
        result = configure_router_port(request.tenant, interface_name, service_type, profile_name, base_url=public_base_url(request).rstrip("/"))
        assignments = dict(request.tenant.get("router_port_assignments") or {})
        assignments[interface_name] = {
            "service_type": service_type,
            "profile": result.get("profile") or profile_name,
            "portal_url": result.get("portal_url"),
            "updated_at": iso_now(),
            "status": "applied",
        }
        ref(f"tenants/{request.tenant['id']}").update({"router_port_assignments": assignments})
        return ok({"success": True, "message": f"{interface_name} assigned to {service_type.upper()}", "result": result, "assignments": assignments})
    except (TimeoutError, OSError):
        # Router isn't directly reachable (typical when it's behind
        # NAT/CGNAT and no port-forward/tunnel exists for the RouterOS API
        # port). Queue the change instead — the router's own scheduler
        # polls for pending commands and applies them on its own outbound
        # connection, same as provisioning does.
        return _queue_router_port_command(request, interface_name, service_type, profile_name)
    except Exception as exc:
        if request.tenant.get("mikrotik_last_seen_at"):
            return _queue_router_port_command(request, interface_name, service_type, profile_name)
        return ok({"message": f"Unable to assign router port: {exc}"}, 400)


def _queue_router_port_command(request, interface_name, service_type, profile_name):
    if service_type not in {"pppoe", "hotspot", "both"}:
        return ok({"message": "Port service must be PPPoE, Hotspot, or both"}, 400)

    tenant_id = request.tenant["id"]
    portal_url = captive_portal_url({"id": tenant_id, **request.tenant}, public_base_url(request).rstrip("/")) if service_type in {"hotspot", "both"} else None
    bridge_name = mikrotik_managed_bridge_name(request.tenant)
    if service_type == "both":
        script = (
            _build_port_command_script(interface_name, "pppoe", profile_name, None, bridge_name)
            + _build_port_command_script(interface_name, "hotspot", "billing-saas-captive", portal_url, bridge_name)
        )
    else:
        script = _build_port_command_script(interface_name, service_type, profile_name, portal_url, bridge_name)

    commands = [c for c in (request.tenant.get("pending_router_commands") or []) if c.get("status") == "pending"][-19:]
    commands.append({
        "id": secrets.token_hex(8),
        "interface": interface_name,
        "service_type": service_type,
        "profile": profile_name,
        "portal_url": portal_url,
        "bridge": bridge_name,
        "script": script,
        "status": "pending",
        "created_at": iso_now(),
    })
    ref(f"tenants/{tenant_id}").update({"pending_router_commands": commands})

    assignments = dict(request.tenant.get("router_port_assignments") or {})
    assignments[interface_name] = {
        "service_type": service_type,
        "profile": profile_name,
        "portal_url": portal_url,
        "bridge": bridge_name,
        "updated_at": iso_now(),
        "status": "queued",
    }
    ref(f"tenants/{tenant_id}").update({"router_port_assignments": assignments})

    return ok({
        "success": True,
        "queued": True,
        "message": f"Router isn't directly reachable, so {interface_name} has been queued and will be applied automatically the next time the router checks in (usually within 30s).",
        "assignments": assignments,
    })


@csrf_exempt
@api_view(["POST"])
@tenant_required
def router_suspend(request):
    if not _router_is_agent_linked(request.tenant):
        return ok({"message": "No linked MikroTik router was found"}, 404)
    script = (
        ':do { /ip hotspot disable [find name~"billing"] } on-error={}; '
        ':do { /interface pppoe-server server disable [find service-name~"billing"] } on-error={}; '
        ':log warning "Billing SaaS: router services suspended from dashboard";'
    )
    _queue_router_command(request, {"type": "suspend_router", "script": script})
    _update_linked_router(request.tenant["id"], request.tenant, status="suspended", suspended_at=iso_now())
    ref(f"tenants/{request.tenant['id']}").update({"mikrotik_router_suspended": True})
    return ok({"success": True, "message": "Router suspension queued. The router will apply it on the next agent check-in."})


@csrf_exempt
@api_view(["DELETE"])
@tenant_required
def router_delete(request):
    updates = {
        "linked_routers": {},
        "mikrotik_router_snapshot": {},
        "mikrotik_provisioning_status": "",
        "mikrotik_last_seen_at": "",
        "mikrotik_last_seen_ip": "",
        "mikrotik_detected_identity": "",
        "mikrotik_detected_version": "",
        "mikrotik_detected_board": "",
        "mikrotik_router_suspended": False,
        "router_port_assignments": {},
        "pending_router_commands": [],
    }
    ref(f"tenants/{request.tenant['id']}").update(updates)
    return ok({"success": True, "message": "Linked MikroTik router deleted from this account."})


def _customer_secret_script(customer):
    """Generate an .rsc snippet that upserts a single customer into /ppp secret or /ip hotspot user."""
    service_type = customer.get("service_type") or "pppoe"
    if service_type not in {"pppoe", "hotspot"}:
        service_type = "pppoe"
    username = _rsc_escape(customer.get("username") or "")
    password = _rsc_escape(customer.get("password") or "")
    profile = _rsc_escape(customer.get("package") or customer.get("package_name") or "default")
    if not username:
        return ""
    disabled = "no" if customer.get("status") == "active" else "yes"
    rate_limit = _rsc_escape(normalize_rate_limit(customer.get("speed")) or "")
    rate_limit_field = f' rate-limit="{rate_limit}"' if rate_limit else ""
    ppp_profile_script = (
        f':if ("{profile}" != "default") do={{ '
        f':if ([:len [/ppp profile find name="{profile}"]] = 0) do={{'
        f' /ppp profile add name="{profile}"{rate_limit_field} }} '
        f'else={{ /ppp profile set [find name="{profile}"]{rate_limit_field} }}; }};'
        if rate_limit
        else (
            f':if ("{profile}" != "default") do={{ '
            f':if ([:len [/ppp profile find name="{profile}"]] = 0) do={{'
            f' /ppp profile add name="{profile}" }}; }};'
        )
    )
    hotspot_profile_script = (
        f':if ("{profile}" != "default") do={{ '
        f':if ([:len [/ip hotspot user profile find name="{profile}"]] = 0) do={{'
        f' /ip hotspot user profile add name="{profile}"{rate_limit_field} }} '
        f'else={{ /ip hotspot user profile set [find name="{profile}"]{rate_limit_field} }}; }};'
        if rate_limit
        else (
            f':if ("{profile}" != "default") do={{ '
            f':if ([:len [/ip hotspot user profile find name="{profile}"]] = 0) do={{'
            f' /ip hotspot user profile add name="{profile}" }}; }};'
        )
    )
    if service_type == "pppoe":
        return (
            ppp_profile_script
            +
            f':if ([:len [/ppp secret find name="{username}"]] = 0) do={{'
            f' /ppp secret add name="{username}" password="{password}" service=pppoe '
            f'profile="{profile}" disabled={disabled} comment="billing-saas-managed" }} '
            f'else={{ /ppp secret set [find name="{username}"] password="{password}" '
            f'service=pppoe profile="{profile}" disabled={disabled} comment="billing-saas-managed" }};'
        )
    return (
        hotspot_profile_script
        +
        f':if ([:len [/ip hotspot user find name="{username}"]] = 0) do={{'
        f' /ip hotspot user add name="{username}" password="{password}" '
        f'profile="{profile}" disabled={disabled} comment="billing-saas-managed" }} '
        f'else={{ /ip hotspot user set [find name="{username}"] password="{password}" '
        f'profile="{profile}" disabled={disabled} comment="billing-saas-managed" }};'
    )


def _package_profile_script(package):
    """Generate an .rsc snippet that upserts one PPPoE or Hotspot package profile."""
    name = _rsc_escape(package.get("name") or "")
    if not name:
        return ""
    service_type = package_service_type(package)
    rate_limit = _rsc_escape(normalize_rate_limit(package.get("speed")) or "")
    rate_limit_field = f' rate-limit="{rate_limit}"' if rate_limit else ""
    if service_type == "pppoe":
        return (
            f':do {{ '
            f':if ([:len [/ppp profile find name="{name}"]] = 0) do={{'
            f' /ppp profile add name="{name}"{rate_limit_field} comment="billing-saas-package" }} '
            f'else={{ /ppp profile set [find name="{name}"]{rate_limit_field} comment="billing-saas-package" }}; '
            f'}} on-error={{ :log warning "Billing SaaS agent: PPPoE profile sync failed for {name}"; :error "PPPoE profile sync failed for {name}" }};'
            f':if ([:len [/ppp profile find name="{name}"]] = 0) do={{ :error "PPPoE profile missing after sync: {name}" }};'
        )
    return (
        f':do {{ '
        f':if ([:len [/ip hotspot user profile find name="{name}"]] = 0) do={{'
        f' /ip hotspot user profile add name="{name}"{rate_limit_field} }} '
        f'else={{ /ip hotspot user profile set [find name="{name}"]{rate_limit_field} }}; '
        f'}} on-error={{ :log warning "Billing SaaS agent: Hotspot profile sync failed for {name}"; :error "Hotspot profile sync failed for {name}" }};'
        f':if ([:len [/ip hotspot user profile find name="{name}"]] = 0) do={{ :error "Hotspot profile missing after sync: {name}" }};'
    )


def _hotspot_captive_file_script(tenant, base_url=None):
    portal_url = captive_portal_url(tenant, base_url)
    return routeros_hotspot_fetch_script(portal_url, "Billing SaaS agent")


def _queue_all_customer_secrets(request):
    """Queue a sync-secrets command that pushes every existing customer to the router."""
    tenant_id = request.tenant["id"]
    customers = list_children(f"tenants/{tenant_id}/customers")
    packages = {
        str(package.get("name") or ""): package
        for package in list_children(f"tenants/{tenant_id}/packages")
        if package.get("name")
    }
    customers = [
        {**customer, "speed": (packages.get(str(customer.get("package") or "")) or {}).get("speed")}
        for customer in customers
    ]
    script = "".join(_customer_secret_script(c) for c in customers)
    if script:
        _queue_router_command(request, {"type": "sync_secrets", "script": script})


def _queue_router_command(request, command_data):
    tenant_id = request.tenant["id"]
    commands = [c for c in (request.tenant.get("pending_router_commands") or []) if c.get("status") == "pending"][-19:]
    command_id = secrets.token_hex(8)
    if command_data.get("type") == "reboot":
        script = '/system reboot;'
    else:
        script = command_data.get("script", "")
    commands.append({
        "id": command_id,
        **command_data,
        "script": script,
        "status": "pending",
        "created_at": iso_now(),
    })
    ref(f"tenants/{tenant_id}").update({"pending_router_commands": commands})
    return ok({
        "success": True,
        "queued": True,
        "message": "Command queued and will be applied on next router poll (usually within 30s).",
        "command_id": command_id,
    })

@csrf_exempt
@api_view(["GET"])
@tenant_required
def router_provision_command(request):
    fresh_router = request.GET.get("fresh") == "1"
    expires_at = utcnow() + timedelta(minutes=15)
    payload = {
        "purpose": "mikrotik_provision",
        "tenant_id": request.tenant["id"],
        "fresh_router": fresh_router,
        "exp": expires_at,
    }
    token = jwt.encode(payload, _get_jwt_secret("JWT_SECRET"), algorithm="HS256")
    if not fresh_router:
        ref(f"tenants/{request.tenant['id']}").update({
            "provision_token_expires_at": expires_at.isoformat(),
            "mikrotik_provisioning_status": "pending",
        })
    # Backfill Postgres tables from Firebase so the RADIUS server can
    # authenticate existing customers created before RADIUS was enabled.
    try:
        from .radius_provisioning import backfill_radius_data
        from .models import Tenant as TenantModel
        tenant_obj = TenantModel.objects.get(pk=request.tenant["id"])
        backfill_radius_data(tenant_obj, request.tenant["id"])
    except Exception:
        pass
    # Re-queue all existing customer secrets so the router picks them up
    # after the provisioning script runs (the script purges non-managed secrets).
    # A newly added router must start with its own provisioning state. Do not
    # replay the existing router's customer secrets onto it.
    if not fresh_router:
        _queue_all_customer_secrets(request)
    script_url = f"{public_base_url(request)}/api/router/provision/{token}"
    callback_url = f"{public_base_url(request)}/api/router/provision/{token}/complete"
    script_host = urlparse(script_url).netloc.split("@")[-1].split(":")[0]
    # NOTE: the imported .rsc script (router_provision_script) already performs
    # the full device/interface/profile snapshot AND calls the /complete
    # callback internally. Do not duplicate that work here — doing so doubles
    # the number of sequential HTTPS/TLS handshakes the router has to make,
    # which is enough to exhaust RouterOS's SSL session pool on low-resource
    # hardware (RB9xx-class devices) and surfaces as "SSL: internal error (6)".
    command = (
        ':do { /ip dns set servers=1.1.1.1,8.8.8.8 allow-remote-requests=yes } on-error={}; '
        f':do {{ :resolve "{script_host}" }} on-error={{ :log warning "Billing SaaS: DNS cannot resolve {script_host}" }}; '
        f'/tool fetch url="{script_url}" dst-path=billing-saas.rsc; delay 2s; /import billing-saas.rsc;'
    )
    return ok({
        "command": command,
        "script_url": script_url,
        "script_host": script_host,
        "callback_url": callback_url,
        "expires_in_minutes": 15,
        "expires_at": expires_at.isoformat(),
    })


@csrf_exempt
@api_view(["POST"])
@tenant_required
def router_reboot(request):
    if not has_mikrotik_credentials(request.tenant):
        return ok({"message": "Configure MikroTik credentials before rebooting"}, 400)
    try:
        api = router_connect(request.tenant)
        try:
            api.command("/system/reboot")
        finally:
            api.close()
        return ok({"success": True, "message": "Reboot command sent"})
    except (TimeoutError, OSError):
        return _queue_router_command(request, {"type": "reboot"})
    except Exception as exc:
        if request.tenant.get("mikrotik_last_seen_at"):
            return _queue_router_command(request, {"type": "reboot"})
        return ok({"message": f"Unable to reboot router: {exc}"}, 400)


@csrf_exempt
@api_view(["GET"])
@tenant_required
def router_resources(request):
    try:
        status = router_interface_status(request.tenant)
        device = status.get("device", {})
        total = float(device.get("total_memory") or 0)
        free = float(device.get("free_memory") or 0)
        return ok({
            "cpu_load_percent": device.get("cpu_load"),
            "uptime": device.get("uptime"),
            "memory_used_bytes": max(0, total - free),
            "memory_total_bytes": total,
            "memory_used_percent": round((1 - free / total) * 100, 1) if total else None,
            "board_name": device.get("board_name"),
            "version": device.get("version"),
        })
    except (TimeoutError, OSError) as exc:
        snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
        device = snapshot.get("device") or {}
        total = float(device.get("total_memory") or 0)
        free = float(device.get("free_memory") or 0)
        return ok({
            "cpu_load_percent": device.get("cpu_load"),
            "uptime": device.get("uptime"),
            "memory_used_bytes": max(0, total - free),
            "memory_total_bytes": total,
            "memory_used_percent": round((1 - free / total) * 100, 1) if total else None,
            "board_name": device.get("board_name"),
            "version": device.get("version"),
            "source": "provisioning_snapshot",
            "message": f"Live API unreachable, showing last snapshot: {exc}",
        })
    except Exception as exc:
        snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
        device = snapshot.get("device") or {}
        total = float(device.get("total_memory") or 0)
        free = float(device.get("free_memory") or 0)
        return ok({
            "cpu_load_percent": device.get("cpu_load"),
            "uptime": device.get("uptime"),
            "memory_used_bytes": max(0, total - free),
            "memory_total_bytes": total,
            "memory_used_percent": round((1 - free / total) * 100, 1) if total else None,
            "board_name": device.get("board_name"),
            "version": device.get("version"),
            "source": "provisioning_snapshot",
            "message": f"Live API failed, showing last snapshot: {exc}",
        })


def _empty_router_snapshot():
    return {
        "device": {},
        "interfaces": [],
        "bridge_ports": [],
        "addresses": [],
        "dhcp_servers": [],
        "pools": [],
        "pppoe_servers": [],
        "hotspot_servers": [],
        "profiles": {"pppoe": [], "hotspot": []},
    }


def _router_bool(value):
    return str(value or "").lower() in {"true", "yes", "1"}


def _snapshot_item(request, keys):
    return {key: str(request.GET.get(key) or "").strip() for key in keys}


def _append_unique(items, item, key="name"):
    value = item.get(key)
    if not value:
        return items
    return [existing for existing in items if existing.get(key) != value] + [item]


def _router_snapshot_fetch_script(snapshot_url):
    return f"""
        :local billingSnapshot "{snapshot_url}";
        :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/marker") }} on-error={{}}
        :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/device?board_name=" . [/system resource get board-name] . "&version=" . [/system resource get version] . "&uptime=" . [/system resource get uptime] . "&cpu_load=" . [/system resource get cpu-load] . "&free_memory=" . [/system resource get free-memory] . "&total_memory=" . [/system resource get total-memory] . "&architecture=" . [/system resource get architecture-name]) }} on-error={{ :log warning "Billing SaaS device snapshot failed" }}
        :foreach i in=[/interface find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/interface?name=" . [/interface get $i name] . "&type=" . [/interface get $i type] . "&running=" . [/interface get $i running] . "&disabled=" . [/interface get $i disabled] . "&mac_address=" . [/interface get $i mac-address]) }} on-error={{}} }}
        :foreach p in=[/interface bridge port find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/bridge-port?name=" . [/interface bridge port get $p interface] . "&interface=" . [/interface bridge port get $p interface] . "&bridge=" . [/interface bridge port get $p bridge] . "&disabled=" . [/interface bridge port get $p disabled]) }} on-error={{}} }}
        :foreach a in=[/ip address find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/address?name=" . [/ip address get $a address] . "&address=" . [/ip address get $a address] . "&interface=" . [/ip address get $a interface] . "&disabled=" . [/ip address get $a disabled]) }} on-error={{}} }}
        :foreach p in=[/ip pool find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/pool?name=" . [/ip pool get $p name] . "&ranges=" . [/ip pool get $p ranges]) }} on-error={{}} }}
        :foreach d in=[/ip dhcp-server find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/dhcp-server?name=" . [/ip dhcp-server get $d name] . "&interface=" . [/ip dhcp-server get $d interface] . "&address_pool=" . [/ip dhcp-server get $d address-pool] . "&disabled=" . [/ip dhcp-server get $d disabled]) }} on-error={{}} }}
        :foreach p in=[/ppp profile find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/pppoe-profile?name=" . [/ppp profile get $p name] . "&rate_limit=" . [/ppp profile get $p rate-limit]) }} on-error={{}} }}
        :foreach p in=[/ip hotspot user profile find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/hotspot-profile?name=" . [/ip hotspot user profile get $p name] . "&rate_limit=" . [/ip hotspot user profile get $p rate-limit]) }} on-error={{}} }}
        :foreach s in=[/interface pppoe-server server find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/pppoe-server?name=" . [/interface pppoe-server server get $s service-name] . "&interface=" . [/interface pppoe-server server get $s interface] . "&default_profile=" . [/interface pppoe-server server get $s default-profile] . "&disabled=" . [/interface pppoe-server server get $s disabled]) }} on-error={{}} }}
        :foreach h in=[/ip hotspot find] do={{ :do {{ /tool fetch keep-result=no url=($billingSnapshot . "/hotspot-server?name=" . [/ip hotspot get $h name] . "&interface=" . [/ip hotspot get $h interface] . "&profile=" . [/ip hotspot get $h profile] . "&address_pool=" . [/ip hotspot get $h address-pool] . "&disabled=" . [/ip hotspot get $h disabled]) }} on-error={{}} }}
    """





@csrf_exempt
@api_view(["GET"])
@permission_classes([AllowAny])  # Safely opens the wall for MikroTik requests
def router_provision_script(request, token):
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    if user_agent and "Mikrotik" not in user_agent and "RouterOS" not in user_agent and "curl" not in user_agent.lower():
        return HttpResponse("Forbidden: Invalid Access Point.", status=403, content_type="text/plain")

    try:
        payload = jwt.decode(token, _get_jwt_secret("JWT_SECRET"), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return HttpResponse("Unauthorized: The provisioning token has expired.", status=401, content_type="text/plain")
    except jwt.InvalidTokenError:
        return HttpResponse("Unauthorized: Provided configuration token signature is invalid.", status=401, content_type="text/plain")

    if payload.get("purpose") != "mikrotik_provision":
        return HttpResponse("Unauthorized: Invalid token assignment.", status=401, content_type="text/plain")

    tenant_id = str(payload.get("tenant_id") or "")
    try:
        tenant_data = ref(f"tenants/{tenant_id}").get()
    except OperationalError:
        close_old_connections()
        try:
            tenant_data = ref(f"tenants/{tenant_id}").get()
        except OperationalError:
            return HttpResponse(':log warning "Billing SaaS provisioning: app database is temporarily unreachable";\n', content_type="text/plain")
    if not tenant_data:
        return HttpResponse("Not Found: Tenant account profile was not located.", status=404, content_type="text/plain")

    tenant = {"id": tenant_id, **tenant_data}
    app_base_url = public_base_url(request).rstrip("/")
    portal_url = captive_portal_url(tenant, app_base_url)
    portal_host = urlparse(portal_url).netloc.split("@")[-1].split(":")[0]
    callback_base_url = f"{app_base_url}/api/router/provision/{token}/complete"
    snapshot_url = f"{app_base_url}/api/router/provision/{token}/snapshot"
    snapshot_interface_url = f"{app_base_url}/api/router/provision/{token}/snapshot/interface"
    snapshot_pppoe_profile_url = f"{app_base_url}/api/router/provision/{token}/snapshot/pppoe-profile"
    snapshot_hotspot_profile_url = f"{app_base_url}/api/router/provision/{token}/snapshot/hotspot-profile"
    snapshot_pppoe_server_url = f"{app_base_url}/api/router/provision/{token}/snapshot/pppoe-server"
    snapshot_hotspot_server_url = f"{app_base_url}/api/router/provision/{token}/snapshot/hotspot-server"

    agent_token = jwt.encode({"purpose": "mikrotik_agent", "tenant_id": tenant_id}, _get_jwt_secret("JWT_SECRET"), algorithm="HS256")
    agent_poll_url = f"{app_base_url}/api/router/agent/{agent_token}/poll"

    def _rsc_escape(value):
        return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")

    hotspot_file_script = _hotspot_captive_file_script(tenant, app_base_url)
    snapshot_script = _router_snapshot_fetch_script(snapshot_url)

    wg_server_public_key = str(os.getenv("WG_SERVER_PUBLIC_KEY") or tenant.get("wg_server_public_key") or "").strip()
    wg_server_endpoint = str(os.getenv("WG_SERVER_ENDPOINT") or os.getenv("WG_SERVER_PUBLIC_IP") or tenant.get("wg_server_endpoint") or "").strip()
    wg_server_port = str(os.getenv("WG_SERVER_PORT") or tenant.get("wg_server_port") or "51820").strip()
    wg_server_tunnel_ip = str(os.getenv("WG_SERVER_TUNNEL_IP") or tenant.get("wg_server_tunnel_ip") or "10.8.0.1").strip().split("/")[0]
    wg_router_tunnel_ip = str(tenant.get("wg_tunnel_ip") or os.getenv("WG_ROUTER_TUNNEL_IP") or "10.8.0.2/24").strip()
    wg_router_api_ip = wg_router_tunnel_ip.split("/", 1)[0]
    wg_router_private_key = str(tenant.get("wg_private_key") or "").strip()
    vpn_peer_enabled = bool(wg_server_public_key and wg_server_endpoint)
    callback_url = f"{callback_base_url}?vpn=1&vpn_peer={'1' if vpn_peer_enabled else '0'}&hotspot=1"
    callback_join = "&" if "?" in callback_url else "?"
    bridge_name = mikrotik_managed_bridge_name(tenant)
    wan_interface = str(os.getenv("MIKROTIK_WAN_INTERFACE") or tenant.get("mikrotik_wan_interface") or "ether1").strip()
    lan_cidr = str(os.getenv("MIKROTIK_LAN_CIDR") or tenant.get("mikrotik_lan_cidr") or "192.168.88.1/24").strip()
    lan_gateway = lan_cidr.split("/", 1)[0]
    lan_network = str(os.getenv("MIKROTIK_LAN_NETWORK") or tenant.get("mikrotik_lan_network") or "192.168.88.0/24").strip()
    dhcp_pool = str(os.getenv("MIKROTIK_DHCP_POOL") or tenant.get("mikrotik_dhcp_pool") or "192.168.88.10-192.168.88.254").strip()
    hotspot_dns_name = str(os.getenv("MIKROTIK_HOTSPOT_DNS_NAME") or tenant.get("mikrotik_hotspot_dns_name") or "signup.billing.local").strip()

    vpn_private_key_set = (
        f':do {{ /interface wireguard set [find name="wg-saas"] private-key="{_rsc_escape(wg_router_private_key)}" }} on-error={{}}\n'
        if wg_router_private_key
        else ""
    )
    vpn_peer_script = (
        f""":do {{ /interface wireguard peers remove [find comment="billing-saas server peer"] }} on-error={{}}
        :do {{ /interface wireguard peers add interface=wg-saas public-key="{_rsc_escape(wg_server_public_key)}" endpoint-address="{_rsc_escape(wg_server_endpoint)}" endpoint-port={_rsc_escape(wg_server_port)} allowed-address={_rsc_escape(wg_server_tunnel_ip)}/32 persistent-keepalive=25s comment="billing-saas server peer" }} on-error={{ :log warning "Billing SaaS: WireGuard peer setup failed" }}"""
        if vpn_peer_enabled
        else ':log warning "Billing SaaS: WireGuard peer skipped because WG_SERVER_PUBLIC_KEY or WG_SERVER_ENDPOINT is not configured";'
    )
    vpn_script = f"""
        :log info "Billing SaaS: configuring WireGuard VPN";
        :do {{ /interface wireguard add name=wg-saas listen-port=13231 mtu=1420 comment="billing-saas vpn" }} on-error={{}}
        {vpn_private_key_set}
        :do {{ /ip address remove [find comment="billing-saas vpn ip"] }} on-error={{}}
        :do {{ /ip address add address={_rsc_escape(wg_router_tunnel_ip)} interface=wg-saas comment="billing-saas vpn ip" }} on-error={{}}
        {vpn_peer_script}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow wireguard"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept protocol=udp dst-port=13231 comment="billing-saas allow wireguard" }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow api over vpn"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept in-interface=wg-saas protocol=tcp dst-port=8728 comment="billing-saas allow api over vpn" }} on-error={{}}
        """

    try:
        ref(f"tenants/{tenant_id}").update({
            "mikrotik_provisioning_status": "script_downloaded",
            "mikrotik_script_downloaded_at": iso_now(),
            "mikrotik_vpn_enabled": True,
            "mikrotik_vpn_peer_enabled": vpn_peer_enabled,
            "mikrotik_vpn_tunnel_ip": wg_router_tunnel_ip,
            "mikrotik_host": wg_router_api_ip,
            "mikrotik_port": int(tenant.get("mikrotik_port") or 8728),
            "mikrotik_bridge_name": bridge_name,
            "mikrotik_wan_interface": wan_interface,
        })
    except OperationalError:
        close_old_connections()
        try:
            ref(f"tenants/{tenant_id}").update({
                "mikrotik_provisioning_status": "script_downloaded",
                "mikrotik_script_downloaded_at": iso_now(),
                "mikrotik_vpn_enabled": True,
                "mikrotik_vpn_peer_enabled": vpn_peer_enabled,
                "mikrotik_vpn_tunnel_ip": wg_router_tunnel_ip,
                "mikrotik_host": wg_router_api_ip,
                "mikrotik_port": int(tenant.get("mikrotik_port") or 8728),
                "mikrotik_bridge_name": bridge_name,
                "mikrotik_wan_interface": wan_interface,
            })
        except OperationalError:
            pass

   
    interface_report_loop = f"""
        :log info "Billing SaaS: reporting interfaces";
        :foreach i in=[/interface find] do={{
            :local n [/interface get $i name];
            :local t [/interface get $i type];
            :local mac "";
            :do {{ :set mac [/interface get $i mac-address] }} on-error={{}}
            :local run [/interface get $i running];
            :local dis [/interface get $i disabled];
            :do {{ /tool fetch keep-result=no url=("{snapshot_interface_url}?name=" . $n . "&type=" . $t . "&mac_address=" . $mac . "&running=" . $run . "&disabled=" . $dis) }} on-error={{ :log warning "Billing SaaS: interface report failed" }}
        }}
        """

    profile_and_server_report_loop = f"""
        :log info "Billing SaaS: reporting profiles and servers";
        :foreach p in=[/ppp profile find] do={{
            :local pn [/ppp profile get $p name];
            :local pr "";
            :do {{ :set pr [/ppp profile get $p rate-limit] }} on-error={{}}
            :do {{ /tool fetch keep-result=no url=("{snapshot_pppoe_profile_url}?name=" . $pn . "&rate_limit=" . $pr) }} on-error={{}}
        }}
        :foreach p in=[/ip hotspot profile find] do={{
            :local pn [/ip hotspot profile get $p name];
            :do {{ /tool fetch keep-result=no url=("{snapshot_hotspot_profile_url}?name=" . $pn) }} on-error={{}}
        }}
        :foreach s in=[/interface pppoe-server server find] do={{
            :local sn [/interface pppoe-server server get $s service-name];
            :local si [/interface pppoe-server server get $s interface];
            :local sp [/interface pppoe-server server get $s default-profile];
            :local sd [/interface pppoe-server server get $s disabled];
            :do {{ /tool fetch keep-result=no url=("{snapshot_pppoe_server_url}?name=" . $sn . "&interface=" . $si . "&default_profile=" . $sp . "&disabled=" . $sd) }} on-error={{}}
        }}
        :foreach h in=[/ip hotspot find] do={{
            :local hn [/ip hotspot get $h name];
            :local hi [/ip hotspot get $h interface];
            :local hp [/ip hotspot get $h profile];
            :local hd [/ip hotspot get $h disabled];
            :do {{ /tool fetch keep-result=no url=("{snapshot_hotspot_server_url}?name=" . $hn . "&interface=" . $hi . "&profile=" . $hp . "&disabled=" . $hd) }} on-error={{}}
        }}
        """

    # Generate RADIUS shared secret for this tenant — persist to Postgres
    # (same store that router_provision_complete reads from) so the secret
    # survives across re-provisions and is available to the callback.
    from .models import RadiusNasClient
    tenant_obj = Tenant.objects.get(pk=tenant_id)
    existing_extra = tenant_obj.extra or {}
    radius_shared_secret = existing_extra.get("radius_shared_secret_pending") or RadiusNasClient.generate_secret()
    tenant_obj.extra = {**existing_extra, "radius_shared_secret_pending": radius_shared_secret}
    tenant_obj.save(update_fields=["extra"])

    script = f""":log info "Billing SaaS provisioning started";
        :local billingVer [/system resource get version];
        :local billingVerNum $billingVer;
        :local billingSpaceIdx [:find $billingVer " "];
        :if ([:len $billingSpaceIdx] > 0) do={{ :set billingVerNum [:pick $billingVer 0 $billingSpaceIdx] }}
        :local billingDot1 [:find $billingVerNum "."];
        :local billingMajor 0;
        :if ([:len $billingDot1] > 0) do={{ :set billingMajor [:tonum [:pick $billingVerNum 0 $billingDot1]] }} else={{ :set billingMajor [:tonum $billingVerNum] }}
        :if ($billingMajor < 7) do={{
            :do {{ /tool fetch keep-result=no url=("{callback_url}{callback_join}status=unsupported&reason=" . "RouterOS_" . $billingVerNum . "_requires_7_or_later_for_WireGuard") }} on-error={{}}
            :error ("Billing SaaS: RouterOS " . $billingVer . " is not supported -- version 7.0 or later is required for WireGuard.");
        }}
        {vpn_script}
        :log info "Billing SaaS: creating LAN bridge (no ports attached yet -- assign ports from the dashboard)";
        :local billingBridge "{_rsc_escape(bridge_name)}";
        :do {{ /interface bridge add name=$billingBridge comment="billing-saas managed bridge" }} on-error={{}}
        :do {{ /ip address remove [find comment="billing-saas bridge gateway"] }} on-error={{}}
        :do {{ /ip address add address={_rsc_escape(lan_cidr)} interface=$billingBridge comment="billing-saas bridge gateway" }} on-error={{}}
        :do {{ /ip pool remove [find name="billing-saas-dhcp"] }} on-error={{}}
        :do {{ /ip pool add name=billing-saas-dhcp ranges={_rsc_escape(dhcp_pool)} }} on-error={{}}
        :foreach s in=[/ip dhcp-server find] do={{
            :local sn [/ip dhcp-server get $s name];
            :if ($sn != "billing-saas-dhcp") do={{ :do {{ /ip dhcp-server disable $s }} on-error={{}} }}
        }}
        :do {{ /ip dhcp-server remove [find name="billing-saas-dhcp"] }} on-error={{}}
        :do {{ /ip dhcp-server add name=billing-saas-dhcp interface=$billingBridge address-pool=billing-saas-dhcp disabled=no lease-time=1d }} on-error={{}}
        :do {{ /ip dhcp-server network remove [find comment="billing-saas dhcp network"] }} on-error={{}}
        :do {{ /ip dhcp-server network add address={_rsc_escape(lan_network)} gateway={_rsc_escape(lan_gateway)} dns-server={_rsc_escape(lan_gateway)},8.8.8.8 comment="billing-saas dhcp network" }} on-error={{}}
        :log info "Billing SaaS: configuring firewall and NAT";
        /ip service enable api;
        :do {{ /ip service set api disabled=no }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow established"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept connection-state=established,related comment="billing-saas allow established" }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow api over vpn only"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept in-interface=wg-saas protocol=tcp dst-port=8728 comment="billing-saas allow api over vpn only" }} on-error={{}}
        :do {{ /ip firewall nat remove [find comment="billing-saas masquerade"] }} on-error={{}}
        :do {{ /ip firewall nat add chain=srcnat action=masquerade comment="billing-saas masquerade" }} on-error={{}}
        :do {{ /ip dns static remove [find comment="billing-saas hotspot dns"] }} on-error={{}}
        :do {{ /ip dns static add name="{_rsc_escape(hotspot_dns_name)}" address={_rsc_escape(lan_gateway)} comment="billing-saas hotspot dns" }} on-error={{}}
        :do {{ /system ntp client set enabled=yes servers=pool.ntp.org }} on-error={{}}
        :do {{ /system clock set time-zone-name="Africa/Nairobi" }} on-error={{}}
        :log info "Billing SaaS: creating default PPPoE server on managed bridge";
        :do {{ /interface pppoe-server server add service-name="billing-default-pppoe" interface=$billingBridge default-profile=default one-session-per-host=yes disabled=no comment="billing-saas default pppoe server" }} on-error={{ :log warning "Billing SaaS: PPPoE server creation failed" }}
        :log info "Billing SaaS: configuring RADIUS for PPPoE and Hotspot";
        :do {{ /radius add service=ppp,hotspot address={_rsc_escape(wg_server_tunnel_ip)} secret="{_rsc_escape(radius_shared_secret)}" src-address={_rsc_escape(wg_router_api_ip)} comment="billing-saas radius" }} on-error={{ /radius set [find address={_rsc_escape(wg_server_tunnel_ip)}] secret="{_rsc_escape(radius_shared_secret)}" src-address={_rsc_escape(wg_router_api_ip)} comment="billing-saas radius" }}
        :do {{ /radius incoming set accept=yes port=3799 }} on-error={{ :log warning "Billing SaaS: RADIUS incoming (CoA) setup failed" }}
        /ppp aaa set use-radius=yes accounting=yes interim-update=5m;
        :do {{ /ip hotspot profile set [find name="billing-saas-captive"] use-radius=yes radius-accounting=yes radius-interim-update=5m }} on-error={{ :log warning "Billing SaaS: hotspot RADIUS setup failed" }}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow radius"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input in-interface=wg-saas protocol=udp dst-port=1812,1813,3799 action=accept comment="billing-saas allow radius" }} on-error={{ :log warning "Billing SaaS: RADIUS firewall rule failed" }}
        :foreach s in=[/ppp secret find] do={{ :if ([/ppp secret get $s comment] != "billing-saas-managed") do={{ :do {{ /ppp secret remove $s }} on-error={{}} }} }}
        :log info "Billing SaaS: configuring captive portal (empty page until a port is assigned)";
        :do {{ /ip hotspot profile add name=billing-saas-captive hotspot-address={_rsc_escape(lan_gateway)} dns-name="{_rsc_escape(hotspot_dns_name)}" login-by=http-chap,http-pap use-radius=yes radius-accounting=yes radius-interim-update=5m html-directory=hotspot }} on-error={{ /ip hotspot profile set [find name=billing-saas-captive] hotspot-address={_rsc_escape(lan_gateway)} dns-name="{_rsc_escape(hotspot_dns_name)}" login-by=http-chap,http-pap use-radius=yes radius-accounting=yes radius-interim-update=5m html-directory=hotspot }}
        :do {{ /ip hotspot user profile add name=billing-saas-unpaid shared-users=1 keepalive-timeout=2m status-autorefresh=1m }} on-error={{}}
        :foreach h in=[/ip hotspot find] do={{
            :local hn [/ip hotspot get $h name];
            :if ($hn != "billing-saas-hotspot") do={{ :do {{ /ip hotspot disable $h }} on-error={{}} }}
        }}
        :do {{ /ip hotspot remove [find name=billing-saas-hotspot] }} on-error={{}}
        :do {{ /ip hotspot add name=billing-saas-hotspot interface=$billingBridge address-pool=billing-saas-dhcp profile=billing-saas-captive disabled=no }} on-error={{ :log warning "Billing SaaS hotspot server setup failed" }}
        :do {{ /ip hotspot set [find name=billing-saas-hotspot] disabled=no }} on-error={{}}
        :do {{ /ip hotspot walled-garden remove [find comment="billing-saas captive portal access"] }} on-error={{}}
        :do {{ /ip hotspot walled-garden ip remove [find comment="billing-saas captive portal access"] }} on-error={{}}
        :do {{ /ip hotspot walled-garden add action=allow dst-host="{portal_host}" comment="billing-saas captive portal access" }} on-error={{}}
        :do {{ /ip hotspot walled-garden add action=allow dst-host="checkout.paystack.com" comment="billing-saas captive portal access" }} on-error={{}}
        :do {{ /ip hotspot walled-garden add action=allow dst-host="api.paystack.co" comment="billing-saas captive portal access" }} on-error={{}}
        :do {{ /ip hotspot walled-garden add action=allow dst-host="*.paystack.co" comment="billing-saas captive portal access" }} on-error={{}}
        :do {{ /ip hotspot walled-garden add action=allow dst-host="*.paystack.com" comment="billing-saas captive portal access" }} on-error={{}}
        :local billingPortalIp "";
        :do {{ :set billingPortalIp [:resolve "{portal_host}"] }} on-error={{ :log warning "Billing SaaS portal DNS resolve failed" }}
        :if ([:len $billingPortalIp] > 0) do={{
            :do {{ /ip hotspot walled-garden ip add action=accept dst-address=$billingPortalIp protocol=tcp dst-port=80 comment="billing-saas captive portal access" }} on-error={{}}
            :do {{ /ip hotspot walled-garden ip add action=accept dst-address=$billingPortalIp protocol=tcp dst-port=443 comment="billing-saas captive portal access" }} on-error={{}}
        }}
        {hotspot_file_script}
        :local billingHsFileCount [:len [/file find name~"hotspot"]];
        :do {{ /tool fetch keep-result=no url=("{snapshot_url}/hotspot-files-check?count=" . $billingHsFileCount) }} on-error={{ :log warning "Billing SaaS: hotspot file count report failed" }}
        {interface_report_loop}
        {profile_and_server_report_loop}
        {snapshot_script}
        :local billingWgPub "";
        :do {{ :set billingWgPub [/interface wireguard get [find name=wg-saas] public-key] }} on-error={{}}
        :do {{ /tool fetch keep-result=no url=("{callback_url}{callback_join}wg_public_key=" . $billingWgPub . "&wg_tunnel_ip={_rsc_escape(wg_router_api_ip)}&bridge={_rsc_escape(bridge_name)}") }} on-error={{ :log warning "Billing SaaS provisioning callback failed" }}
        :do {{ /system scheduler remove [find name="billing-saas-agent"] }} on-error={{}}
        /system scheduler add name="billing-saas-agent" interval=30s on-event=":do {{ /file remove [find name=\\"billing-saas-cmd.rsc\\"] }} on-error={{}}; :do {{ /tool fetch url=\\"{agent_poll_url}\\" dst-path=billing-saas-cmd.rsc }} on-error={{ :log warning \\"Billing SaaS agent: command fetch failed\\" }}; :if ([:len [/file find name=\\"billing-saas-cmd.rsc\\"]] > 0) do={{ :do {{ /import billing-saas-cmd.rsc }} on-error={{ :log warning \\"Billing SaaS agent: command import failed\\" }} }};"
        :log info "Billing SaaS provisioning complete. No ports were assigned -- open the dashboard, pull router ports, and assign each interface to Hotspot or PPPoE.";
        :put "Configuration completed successfully. No LAN ports were touched -- assign ports from the dashboard.";
        """
    return HttpResponse(script, content_type="text/plain")
@csrf_exempt
@api_view(["GET"])
def router_agent_poll(request, token):
    try:
        payload = jwt.decode(token, _get_jwt_secret("JWT_SECRET"), algorithms=["HS256"])
    except Exception:
        return HttpResponse("# Invalid or expired agent token\n", status=401, content_type="text/plain")
    if payload.get("purpose") != "mikrotik_agent":
        return HttpResponse("# Invalid agent token\n", status=401, content_type="text/plain")

    tenant_id = str(payload.get("tenant_id") or "")
    try:
        tenant = ref(f"tenants/{tenant_id}").get()
    except OperationalError:
        close_old_connections()
        try:
            tenant = ref(f"tenants/{tenant_id}").get()
        except OperationalError:
            return HttpResponse(':log warning "Billing SaaS agent: app database is temporarily unreachable";\n', content_type="text/plain")
    if not tenant:
        return HttpResponse("# Unknown tenant\n", status=404, content_type="text/plain")

    client_ip = (
        request.META.get("HTTP_NGROK_AGENT_IPS")
        or request.META.get("HTTP_X_FORWARDED_FOR")
        or request.META.get("REMOTE_ADDR")
        or ""
    ).split(",")[0].strip()
    ref(f"tenants/{tenant_id}").update({
        "mikrotik_last_seen_at": iso_now(),
        "mikrotik_last_seen_ip": client_ip,
    })

    all_commands = tenant.get("pending_router_commands") or []
    commands = [
        c
        for c in all_commands
        if c.get("status") == "pending" or (c.get("status") == "delivered" and int(c.get("delivery_attempts") or 0) < 5)
    ]
    base_url = public_base_url(request).rstrip("/")
    if not commands:
        return HttpResponse(':log info "Billing SaaS agent: no pending commands";\n', content_type="text/plain")

    lines = [':log info "Billing SaaS agent: applying queued commands";']
    delivered_at = iso_now()
    delivered_ids = {command.get("id") for command in commands if command.get("id")}
    for command in commands:
        command_id = command.get("id") or secrets.token_hex(8)
        script = str(command.get("script") or "").strip()
        if not script:
            lines.append(f':log warning "Billing SaaS agent: command {command_id} had no script";')
            continue
        post_script = ""
        if command.get("type") == "sync_packages":
            snapshot_base = f"{base_url}/api/router/provision/{token}/snapshot"
            post_script = (
                f':foreach p in=[/ppp profile find] do={{ :do {{ /tool fetch keep-result=no url=("{snapshot_base}/pppoe-profile?name=" . [/ppp profile get $p name] . "&rate_limit=" . [/ppp profile get $p rate-limit]) }} on-error={{}} }}; '
                f':foreach p in=[/ip hotspot user profile find] do={{ :do {{ /tool fetch keep-result=no url=("{snapshot_base}/hotspot-profile?name=" . [/ip hotspot user profile get $p name] . "&rate_limit=" . [/ip hotspot user profile get $p rate-limit]) }} on-error={{}} }}; '
                f':local billingHsFileCount [:len [/file find name~"hotspot"]]; '
                f':do {{ /tool fetch keep-result=no url=("{snapshot_base}/hotspot-files-check?count=" . $billingHsFileCount) }} on-error={{}}; '
            )
        lines.append(f':log info "Billing SaaS agent: running command {command_id} ({command.get("type") or "router"})";')
        lines.append(
            f':do {{ {script}; {post_script}/tool fetch keep-result=no url="{base_url}/api/router/agent/{token}/ack/{command_id}" }} '
            f'on-error={{ :log warning "Billing SaaS agent: command {command_id} failed" }}'
        )
    assignments = dict(tenant.get("router_port_assignments") or {})
    for command in all_commands:
        if command.get("id") not in delivered_ids or command.get("status") not in {"pending", "delivered"}:
            continue
        command["status"] = "delivered"
        command["delivered_at"] = delivered_at
        command["delivery_attempts"] = int(command.get("delivery_attempts") or 0) + 1
        command["ack_mode"] = "poll_delivery"
        interface_name = command.get("interface")
        if interface_name:
            assignment = assignments.get(interface_name) or {}
            assignment.update({
                "service_type": command.get("service_type") or assignment.get("service_type"),
                "profile": command.get("profile") or assignment.get("profile"),
                "portal_url": command.get("portal_url") or assignment.get("portal_url"),
                "bridge": command.get("bridge") or assignment.get("bridge"),
                "status": "delivered",
                "updated_at": delivered_at,
            })
            assignments[interface_name] = assignment
    ref(f"tenants/{tenant_id}").update({
        "pending_router_commands": all_commands,
        "router_port_assignments": assignments,
        "mikrotik_last_command_delivered_at": delivered_at,
    })
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")


@csrf_exempt
@api_view(["GET"])
def router_agent_ack(request, token, command_id):
    try:
        payload = jwt.decode(token, _get_jwt_secret("JWT_SECRET"), algorithms=["HS256"])
    except Exception:
        return ok({"message": "Invalid or expired agent token"}, 401)
    if payload.get("purpose") != "mikrotik_agent":
        return ok({"message": "Invalid agent token"}, 401)

    tenant_id = str(payload.get("tenant_id") or "")
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return ok({"message": "Tenant not found"}, 404)

    commands = tenant.get("pending_router_commands") or []
    updated = False
    applied_command = None
    for command in commands:
        if command.get("id") == command_id and command.get("status") in {"pending", "delivered"}:
            command["status"] = "applied"
            command["applied_at"] = iso_now()
            updated = True
            applied_command = command
            break

    if updated:
        ref(f"tenants/{tenant_id}").update({
            "pending_router_commands": commands,
            "mikrotik_last_seen_at": iso_now(),
        })
        interface_name = (applied_command or {}).get("interface")
        if interface_name:
            assignments = dict(tenant.get("router_port_assignments") or {})
            assignment = assignments.get(interface_name)
            if assignment and assignment.get("status") in {"queued", "delivered"}:
                assignment["status"] = "applied"
                assignment["updated_at"] = iso_now()
                assignments[interface_name] = assignment
                ref(f"tenants/{tenant_id}").update({"router_port_assignments": assignments})
        if (applied_command or {}).get("type") == "sync_packages":
            for package_id_value in ((applied_command or {}).get("package_ids") or []):
                ref(f"tenants/{tenant_id}/packages/{package_id_value}").update({
                    "ppp_profile_status": "synced",
                    "ppp_profile_synced_at": iso_now(),
                    "ppp_profile_error": None,
                })
        if (applied_command or {}).get("type") == "suspend_router":
            ref(f"tenants/{tenant_id}").update({"mikrotik_router_suspended": True})
            _update_linked_router(tenant_id, {**tenant, "mikrotik_router_suspended": True, "mikrotik_last_seen_at": iso_now()}, status="suspended")
        elif applied_command:
            _update_linked_router(tenant_id, {**tenant, "mikrotik_last_seen_at": iso_now()}, status="online")

    return ok({"success": True, "acknowledged": updated})

@csrf_exempt
@api_view(["GET"])
def router_provision_complete(request, token):
    try:
        payload = jwt.decode(token, _get_jwt_secret("JWT_SECRET"), algorithms=["HS256"])
    except Exception:
        return ok({"message": "Invalid or expired provisioning token"}, 401)
    if payload.get("purpose") not in {"mikrotik_provision", "mikrotik_agent"}:
        return ok({"message": "Invalid provisioning token"}, 401)
    tenant_id = str(payload.get("tenant_id") or "")
    client_ip = (
        request.META.get("HTTP_NGROK_AGENT_IPS")
        or request.META.get("HTTP_X_FORWARDED_FOR")
        or request.META.get("REMOTE_ADDR")
        or ""
    ).split(",")[0].strip()
    updates = {
        "mikrotik_provisioning_status": "completed",
        "mikrotik_provisioned_at": iso_now(),
        "mikrotik_last_seen_at": iso_now(),
        "mikrotik_last_seen_ip": client_ip,
        "mikrotik_detected_identity": str(request.GET.get("identity") or "").strip(),
        "mikrotik_detected_version": str(request.GET.get("version") or "").strip(),
        "mikrotik_detected_board": str(request.GET.get("board") or "").strip(),
        "mikrotik_vpn_status": "configured" if str(request.GET.get("vpn") or "").lower() in {"1", "true", "yes"} else "callback_received",
        "mikrotik_vpn_peer_status": "configured" if str(request.GET.get("vpn_peer") or "").lower() in {"1", "true", "yes"} else "missing_server_peer_config",
        "mikrotik_hotspot_status": "configured" if str(request.GET.get("hotspot") or "").lower() in {"1", "true", "yes"} else "callback_received",
    }
    wg_public_key = str(request.GET.get("wg_public_key") or "").strip().replace(" ", "+")
    wg_tunnel_ip = str(request.GET.get("wg_tunnel_ip") or "").strip()
    bridge = str(request.GET.get("bridge") or "").strip()
    if wg_public_key:
        updates["wg_public_key"] = wg_public_key
        updates["mikrotik_wg_public_key"] = wg_public_key
    if wg_tunnel_ip:
        updates["mikrotik_host"] = wg_tunnel_ip
        updates["mikrotik_vpn_tunnel_ip"] = wg_tunnel_ip
    if bridge:
        updates["mikrotik_bridge_name"] = bridge
    ref(f"tenants/{tenant_id}").update(updates)
    tenant_data = ref(f"tenants/{tenant_id}").get() or {}
    _update_linked_router(tenant_id, {**tenant_data, **updates}, status="online")

    # Create RADIUS NAS client record if we have a pending secret and tunnel IP
    try:
        from .radius_provisioning import ensure_nas_client
        tenant_obj = Tenant.objects.filter(pk=tenant_id).first()
        if tenant_obj and wg_tunnel_ip:
            pending_secret = (tenant_obj.extra or {}).get("radius_shared_secret_pending")
            if pending_secret:
                from .models import RadiusNasClient
                nas_client, created = RadiusNasClient.objects.get_or_create(
                    tenant=tenant_obj,
                    nas_ip=wg_tunnel_ip,
                    defaults={
                        "shared_secret": pending_secret,
                        "identifier": str(request.GET.get("identity") or "").strip(),
                    },
                )
                if created:
                    updates["radius_enabled"] = True
                    updates["radius_nas_configured"] = True
                    ref(f"tenants/{tenant_id}").update({
                        "radius_enabled": True,
                        "radius_nas_configured": True,
                    })
    except Exception:
        pass

    return ok({"success": True, "message": "MikroTik provisioning callback received"})


@csrf_exempt
@api_view(["GET"])
def router_provision_snapshot(request, token, section):
    try:
        payload = jwt.decode(token, _get_jwt_secret("JWT_SECRET"), algorithms=["HS256"])
    except Exception:
        return ok({"message": "Invalid or expired provisioning token"}, 401)
    if payload.get("purpose") not in {"mikrotik_provision", "mikrotik_agent"}:
        return ok({"message": "Invalid provisioning token"}, 401)

    tenant_id = str(payload.get("tenant_id") or "")
    try:
        tenant = Tenant.objects.filter(pk=tenant_id).first()
    except OperationalError:
        close_old_connections()
        tenant = Tenant.objects.filter(pk=tenant_id).first()
    if not tenant:
        return ok({"message": "Tenant not found"}, 404)

    snapshot = dict((tenant.extra or {}).get("mikrotik_router_snapshot") or _empty_router_snapshot())
    snapshot.setdefault("device", {})
    snapshot.setdefault("interfaces", [])
    snapshot.setdefault("bridge_ports", [])
    snapshot.setdefault("addresses", [])
    snapshot.setdefault("dhcp_servers", [])
    snapshot.setdefault("pools", [])
    snapshot.setdefault("pppoe_servers", [])
    snapshot.setdefault("hotspot_servers", [])
    snapshot.setdefault("profiles", {})
    snapshot["profiles"].setdefault("pppoe", [])
    snapshot["profiles"].setdefault("hotspot", [])

    if section == "marker":
        snapshot["marker"] = {"received_at": iso_now()}
    elif section == "device":
        snapshot["device"] = _snapshot_item(request, ["board_name", "version", "uptime", "cpu_load", "free_memory", "total_memory", "architecture"])
    elif section == "interface":
        item = _snapshot_item(request, ["name", "type", "mac_address"])
        item["running"] = _router_bool(request.GET.get("running"))
        item["disabled"] = _router_bool(request.GET.get("disabled"))
        snapshot["interfaces"] = _append_unique(snapshot["interfaces"], item)
    elif section == "bridge-port":
        item = _snapshot_item(request, ["name", "interface", "bridge"])
        item["disabled"] = _router_bool(request.GET.get("disabled"))
        snapshot["bridge_ports"] = _append_unique(snapshot["bridge_ports"], item)
    elif section == "address":
        item = _snapshot_item(request, ["name", "address", "interface"])
        item["disabled"] = _router_bool(request.GET.get("disabled"))
        snapshot["addresses"] = _append_unique(snapshot["addresses"], item)
    elif section == "pool":
        item = _snapshot_item(request, ["name", "ranges"])
        snapshot["pools"] = _append_unique(snapshot["pools"], item)
    elif section == "dhcp-server":
        item = _snapshot_item(request, ["name", "interface", "address_pool"])
        item["disabled"] = _router_bool(request.GET.get("disabled"))
        snapshot["dhcp_servers"] = _append_unique(snapshot["dhcp_servers"], item)
    elif section == "pppoe-profile":
        item = _snapshot_item(request, ["name", "rate_limit"])
        snapshot["profiles"]["pppoe"] = _append_unique(snapshot["profiles"]["pppoe"], item)
    elif section == "hotspot-profile":
        item = _snapshot_item(request, ["name", "rate_limit"])
        snapshot["profiles"]["hotspot"] = _append_unique(snapshot["profiles"]["hotspot"], item)
    elif section == "pppoe-server":
        item = _snapshot_item(request, ["name", "interface", "default_profile"])
        item["disabled"] = _router_bool(request.GET.get("disabled"))
        snapshot["pppoe_servers"] = _append_unique(snapshot["pppoe_servers"], item)
    elif section == "hotspot-server":
        item = _snapshot_item(request, ["name", "interface", "profile", "address_pool"])
        item["disabled"] = _router_bool(request.GET.get("disabled"))
        snapshot["hotspot_servers"] = _append_unique(snapshot["hotspot_servers"], item)
    elif section == "hotspot-files-check":
        file_count = request.GET.get("count", "0")
        snapshot["hotspot_file_count"] = int(file_count) if file_count.isdigit() else 0
        ref(f"tenants/{tenant_id}").update({"mikrotik_router_snapshot": snapshot, "mikrotik_snapshot_updated_at": iso_now()})
        return HttpResponse("OK", content_type="text/plain")
    else:
        return ok({"message": "Unknown snapshot section"}, 404)

    ref(f"tenants/{tenant_id}").update({
        "mikrotik_router_snapshot": snapshot,
        "mikrotik_snapshot_updated_at": iso_now(),
    })
    return ok({"success": True})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def package_sync(request, package_id=None):
    if not has_mikrotik_credentials(request.tenant) and not _router_is_agent_linked(request.tenant):
        return ok({"message": "Run the MikroTik provisioning command before syncing package profiles"}, 400)
    tenant_id = request.tenant["id"]
    packages_to_sync = list_children(f"tenants/{tenant_id}/packages") if package_id is None else [{"id": package_id, **(ref(f"tenants/{tenant_id}/packages/{package_id}").get() or {})}]
    if package_id and not packages_to_sync[0].get("name"):
        return ok({"message": "Package not found"}, 404)
    should_queue = _router_is_agent_linked(request.tenant)
    if should_queue:
        script = "".join(_package_profile_script(pkg) for pkg in packages_to_sync)
        if any(package_service_type(pkg) == "hotspot" for pkg in packages_to_sync):
            script = _hotspot_captive_file_script({"id": tenant_id, **request.tenant}, public_base_url(request).rstrip("/")) + script
        if not script:
            return ok({"message": "No valid package profiles to sync"}, 400)
        package_ids = [pkg.get("id") for pkg in packages_to_sync if pkg.get("id")]
        response = _queue_router_command(request, {"type": "sync_packages", "script": script, "package_ids": package_ids})
        status_updates = {
            "ppp_profile_status": "queued",
            "ppp_profile_synced_at": "",
            "ppp_profile_error": None,
            "ppp_profile_queued_at": iso_now(),
        }
        for pkg in packages_to_sync:
            if pkg.get("id"):
                ref(f"tenants/{tenant_id}/packages/{pkg['id']}").update(status_updates)
        payload = getattr(response, "data", {}) or {}
        payload.update({
            "message": "Package profile sync queued and will be applied on next router poll (usually within 30s).",
            "queued": True,
            "count": len(packages_to_sync),
        })
        return ok(payload)
    results = []
    for pkg in packages_to_sync:
        try:
            if package_service_type(pkg) == "hotspot":
                ensure_hotspot_captive_portal({"id": tenant_id, **request.tenant}, public_base_url(request).rstrip("/"))
            sync_package_profile(request.tenant, pkg)
            ref(f"tenants/{tenant_id}/packages/{pkg['id']}").update({"ppp_profile_status": "synced", "ppp_profile_synced_at": iso_now(), "ppp_profile_error": None})
            results.append({"id": pkg["id"], "name": pkg["name"], "success": True})
        except Exception as exc:
            if request.tenant.get("mikrotik_last_seen_at"):
                script = _package_sync_script_for_request(request, pkg)
                if script:
                    _queue_router_command(request, {"type": "sync_packages", "script": script, "package_ids": [pkg["id"]]})
                    ref(f"tenants/{tenant_id}/packages/{pkg['id']}").update({"ppp_profile_status": "queued", "ppp_profile_error": None, "ppp_profile_queued_at": iso_now()})
                    results.append({"id": pkg["id"], "name": pkg.get("name"), "success": True, "queued": True})
                    continue
            ref(f"tenants/{tenant_id}/packages/{pkg['id']}").update({"ppp_profile_status": "failed", "ppp_profile_error": str(exc), "ppp_profile_failed_at": iso_now()})
            results.append({"id": pkg["id"], "name": pkg.get("name"), "success": False, "message": str(exc)})
    if package_id:
        result = results[0] if results else {}
        return ok({"success": bool(result.get("success")), "queued": bool(result.get("queued")), "message": "Package profile sync queued" if result.get("queued") else "MikroTik package profile synced" if result.get("success") else result.get("message", "Package profile sync failed")}, 200 if result.get("success") else 400)
    synced = len([r for r in results if r["success"]])
    failed = len(results) - synced
    return ok({"success": failed == 0, "message": "All package profiles synced" if failed == 0 else f"{synced} package profiles synced, {failed} failed", "synced": synced, "failed": failed, "results": results})


@csrf_exempt
@api_view(["GET", "POST"])
@tenant_required
def payments(request):
    if method(request, "GET"):
        payments_data = list_children(f"tenants/{request.tenant['id']}/payments")
        status_filter = request.GET.get("status")
        from_date = parse_date(request.GET.get("from"))
        to_date = parse_date(request.GET.get("to"))
        if status_filter and status_filter != "all":
            payments_data = [item for item in payments_data if item.get("status") == status_filter]
        if from_date or to_date:
            filtered = []
            for item in payments_data:
                current = payment_date(item)
                if not current:
                    continue
                if from_date and current < from_date:
                    continue
                if to_date and current > to_date:
                    continue
                filtered.append(item)
            payments_data = filtered
        return as_collection_response(request, payments_data)
    data = body(request)
    if not data.get("phone"):
        return ok({"message": "Customer phone is required"}, 400)
    phone = normalize_phone(data["phone"])
    payment_ref = ref(f"tenants/{request.tenant['id']}/payments").push(
        {
            "customer_id": data.get("customer_id"),
            "customer_name": data.get("customer_name"),
            "package_name": data.get("package_name"),
            "service_type": data.get("service_type") or "pppoe",
            "amount": float(data.get("amount") or 0),
            "payment_code": None,
            "phone": phone,
            "status": "pending",
            "paid_at": None,
            "initiated_at": iso_now(),
            "provider": "paystack",
        }
    )
    try:
        checkout = initiate_paystack_payment(
            request.tenant,
            payment_ref.key,
            data.get("amount"),
            email=data.get("email"),
            phone=phone,
            description=f"{data.get('package_name') or 'Internet'} payment",
            metadata={
                "customer_id": data.get("customer_id"),
                "customer_name": data.get("customer_name"),
                "package_name": data.get("package_name"),
                "service_type": data.get("service_type") or "pppoe",
            },
        )
    except PaymentProviderError as exc:
        payment_ref.update({"status": "failed", "failed_at": iso_now(), "callback_result_desc": exc.detail})
        return ok({"success": False, "message": exc.public_message, "paymentId": payment_ref.key}, exc.status_code)
    payment_ref.update(
        {
            "paystack_reference": checkout.get("reference"),
            "paystack_access_code": checkout.get("access_code"),
            "paystack_authorization_url": checkout.get("authorization_url"),
            "paystack_customer_email": checkout.get("customer_email"),
            "currency": checkout.get("currency"),
            "checkout_requested_at": iso_now(),
        }
    )
    return ok({"success": True, "message": "Paystack checkout created", "paymentId": payment_ref.key, "reference": checkout.get("reference"), "authorizationUrl": checkout.get("authorization_url")})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def payment_mark_paid(request, payment_id):
    payment = ref(f"tenants/{request.tenant['id']}/payments/{payment_id}").get()
    if not payment:
        return ok({"message": "Payment not found"}, 404)
    payment_code = payment.get("payment_code") or payment.get("paystack_reference") or f"CASH-{secrets.token_hex(4).upper()}"
    updates = {
        "status": "success",
        "provider": payment.get("provider") or "cash",
        "payment_code": payment_code,
        "paid_at": iso_now(),
        "callback_result_code": "manual",
        "callback_result_desc": "Marked as paid by operator",
    }
    ref(f"tenants/{request.tenant['id']}/payments/{payment_id}").update(updates)
    try:
        activate_paid_access(request.tenant, payment_id, {**payment, **updates}, payment.get("phone"), payment_code)
    except Exception as exc:
        ref(f"tenants/{request.tenant['id']}/payments/{payment_id}").update({"access_status": "activation_failed", "callback_result_desc": str(exc)})
        return ok({"success": True, "message": "Payment marked paid, but router activation failed", "activation_error": str(exc)})
    return ok({"success": True, "message": "Payment marked as paid and access activated"})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def customer_renew(request, customer_id):
    data = body(request)
    customer = ref(f"tenants/{request.tenant['id']}/customers/{customer_id}").get()
    if not customer:
        return ok({"message": "Customer not found"}, 404)
    package_id = data.get("package_id")
    package = ref(f"tenants/{request.tenant['id']}/packages/{package_id}").get() if package_id else find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", customer.get("package"))
    if not package or package.get("is_active") is False:
        return ok({"message": "Active package not found"}, 404)
    payment_ref = ref(f"tenants/{request.tenant['id']}/payments").push(
        {
            "customer_id": customer_id,
            "customer_name": customer.get("name"),
            "package_id": package_id or package.get("id"),
            "package_name": package.get("name"),
            "service_type": customer.get("service_type") or "pppoe",
            "amount": float(package.get("price") or 0),
            "payment_code": f"MANUAL-{secrets.token_hex(4).upper()}",
            "phone": customer.get("phone"),
            "status": "success",
            "paid_at": iso_now(),
            "initiated_at": iso_now(),
            "provider": data.get("provider") or "cash",
            "source": "manual_renewal",
        }
    )
    try:
        activate_paid_access(request.tenant, payment_ref.key, {**payment_ref.instance.as_dict(), "package_name": package.get("name")}, customer.get("phone"), payment_ref.instance.payment_code)
    except Exception as exc:
        payment_ref.update({"access_status": "activation_failed", "callback_result_desc": str(exc)})
        return ok({"success": True, "message": "Renewal saved, but router activation failed", "paymentId": payment_ref.key, "activation_error": str(exc)})
    return ok({"success": True, "message": "Customer renewed and access activated", "paymentId": payment_ref.key})


def tenant_payments(tenant_id):
    return list_children(f"tenants/{tenant_id}/payments")


def tenant_customers(tenant_id):
    return list_children(f"tenants/{tenant_id}/customers")


def tenant_packages(tenant_id):
    return list_children(f"tenants/{tenant_id}/packages")


def month_key(value):
    dt = parse_date(value)
    return dt.strftime("%b") if dt else ""


def in_range(item_date, start, end):
    if not item_date:
        return False
    if start and item_date < start:
        return False
    if end and item_date > end:
        return False
    return True


@csrf_exempt
@api_view(["GET"])
@tenant_required
def dashboard_stats(request):
    tenant_id = request.tenant["id"]
    payments_data = tenant_payments(tenant_id)
    customers_data = tenant_customers(tenant_id)
    packages_data = tenant_packages(tenant_id)
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    paid_payments = [p for p in payments_data if p.get("status") == "success"]
    revenue_this_month = sum(float(p.get("amount") or 0) for p in paid_payments if (payment_date(p) or now) >= month_start)

    last_12 = []
    for offset in range(11, -1, -1):
        year = now.year
        month = now.month - offset
        while month <= 0:
            month += 12
            year -= 1
        label = datetime(year, month, 1).strftime("%b")
        total = sum(float(p.get("amount") or 0) for p in paid_payments if (payment_date(p) and payment_date(p).year == year and payment_date(p).month == month))
        last_12.append([label, round(total, 2)])

    days = []
    for offset in range(6, -1, -1):
        day = (now - timedelta(days=offset)).date()
        label = day.strftime("%a")
        active = len([c for c in customers_data if c.get("status") == "active"])
        new = len([c for c in customers_data if parse_date(c.get("created_at")) and parse_date(c.get("created_at")).date() == day])
        days.append([label, active, new])

    package_counts = Counter(c.get("package") or "Unassigned" for c in customers_data)
    palette = ["#fa8200", "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#0f766e"]
    package_utilization = [[name, count, palette[index % len(palette)]] for index, (name, count) in enumerate(package_counts.items())]
    package_revenue = defaultdict(float)
    for payment in paid_payments:
        package_revenue[payment.get("package_name") or "Unassigned"] += float(payment.get("amount") or 0)
    # Enrich customer data with RADIUS session data usage when available
    radius_data_usage = {}
    try:
        from .models import RadiusSession as RadiusSessionModel
        from django.db.models import Sum
        from datetime import timedelta as td

        month_ago = now - td(days=30)
        sessions = RadiusSessionModel.objects.filter(
            tenant_id=tenant_id,
            started_at__gte=month_ago,
        ).values("customer__username").annotate(
            total_input=Sum("input_octets"),
            total_output=Sum("output_octets"),
        )
        for s in sessions:
            username = s["customer__username"] or ""
            radius_data_usage[username] = float((s["total_input"] or 0) + (s["total_output"] or 0))
    except Exception:
        pass

    # Compute avg_data_usage per package from RADIUS sessions
    radius_package_usage = defaultdict(float)
    radius_package_count = defaultdict(int)
    try:
        from .models import RadiusSession as RadiusSessionModel
        from django.db.models import Sum, Count

        month_ago = now - td(days=30)
        pkg_sessions = RadiusSessionModel.objects.filter(
            tenant_id=tenant_id,
            started_at__gte=month_ago,
        ).values("customer__package").annotate(
            total_input=Sum("input_octets"),
            total_output=Sum("output_octets"),
            session_count=Count("id"),
        )
        for s in pkg_sessions:
            pkg_name = s["customer__package"] or "Unassigned"
            total_bytes = float((s["total_input"] or 0) + (s["total_output"] or 0))
            radius_package_usage[pkg_name] += total_bytes
            radius_package_count[pkg_name] += int(s["session_count"] or 0)
    except Exception:
        pass

    package_performance = []
    for package in packages_data:
        name = package.get("name")
        active_count = len([c for c in customers_data if c.get("package") == name and c.get("status") == "active"])
        revenue = package_revenue.get(name, 0)
        # Use real RADIUS data usage if available, fall back to package field
        if name in radius_package_usage and radius_package_count.get(name, 0) > 0:
            avg_bytes = radius_package_usage[name] / radius_package_count[name]
            avg_usage_mb = round(avg_bytes / (1024 * 1024), 2)
        else:
            avg_usage_mb = float(package.get("avg_data_usage") or 0)
        package_performance.append(
            {
                "name": name,
                "price": float(package.get("price") or 0),
                "active_users": active_count,
                "monthly_revenue": round(revenue, 2),
                "avg_data_usage": avg_usage_mb,
                "arpu": round(revenue / active_count, 2) if active_count else 0,
                "sync_status": package.get("ppp_profile_status") or "pending",
            }
        )

    return ok(
        {
            "summary": {
                "revenue_this_month": round(revenue_this_month, 2),
                "sms_balance": float(request.tenant.get("sms_balance") or 0),
                "total_customers": len(customers_data),
                "active_customers": len([c for c in customers_data if c.get("status") == "active"]),
            },
            "payments_chart": last_12,
            "active_users_chart": days,
            "retention_chart": [[item[0], item[1], max(0, item[1] - item[2]), 90] for item in days[-6:]],
            "data_usage_chart": [[item[0], float(index * 8 + item[1])] for index, item in enumerate(days[-8:])],
            "package_utilization": package_utilization,
            "revenue_forecast": last_12[-6:] + [[f"+{i}", round((last_12[-1][1] if last_12 else 0) * (1 + i * 0.05), 2)] for i in range(1, 4)],
            "sms_chart": [[item[0], int(request.tenant.get("sms_sent_today") or 0)] for item in days],
            "most_active_users": sorted(
                [
                    {
                        "username": c.get("username") or c.get("phone"),
                        "phone": c.get("phone"),
                        "data_used": radius_data_usage.get(
                            c.get("username"),
                            float(c.get("data_used") or c.get("data_usage") or 0),
                        ),
                    }
                    for c in customers_data
                ],
                key=lambda item: item["data_used"],
                reverse=True,
            )[:6],
            "package_performance": package_performance,
        }
    )


@csrf_exempt
@api_view(["GET"])
@tenant_required
def report_revenue(request):
    start = parse_date(request.GET.get("from"))
    end = parse_date(request.GET.get("to"))
    monthly = defaultdict(float)
    for payment in tenant_payments(request.tenant["id"]):
        dt = payment_date(payment)
        if payment.get("status") == "success" and in_range(dt, start, end):
            monthly[dt.strftime("%Y-%m")] += float(payment.get("amount") or 0)
    rows = [{"month": key, "revenue": round(value, 2)} for key, value in sorted(monthly.items())]
    return ok({"results": rows, "total": round(sum(item["revenue"] for item in rows), 2)})


@csrf_exempt
@api_view(["GET"])
@tenant_required
def report_customers(request):
    start = parse_date(request.GET.get("from"))
    end = parse_date(request.GET.get("to"))
    monthly = defaultdict(int)
    customers_data = tenant_customers(request.tenant["id"])
    for customer in customers_data:
        dt = parse_date(customer.get("created_at"))
        if in_range(dt, start, end):
            monthly[dt.strftime("%Y-%m")] += 1
    expired = len([c for c in customers_data if c.get("expiry_date") and str(c.get("expiry_date")) < iso_now()])
    return ok({"results": [{"month": key, "new_customers": value} for key, value in sorted(monthly.items())], "total_customers": len(customers_data), "expired_customers": expired})


@csrf_exempt
@api_view(["GET"])
@tenant_required
def report_packages(request):
    customers_data = tenant_customers(request.tenant["id"])
    payments_data = [p for p in tenant_payments(request.tenant["id"]) if p.get("status") == "success"]
    rows = []
    for package in tenant_packages(request.tenant["id"]):
        name = package.get("name")
        revenue = sum(float(p.get("amount") or 0) for p in payments_data if p.get("package_name") == name)
        rows.append({"package": name, "price": float(package.get("price") or 0), "active_customers": len([c for c in customers_data if c.get("package") == name and c.get("status") == "active"]), "revenue": round(revenue, 2)})
    return ok({"results": rows})


@csrf_exempt
@api_view(["GET"])
@tenant_required
def report_expenses(request):
    expenses = request.tenant.get("expenses") or []
    if not isinstance(expenses, list):
        expenses = []
    by_category = defaultdict(float)
    for expense in expenses:
        by_category[expense.get("category") or "Other"] += float(expense.get("amount") or 0)
    revenue = sum(float(p.get("amount") or 0) for p in tenant_payments(request.tenant["id"]) if p.get("status") == "success")
    total_expenses = sum(by_category.values())
    return ok({"results": [{"category": key, "amount": round(value, 2)} for key, value in sorted(by_category.items())], "total_expenses": round(total_expenses, 2), "net_revenue": round(revenue - total_expenses, 2)})


@csrf_exempt
@api_view(["GET", "PATCH"])
@tenant_required
def settings_business(request):
    tenant_id = request.tenant["id"]
    if method(request, "GET"):
        return ok(tenant_theme_payload(request.tenant))

    data = body(request)
    allowed = [
        "business_name",
        "owner_name",
        "phone",
        "support_email",
        "theme_color",
        "font",
        "dark_mode",
        "theme_mode",
        "business_number",
        "bank_code",
        "bank_name",
        "bank_account_number",
        "paystack_platform_percentage",
        "payment_methods",
        "payment_provider",
        "daraja_consumer_key",
        "daraja_consumer_secret",
        "daraja_shortcode",
        "daraja_passkey",
        "daraja_environment",
        "daraja_till_number",
        "daraja_shortcode_type",
    ]
    updates = {}
    for field in allowed:
        if field in data:
            if field == "dark_mode":
                updates[field] = bool(data[field])
            elif field == "payment_methods":
                requested = data[field] if isinstance(data[field], list) else [data[field]]
                updates[field] = [str(item).strip().lower() for item in requested if str(item).strip() in {"bank", "paybill", "buygoods", "daraja_paybill", "daraja_buygoods"}]
            elif field == "payment_provider":
                candidate = str(data[field]).strip().lower()
                updates[field] = candidate if candidate in {"paystack", "mpesa"} else "paystack"
            else:
                updates[field] = str(data[field]).strip()
    if updates.get("theme_mode") not in {None, "light", "dark", "system"}:
        updates["theme_mode"] = "light"
    if "theme_mode" in updates:
        updates["dark_mode"] = updates["theme_mode"] == "dark"
    if "theme_color" in updates and not updates["theme_color"].startswith("#"):
        updates["theme_color"] = f"#{updates['theme_color']}"
    updates["business_settings_updated_at"] = iso_now()

    merged = {**request.tenant, **updates}
    if data.get("create_subaccount"):
        try:
            updates.update(create_or_update_tenant_subaccount(tenant_id, merged, merged))
        except PaymentProviderError as exc:
            updates.update({"paystack_subaccount_status": "failed", "paystack_subaccount_error": exc.detail})
            ref(f"tenants/{tenant_id}").update(updates)
            return ok({"success": False, "message": exc.public_message, "config": tenant_theme_payload({**merged, **updates})}, exc.status_code)

    ref(f"tenants/{tenant_id}").update(updates)
    return ok({"success": True, "message": "Business settings saved", "config": tenant_theme_payload({**merged, **updates})})


@csrf_exempt
@api_view(["GET", "PATCH"])
@tenant_required
def profile(request):
    if method(request, "GET"):
        return ok({"owner_name": request.tenant.get("owner_name") or "", "email": request.tenant.get("email") or "", "phone": request.tenant.get("phone") or "", "business_name": request.tenant.get("business_name") or ""})
    data = body(request)
    updates = {}
    if "owner_name" in data:
        updates["owner_name"] = str(data.get("owner_name") or "").strip()
    if "phone" in data:
        updates["phone"] = str(data.get("phone") or "").strip()
    if "email" in data and str(data.get("email") or "").strip().lower() != request.tenant.get("email"):
        if not check_password(data.get("current_password", ""), request.tenant.get("password")):
            return ok({"message": "Current password is required to change email"}, 400)
        updates["email"] = str(data["email"]).strip().lower()
    if data.get("new_password"):
        if not check_password(data.get("current_password", ""), request.tenant.get("password")):
            return ok({"message": "Current password is incorrect"}, 400)
        if data.get("new_password") != data.get("confirm_password"):
            return ok({"message": "New password and confirmation do not match"}, 400)
        updates["password"] = hash_password(data["new_password"])
    if not updates:
        return ok({"message": "No profile fields provided"}, 400)
    ref(f"tenants/{request.tenant['id']}").update({**updates, "profile_updated_at": iso_now()})
    return ok({"success": True, "message": "Profile updated"})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def settings_logo(request):
    upload = request.FILES.get("logo")
    if not upload:
        return ok({"message": "Logo file is required"}, 400)
    if upload.size > 2 * 1024 * 1024:
        return ok({"message": "Logo must be smaller than 2MB"}, 400)
    ext = Path(upload.name).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
        return ok({"message": "Logo must be PNG, JPG, WEBP, or SVG"}, 400)
    storage = FileSystemStorage(location=Path(settings.MEDIA_ROOT) / "tenant-logos", base_url=f"{settings.MEDIA_URL}tenant-logos/")
    filename = storage.save(f"tenant-{request.tenant['id']}{ext}", upload)
    logo_url = storage.url(filename)
    ref(f"tenants/{request.tenant['id']}").update({"logo_url": logo_url, "logo_updated_at": iso_now()})
    return ok({"success": True, "message": "Logo uploaded", "logo_url": logo_url})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def settings_test_sms(request):
    data = body(request)
    phone = normalize_phone(data.get("phone") or request.tenant.get("phone"))
    if not phone:
        return ok({"message": "Phone number is required"}, 400)
    return ok({"success": True, "message": "Test SMS queued", "phone": phone})


@csrf_exempt
@api_view(["GET", "POST", "PATCH", "DELETE"])
@tenant_required
def tickets(request, ticket_id=None):
    tenant_id = request.tenant["id"]
    if method(request, "GET") and not ticket_id:
        items = list_children(f"tenants/{tenant_id}/tickets")
        status_filter = request.GET.get("status")
        if status_filter and status_filter != "all":
            items = [item for item in items if item.get("status") == status_filter]
        return as_collection_response(request, items)
    if method(request, "POST") and not ticket_id:
        data = body(request)
        if not data.get("title"):
            return ok({"message": "Ticket title is required"}, 400)
        ticket_ref = ref(f"tenants/{tenant_id}/tickets").push(
            {
                "title": str(data.get("title") or "").strip(),
                "description": str(data.get("description") or "").strip(),
                "customer_id": str(data.get("customer_id") or "").strip(),
                "status": data.get("status") or "open",
                "priority": data.get("priority") or "medium",
            }
        )
        return ok({"success": True, "message": "Ticket created", "ticketId": ticket_ref.key}, 201)
    if not ticket_id:
        return ok({"message": "Ticket id is required"}, 400)
    ticket = ref(f"tenants/{tenant_id}/tickets/{ticket_id}").get()
    if not ticket:
        return ok({"message": "Ticket not found"}, 404)
    if method(request, "PATCH"):
        data = body(request)
        allowed = ["title", "description", "customer_id", "status", "priority"]
        updates = {field: data[field] for field in allowed if field in data}
        if updates.get("status") in {"resolved", "closed"} and not ticket.get("resolved_at"):
            updates["resolved_at"] = iso_now()
        updates["updated_at"] = iso_now()
        ref(f"tenants/{tenant_id}/tickets/{ticket_id}").update(updates)
        return ok({"success": True, "message": "Ticket updated", "ticket": {"id": ticket_id, **ticket, **updates}})
    if method(request, "DELETE"):
        ref(f"tenants/{tenant_id}/tickets/{ticket_id}").delete()
        return ok({"success": True, "message": "Ticket deleted"})
    return ok({"message": "Method not allowed"}, 405)


@csrf_exempt
@api_view(["POST"])
@tenant_required
def settings_delete_customers(request):
    data = body(request)
    if str(data.get("confirm") or "").strip() != str(request.tenant.get("business_name") or "").strip():
        return ok({"message": "Type your business name exactly to confirm"}, 400)
    customers_data = list_children(f"tenants/{request.tenant['id']}/customers")
    for customer in customers_data:
        try:
            delete_router_customer(request.tenant, customer.get("username"), customer.get("service_type") or "pppoe")
        except Exception:
            pass
        ref(f"tenants/{request.tenant['id']}/customers/{customer['id']}").delete()
    return ok({"success": True, "message": f"Deleted {len(customers_data)} customers"})


@csrf_exempt
@api_view(["GET", "PATCH"])
@tenant_required
def settings_mikrotik(request):
    if method(request, "GET"):
        linked_routers = request.tenant.get("linked_routers") or {}
        if _router_is_agent_linked(request.tenant) and not linked_routers:
            linked_routers = {"primary": _linked_router_from_tenant(request.tenant)}
        return ok({
            "mikrotik_host": request.tenant.get("mikrotik_host", ""),
            "mikrotik_user": request.tenant.get("mikrotik_user", ""),
            "mikrotik_port": int(request.tenant.get("mikrotik_port") or 8728),
            "has_mikrotik_password": bool(request.tenant.get("mikrotik_pass")),
            "mikrotik_provisioning_status": request.tenant.get("mikrotik_provisioning_status", ""),
            "mikrotik_provisioned_at": request.tenant.get("mikrotik_provisioned_at", ""),
            "mikrotik_last_seen_at": request.tenant.get("mikrotik_last_seen_at", ""),
            "mikrotik_last_seen_ip": request.tenant.get("mikrotik_last_seen_ip", ""),
            "mikrotik_detected_identity": request.tenant.get("mikrotik_detected_identity", ""),
            "mikrotik_detected_version": request.tenant.get("mikrotik_detected_version", ""),
            "mikrotik_detected_board": request.tenant.get("mikrotik_detected_board", ""),
            "linked_routers": linked_routers,
            "router_port_assignments": request.tenant.get("router_port_assignments") or {},
        })
    data = body(request)
    updates = {}
    for field in ["mikrotik_host", "mikrotik_user", "mikrotik_pass"]:
        if field in data and (field != "mikrotik_pass" or str(data[field]).strip()):
            updates[field] = str(data[field]).strip() if field != "mikrotik_pass" else str(data[field])
    if "mikrotik_port" in data:
        updates["mikrotik_port"] = int(data.get("mikrotik_port") or 8728)
    if "mikrotik_port" in updates and not 1 <= updates["mikrotik_port"] <= 65535:
        return ok({"message": "MikroTik port must be between 1 and 65535"}, 400)
    updates["mikrotik_updated_at"] = iso_now()
    ref(f"tenants/{request.tenant['id']}").update(updates)
    merged = {**request.tenant, **updates}
    return ok({"success": True, "message": "MikroTik configuration saved", "config": {"mikrotik_host": merged.get("mikrotik_host", ""), "mikrotik_user": merged.get("mikrotik_user", ""), "mikrotik_port": int(merged.get("mikrotik_port") or 8728), "has_mikrotik_password": bool(merged.get("mikrotik_pass"))}})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def settings_mikrotik_test(request):
    candidate = {**request.tenant, **body(request)}
    if not candidate.get("mikrotik_pass"):
        return ok({"message": "MikroTik password is required to test the connection"}, 400)
    try:
        profiles = router_items(candidate, "ppp", "profile")
        return ok({"success": True, "mode": "routeros_api", "message": "MikroTik live API connection successful.", "profile_count": len(profiles)})
    except (TimeoutError, OSError) as exc:
        live_error = str(exc)
    except Exception as exc:
        live_error = str(exc)

    snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
    status = request.tenant.get("mikrotik_provisioning_status")
    if status in {"script_downloaded", "completed"} or snapshot:
        return ok({
            "success": True,
            "mode": "provisioning_callback",
            "message": "Router provisioning is connected. The router successfully reached this app.",
            "profile_count": len((snapshot.get("profiles") or {}).get("pppoe") or []),
            "warning": f"Using provisioning snapshot/agent mode. Live RouterOS API is not reachable from the server yet: {live_error}",
        })
    return ok({"message": f"Unable to reach MikroTik live API. Confirm public host/port forwarding to {candidate.get('mikrotik_port') or 8728}: {live_error}"}, 400)


@csrf_exempt
@api_view(["GET", "PATCH"])
@tenant_required
def settings_notifications(request):
    if method(request, "GET"):
        return ok(
            {
                "provider": request.tenant.get("notification_provider") or "whatsapp_cloud",
                "sms_enabled": request.tenant.get("sms_enabled") is not False,
                "whatsapp_enabled": bool(request.tenant.get("whatsapp_enabled")) or os.getenv("WHATSAPP_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
                "roamtech_sender_id": request.tenant.get("roamtech_sender_id") or "",
                "payment_sms_template": request.tenant.get("payment_sms_template") or "Hi {{name}}, your {{package}} payment of Ksh {{amount}} is confirmed. Username: {{username}}, Password: {{password}}.",
                "payment_whatsapp_template": request.tenant.get("payment_whatsapp_template") or "Hi {{name}}, your {{package}} internet package is active. Amount: Ksh {{amount}}. Username: {{username}}, Password: {{password}}.",
            }
        )
    data = body(request)
    updates = {
        "notification_provider": str(data.get("provider") or "whatsapp_cloud").strip(),
        "sms_enabled": data.get("sms_enabled") is not False,
        "whatsapp_enabled": bool(data.get("whatsapp_enabled")),
        "roamtech_sender_id": str(data.get("roamtech_sender_id") or "").strip(),
        "payment_sms_template": str(data.get("payment_sms_template") or "").strip(),
        "payment_whatsapp_template": str(data.get("payment_whatsapp_template") or "").strip(),
        "notifications_updated_at": iso_now(),
    }
    ref(f"tenants/{request.tenant['id']}").update(updates)
    return ok({"success": True, "message": "Notification settings saved", "config": updates})


def to_access_username(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def render_notification_template(template, context):
    rendered = str(template or "")
    for key, value in context.items():
        rendered = rendered.replace("{{" + key + "}}", str(value if value is not None else ""))
    return rendered


def notify_payment_access(tenant, payment, access):
    if not ((tenant or {}).get("whatsapp_enabled") or os.getenv("WHATSAPP_ENABLED", "false").lower() in {"1", "true", "yes", "on"}):
        return None
    template = (tenant or {}).get("payment_whatsapp_template") or "Hi {{name}}, your {{package}} internet package is active. Amount: Ksh {{amount}}. Username: {{username}}, Password: {{password}}."
    message = render_notification_template(
        template,
        {
            "name": payment.get("customer_name") or payment.get("phone") or "customer",
            "package": payment.get("package_name") or "",
            "amount": payment.get("amount") or "",
            "username": access.get("username") or access.get("mac_address") or "",
            "password": access.get("password") or "",
            "expires_at": access.get("expiry_date") or "",
        },
    )
    return send_whatsapp_message(payment.get("phone"), message, tenant)


def activate_paid_access(tenant, payment_id, payment, phone, payment_code):
    tenant_id = tenant["id"]
    package_name = payment.get("package_name")
    customers_data = list_children(f"tenants/{tenant_id}/customers")
    customer = None
    if payment.get("customer_id"):
        customer = next((c for c in customers_data if str(c.get("id")) == str(payment.get("customer_id"))), None)
    if not customer and payment.get("username"):
        customer = next((c for c in customers_data if str(c.get("username") or "").lower() == str(payment.get("username") or "").lower()), None)
    if not customer and phone:
        customer = next((c for c in customers_data if str(c.get("phone")) == str(phone)), None)
    service_type = payment.get("service_type") or (customer or {}).get("service_type") or "hotspot"
    package_for_access = package_name or (customer or {}).get("package")
    pkg = find_child_by_field(f"tenants/{tenant_id}/packages", "name", package_for_access)
    expiry = utcnow() + package_duration_delta(pkg)
    mac_address = normalize_mac(payment.get("mac_address") or (customer or {}).get("mac_address"))
    username = mac_address if service_type == "tv" else (payment.get("username") or (customer or {}).get("username") or to_access_username(phone))
    password = str(payment_code)
    if customer:
        updates = {"username": username, "password": password, "package": package_for_access, "service_type": service_type, "status": "active", "expiry_date": expiry.isoformat(), "last_payment_id": payment_id, "last_payment_code": payment_code, "auto_reconnect": True, "updated_at": iso_now()}
        if mac_address:
            updates["mac_address"] = mac_address
        ref(f"tenants/{tenant_id}/customers/{customer['id']}").update(updates)
        customer_id = customer["id"]
    else:
        new_ref = ref(f"tenants/{tenant_id}/customers").push({"name": mac_address or phone, "phone": phone, "username": username, "password": password, "package": package_for_access, "service_type": service_type, "status": "active", "expiry_date": expiry.isoformat(), "last_payment_id": payment_id, "last_payment_code": payment_code, "auto_reconnect": True, "mac_address": mac_address, "created_at": iso_now()})
        customer_id = new_ref.key
    if service_type == "hotspot" and pkg:
        create_hotspot_profile(tenant, pkg["name"], pkg.get("speed"))
    if service_type == "pppoe" and pkg:
        create_ppp_profile(tenant, pkg["name"], pkg.get("speed"))
    if service_type == "tv" and not mac_address:
        raise ValueError("TV MAC address is required for activation")
    if tenant.get("radius_enabled") and service_type in {"hotspot", "pppoe"}:
        try:
            from .radius_provisioning import sync_radius_customer, upsert_pg_customer

            tenant_obj = Tenant.objects.get(pk=tenant_id)
            upsert_pg_customer(
                tenant_obj,
                {
                    "name": (customer or {}).get("name") or phone or username,
                    "phone": phone,
                    "username": username,
                    "password": password,
                    "package": package_for_access,
                    "service_type": service_type,
                    "status": "active",
                },
            )
            sync_radius_customer(tenant_obj, {"username": username, "password": password})
        except Exception:
            pass
    upsert_customer_access(tenant, {"username": username, "password": password, "package_name": package_for_access, "service_type": service_type, "mac_address": mac_address})
    set_customer_enabled(tenant, username, service_type, True)
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"customer_id": customer_id, "access_username": username, "access_password": password, "access_mac_address": mac_address, "access_expires_at": expiry.isoformat(), "access_status": "active", "auto_reconnect": True})
    access = {"username": username, "password": password, "mac_address": mac_address, "expiry_date": expiry.isoformat()}
    try:
        notify_result = notify_payment_access(tenant, {**payment, "phone": phone, "package_name": package_for_access}, access)
        if notify_result:
            ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"whatsapp_status": "sent" if notify_result.get("sent") else "skipped", "whatsapp_detail": notify_result.get("skipped") or ""})
    except Exception as exc:
        ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"whatsapp_status": "failed", "whatsapp_detail": str(exc)})
    return access


def find_payment_by_paystack_reference(reference, tenant_id=None, payment_id=None):
    tenant_ids = [tenant_id] if tenant_id else [tenant["id"] for tenant in list_children("tenants")]
    for current_tenant_id in tenant_ids:
        if not current_tenant_id:
            continue
        if payment_id:
            payment = ref(f"tenants/{current_tenant_id}/payments/{payment_id}").get()
            if payment:
                return current_tenant_id, payment_id, payment
        for item in list_children(f"tenants/{current_tenant_id}/payments"):
            if item.get("paystack_reference") == reference:
                return current_tenant_id, item["id"], item
    return None, None, None


def complete_paystack_payment(event_data):
    reference = event_data.get("reference")
    metadata = event_data.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    tenant_id, payment_id, payment = find_payment_by_paystack_reference(reference, metadata.get("tenant_id"), metadata.get("payment_id"))
    if not tenant_id or not payment_id:
        return False
    if payment.get("status") == "success":
        return True

    expected_amount = round(float(payment.get("amount") or 0), 2)
    paid_amount = round(float(event_data.get("amount") or 0) / 100, 2)
    if expected_amount and paid_amount != expected_amount:
        ref(f"tenants/{tenant_id}/payments/{payment_id}").update({
            "status": "failed",
            "failed_at": iso_now(),
            "callback_result_desc": "Paid amount does not match the package amount",
        })
        return False

    customer = event_data.get("customer") or {}
    authorization = event_data.get("authorization") or {}
    payment_code = reference or event_data.get("id")
    phone = metadata.get("phone") or payment.get("phone")
    update = {
        "provider": "paystack",
        "amount": paid_amount,
        "currency": event_data.get("currency") or payment.get("currency"),
        "payment_code": payment_code,
        "paystack_reference": reference,
        "paystack_transaction_id": event_data.get("id"),
        "paystack_channel": event_data.get("channel"),
        "paystack_paid_at": event_data.get("paid_at") or event_data.get("paidAt"),
        "paystack_customer_email": customer.get("email") or payment.get("paystack_customer_email"),
        "paystack_authorization_code": authorization.get("authorization_code"),
        "phone": phone,
        "status": "success",
        "paid_at": iso_now(),
        "callback_result_code": "success",
        "callback_result_desc": event_data.get("gateway_response") or "Paystack charge successful",
    }
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update(update)
    tenant_data = ref(f"tenants/{tenant_id}").get() or {}
    activate_paid_access({"id": tenant_id, **tenant_data}, payment_id, {**payment, **metadata}, phone, payment_code)
    return True


def paystack_secrets_to_try():
    seen = set()
    for secret in [os.getenv("PAYSTACK_SECRET_KEY")]:
        if secret and "replace_with" not in secret and not secret.strip().endswith("_secret_key") and secret not in seen:
            seen.add(secret)
            yield secret


@csrf_exempt
@api_view(["POST"])
def paystack_webhook(request):
    signature = request.headers.get("x-paystack-signature") or request.headers.get("X-Paystack-Signature")
    if not any(verify_paystack_signature(request.body, signature, secret) for secret in paystack_secrets_to_try()):
        return ok({"message": "Invalid Paystack signature"}, 401)
    event = body(request)
    if event.get("event") == "charge.success":
        event_data = event.get("data") or {}
        reference = event_data.get("reference")
        tenant_id, payment_id, payment = find_payment_by_paystack_reference(reference, (event_data.get("metadata") or {}).get("tenant_id"), (event_data.get("metadata") or {}).get("payment_id"))
        if payment and payment.get("status") == "success":
            return ok({"success": True})
        if reference:
            tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})} if tenant_id else {}
            try:
                verified = verify_paystack_transaction(tenant, reference)
                complete_paystack_payment(verified)
            except Exception:
                return ok({"success": False, "message": "Payment verification will be retried"}, 202)
    return ok({"success": True})


@csrf_exempt
@api_view(["GET"])
def paystack_callback(request):
    reference = request.GET.get("reference") or request.GET.get("trxref")
    if not reference:
        return ok({"message": "Missing Paystack reference"}, 400)
    tenant_id, payment_id, payment = find_payment_by_paystack_reference(reference)
    if not tenant_id:
        return ok({"message": "Payment not found"}, 404)
    tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})}
    try:
        verified = verify_paystack_transaction(tenant, reference)
    except Exception:
        return ok({"success": False, "message": "Payment verification is temporarily unavailable. Please refresh shortly."}, 202)
    if verified.get("status") == "success":
        if not complete_paystack_payment(verified):
            return ok({"success": False, "message": "The paid amount did not match the package amount."}, 400)
        if "text/html" in request.headers.get("accept", ""):
            return redirect(f"/portal/{tenant_id}?reference={reference}")
        return ok({"success": True, "message": "Payment verified. You can return to the customer portal.", "paymentId": payment_id})
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"status": "failed", "callback_result_desc": verified.get("gateway_response") or "Paystack verification did not succeed", "failed_at": iso_now()})
    return ok({"success": False, "message": "Payment was not successful"}, 400)


def complete_daraja_payment(tenant_id, payment_id, callback_metadata, amount, receipt, paid_at, phone):
    payment = ref(f"tenants/{tenant_id}/payments/{payment_id}").get()
    if not payment:
        return False
    if payment.get("status") == "success":
        return True

    expected_amount = round(float(payment.get("amount") or 0), 2)
    paid_amount = round(float(amount or 0), 2)
    if expected_amount and paid_amount != expected_amount:
        ref(f"tenants/{tenant_id}/payments/{payment_id}").update({
            "status": "failed",
            "failed_at": iso_now(),
            "callback_result_desc": "Paid amount does not match the package amount",
        })
        return False

    update = {
        "provider": "mpesa",
        "amount": paid_amount,
        "payment_code": receipt,
        "daraja_receipt_number": receipt,
        "daraja_paid_at": paid_at,
        "phone": phone or payment.get("phone"),
        "status": "success",
        "paid_at": iso_now(),
        "callback_result_code": "success",
        "callback_result_desc": "M-Pesa payment successful",
    }
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update(update)
    tenant_data = ref(f"tenants/{tenant_id}").get() or {}
    activate_paid_access({"id": tenant_id, **tenant_data}, payment_id, {**payment, **callback_metadata}, phone or payment.get("phone"), receipt)
    return True


@csrf_exempt
@api_view(["POST"])
def daraja_callback(request, tenant_id, payment_id, token):
    # Daraja has no request-signing mechanism — the per-payment token
    # embedded in the callback URL at STK push time is what stops this
    # endpoint from being used to spoof completion of an arbitrary payment.
    if not verify_daraja_callback_token(tenant_id, payment_id, token):
        return ok({"success": False, "message": "Invalid callback token"}, 401)

    event = body(request)
    stk_callback = (((event or {}).get("Body") or {}).get("stkCallback")) or {}
    result_code = stk_callback.get("ResultCode")
    result_desc = stk_callback.get("ResultDesc") or ""

    if str(result_code) != "0":
        ref(f"tenants/{tenant_id}/payments/{payment_id}").update({
            "status": "failed",
            "failed_at": iso_now(),
            "callback_result_code": result_code,
            "callback_result_desc": result_desc or "M-Pesa payment was cancelled or failed",
        })
        # Always 200 back to Safaricom — they retry on non-2xx, we don't
        # want retries for a customer-cancelled payment.
        return ok({"success": True})

    items = ((stk_callback.get("CallbackMetadata") or {}).get("Item")) or []
    values = {item.get("Name"): item.get("Value") for item in items if isinstance(item, dict)}
    amount = values.get("Amount")
    receipt = values.get("MpesaReceiptNumber")
    paid_at = values.get("TransactionDate")
    phone = values.get("PhoneNumber")

    complete_daraja_payment(tenant_id, payment_id, {}, amount, receipt, paid_at, phone)
    return ok({"success": True})


@csrf_exempt
@api_view(["POST"])
def admin_login(request):
    data = body(request)
    if not data.get("email") or not data.get("password"):
        return ok({"error": "Email and password required"}, 400)
    email = str(data["email"]).lower().strip()
    password = data["password"]
    admin = find_child_by_field("admins", "email", email)

    if admin and admin.get("is_active") and check_password(password, admin.get("password")):
        login_count = int(admin.get("login_count") or 0) + 1
        ref(f"admins/{admin['id']}").update({"last_login": iso_now(), "login_count": login_count})
        write_audit_log(admin["id"], admin.get("email"), "LOGIN", admin["id"], "admin", request, {"login_count": login_count})
        try:
            token = admin_token(admin["id"], admin)
        except Exception as exc:
            return ok({"error": f"Server configuration error: {exc}"}, 500)
        return ok({"token": token, "admin": {"id": admin["id"], "name": admin.get("name"), "email": admin.get("email"), "role": admin.get("role")}})

    user = User.objects.filter(email__iexact=email).first()
    if not user or not user.is_active or not user.check_password(password):
        return ok({"error": "Invalid credentials"}, 401)
    if not (user.is_superuser or user.is_staff or user.role == User.Role.ADMIN):
        return ok({"error": "Insufficient privileges"}, 403)

    admin_profile = AdminUser.objects.filter(user=user).first() or AdminUser.objects.filter(email__iexact=email).first()
    if not admin_profile:
        admin_profile = AdminUser(user=user, name=user.name, email=user.email, password="", role="admin", is_active=True)
    admin_profile.user = user
    admin_profile.name = admin_profile.name or user.name
    admin_profile.email = user.email
    admin_profile.role = "admin"
    admin_profile.is_active = True
    admin_profile.last_login = iso_now()
    admin_profile.login_count = int(admin_profile.login_count or 0) + 1
    admin_profile.save()

    admin = admin_profile.as_dict()
    admin["id"] = str(admin_profile.pk)
    write_audit_log(admin["id"], admin.get("email"), "LOGIN", admin["id"], "admin", request, {"login_count": admin_profile.login_count})
    try:
        token = admin_token(admin["id"], admin)
    except Exception as exc:
        return ok({"error": f"Server configuration error: {exc}"}, 500)
    return ok({"token": token, "admin": {"id": admin["id"], "name": admin.get("name"), "email": admin.get("email"), "role": admin.get("role")}})


def mask_tenant(tenant):
    return {key: (MASKED if key in SENSITIVE_FIELDS and value else value) for key, value in tenant.items()}


def tenant_admin_payload(tenant):
    tenant_id = tenant.get("id")
    instance = Tenant.objects.filter(pk=tenant_id).first()
    subscription = ensure_subscription(instance) if instance else None
    payload = {"id": tenant_id, **mask_tenant(tenant)}
    payload["subscription"] = subscription_payload(subscription) if subscription else None
    payload["onboarding"] = {
        "mikrotik": bool(tenant.get("mikrotik_host") and tenant.get("mikrotik_user")),
        "customers": len(list_children(f"tenants/{tenant_id}/customers")) > 0,
        "packages": len(list_children(f"tenants/{tenant_id}/packages")) > 0,
    }
    return payload


@csrf_exempt
@api_view(["GET", "POST", "PATCH", "DELETE"])
@admin_required
def admin_tenants(request, tenant_id=None, child=None):
    if tenant_id and child == "customers":
        return ok(list_children(f"tenants/{tenant_id}/customers"))
    if tenant_id and child == "payments":
        return ok(list_children(f"tenants/{tenant_id}/payments"))
    if tenant_id and child == "packages":
        return ok(list_children(f"tenants/{tenant_id}/packages"))
    if method(request, "GET") and not tenant_id:
        tenants = [tenant_admin_payload(item) for item in list_children("tenants")]
        write_audit_log(request.admin["adminId"], request.admin["email"], "LIST_TENANTS", target_type="tenant", request=request, metadata={"count": len(tenants)})
        return ok(tenants)
    if method(request, "GET") and tenant_id:
        tenant = ref(f"tenants/{tenant_id}").get()
        if not tenant:
            return ok({"error": "Tenant not found"}, 404)
        return ok(tenant_admin_payload({"id": tenant_id, **tenant}))
    if method(request, "POST") and not tenant_id:
        data = body(request)
        required = ["business_name", "owner_name", "email", "phone", "password", "mikrotik_host", "mikrotik_user", "mikrotik_pass"]
        missing = [field for field in required if not data.get(field)]
        if missing:
            return ok({"error": f"Missing fields: {', '.join(missing)}"}, 400)
        if find_child_by_field("tenants", "email", data["email"]):
            return ok({"error": "Email already registered"}, 409)
        optional_payment_fields = ["paystack_secret_key", "paystack_subaccount_code", "paystack_bearer", "paystack_currency"]
        new_ref = ref("tenants").push({**{field: data.get(field) for field in required if field != "password"}, **{field: data.get(field, "") for field in optional_payment_fields}, "paystack_bearer": data.get("paystack_bearer") or "subaccount", "paystack_currency": data.get("paystack_currency") or os.getenv("PAYSTACK_CURRENCY", "KES"), "email": data["email"].lower().strip(), "password": hash_password(data["password"]), "mikrotik_port": int(data.get("mikrotik_port") or 8728), "status": "active", "created_by": f"admin:{request.admin['adminId']}", "created_at": iso_now()})
        tenant_instance = Tenant.objects.get(pk=new_ref.key)
        ensure_subscription(tenant_instance, data.get("plan") or "basic")
        write_audit_log(request.admin["adminId"], request.admin["email"], "CREATE_TENANT", new_ref.key, "tenant", request, {"business_name": data["business_name"], "email": data["email"].lower().strip()})
        return ok({"message": "Tenant created", "tenantId": new_ref.key}, 201)
    if method(request, "PATCH") and tenant_id:
        data = body(request)
        existing_tenant = ref(f"tenants/{tenant_id}").get() or {}
        if "password" in data:
            return ok({"error": "Cannot update sensitive fields via this route: password"}, 400)
        allowed = ["business_name", "owner_name", "email", "phone", "mikrotik_host", "mikrotik_user", "mikrotik_pass", "mikrotik_port", "paystack_secret_key", "paystack_subaccount_code", "paystack_bearer", "paystack_currency", "status"]
        updates = {}
        for field in allowed:
            if field in data:
                value = data[field]
                if field in {"mikrotik_pass", "paystack_secret_key"} and (not str(value).strip() or value == MASKED):
                    continue
                updates[field] = value
        if "email" in updates:
            updates["email"] = str(updates["email"]).lower().strip()
        if "mikrotik_port" in updates:
            updates["mikrotik_port"] = int(updates["mikrotik_port"] or 8728)
        if not updates:
            return ok({"error": "No allowed fields provided"}, 400)
        ref(f"tenants/{tenant_id}").update(updates)
        if updates.get("status") == "active" and existing_tenant.get("status") != "active":
            notify_tenant_activated({**existing_tenant, **updates, "id": tenant_id})
        if data.get("plan"):
            tenant_instance = Tenant.objects.filter(pk=tenant_id).first()
            if tenant_instance:
                subscription = ensure_subscription(tenant_instance, data.get("plan"))
                subscription.plan = data.get("plan")
                subscription.save(update_fields=["plan", "updated_at"])
        write_audit_log(request.admin["adminId"], request.admin["email"], "UPDATE_TENANT", tenant_id, "tenant", request, {"updated_fields": list(updates)})
        return ok({"message": "Tenant updated"})
    if method(request, "DELETE") and tenant_id:
        ref(f"tenants/{tenant_id}").update({"status": "suspended", "suspended_by": request.admin["adminId"], "suspended_at": iso_now()})
        write_audit_log(request.admin["adminId"], request.admin["email"], "SUSPEND_TENANT", tenant_id, "tenant", request)
        return ok({"message": "Tenant suspended"})
    return ok({"message": " Method not allowed "}, 405)


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_stats(request):
    return admin_system_stats(request)


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_system_stats(request):
    now = timezone.now()
    today = now.date()
    month_payments = SubscriptionPayment.objects.filter(paid_at__year=now.year, paid_at__month=now.month).aggregate(total=Sum("amount"))["total"] or 0
    payments_today = Payment.objects.filter(paid_at__startswith=today.isoformat()).aggregate(total=Sum("amount"))["total"] or 0
    tenants_qs = Tenant.objects.all()
    top_tenants = []
    for tenant in tenants_qs:
        top_tenants.append({"id": str(tenant.pk), "business_name": tenant.business_name, "customer_count": Customer.objects.filter(tenant=tenant).count()})
    top_tenants = sorted(top_tenants, key=lambda item: item["customer_count"], reverse=True)[:5]
    return ok(
        {
            "totalTenants": tenants_qs.count(),
            "activeTenants": tenants_qs.filter(status="active").count(),
            "suspendedTenants": tenants_qs.filter(status="suspended").count(),
            "pendingTenants": tenants_qs.filter(status="pending_setup").count(),
            "totalCustomers": Customer.objects.count(),
            "paymentsToday": float(payments_today or 0),
            "monthlyRevenue": float(month_payments or 0),
            "expiringThisWeek": TenantSubscription.objects.filter(expires_at__lte=now + timedelta(days=7), expires_at__gte=now).count(),
            "expiredCount": TenantSubscription.objects.filter(expires_at__lt=now).count(),
            "systemHealth": health_payload(),
            "topTenants": top_tenants,
        }
    )


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_revenue_chart(request):
    try:
        days = min(90, max(1, int(request.GET.get("days", 30))))
    except ValueError:
        days = 30
    now = timezone.now()
    start = now - timedelta(days=days - 1)
    rows = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        total = SubscriptionPayment.objects.filter(paid_at__date=day).aggregate(total=Sum("amount"))["total"] or 0
        rows.append({"date": day.isoformat(), "amount": float(total)})
    return ok(rows)


@csrf_exempt
@api_view(["GET", "PATCH"])
@admin_required
def admin_subscriptions(request, subscription_id=None):
    if subscription_id:
        subscription = TenantSubscription.objects.select_related("tenant").filter(pk=subscription_id).first()
        if not subscription:
            return err("Subscription not found", 404)
        if method(request, "GET"):
            return ok(subscription_payload(subscription, include_payments=True))
        data = body(request)
        for field in ["plan", "amount", "expires_at", "auto_renew", "notes"]:
            if field in data:
                value = data[field]
                if field == "expires_at":
                    value = parse_date(value)
                setattr(subscription, field, value)
        subscription.save()
        write_audit_log(request.admin["adminId"], request.admin["email"], "UPDATE_SUBSCRIPTION", str(subscription.pk), "subscription", request)
        return ok({"message": "Subscription updated", "subscription": subscription_payload(subscription)})
    qs = TenantSubscription.objects.select_related("tenant").order_by("expires_at")
    status = request.GET.get("status", "all")
    plan = request.GET.get("plan")
    search = str(request.GET.get("search") or "").lower()
    now = timezone.now()
    if status == "expired":
        qs = qs.filter(expires_at__lt=now)
    elif status == "expiring_soon":
        qs = qs.filter(expires_at__gte=now, expires_at__lte=now + timedelta(days=7))
    elif status == "active":
        qs = qs.filter(expires_at__gte=now)
    if plan and plan != "all":
        qs = qs.filter(plan=plan)
    items = [subscription_payload(item) for item in qs]
    if search:
        items = [item for item in items if search in item.get("tenant_name", "").lower() or search in item.get("tenant_email", "").lower()]
    return ok(paginate_items(request, items))


@csrf_exempt
@api_view(["GET", "POST"])
@admin_required
def admin_subscription_payments(request, subscription_id):
    subscription = TenantSubscription.objects.select_related("tenant").filter(pk=subscription_id).first()
    if not subscription:
        return err("Subscription not found", 404)
    if method(request, "GET"):
        return ok([payment.as_dict() for payment in subscription.payments.order_by("-paid_at")])
    payment = record_subscription_payment(subscription, body(request), request.admin.get("email"))
    write_audit_log(request.admin["adminId"], request.admin["email"], "RECORD_SUBSCRIPTION_PAYMENT", str(subscription.pk), "subscription", request, {"payment_id": payment.pk})
    return ok({"message": "Payment recorded", "payment": payment.as_dict(), "subscription": subscription_payload(subscription)})


@csrf_exempt
@api_view(["GET", "PATCH", "POST"])
@admin_required
def admin_tenant_subscription(request, tenant_id):
    tenant = Tenant.objects.filter(pk=tenant_id).first()
    if not tenant:
        return err("Tenant not found", 404)
    subscription = ensure_subscription(tenant)
    if method(request, "GET"):
        return ok(subscription_payload(subscription, include_payments=True))
    if method(request, "PATCH"):
        data = body(request)
        for field in ["plan", "amount", "expires_at", "auto_renew", "notes"]:
            if field in data:
                value = data[field]
                if field == "expires_at":
                    value = parse_date(value)
                setattr(subscription, field, value)
        subscription.save()
        write_audit_log(request.admin["adminId"], request.admin["email"], "UPDATE_TENANT_SUBSCRIPTION", tenant_id, "tenant", request)
        return ok({"message": "Subscription updated", "subscription": subscription_payload(subscription, include_payments=True)})
    payment = record_subscription_payment(subscription, body(request), request.admin.get("email"))
    write_audit_log(request.admin["adminId"], request.admin["email"], "RECORD_TENANT_SUBSCRIPTION_PAYMENT", tenant_id, "tenant", request, {"payment_id": payment.pk})
    return ok({"message": "Payment recorded", "payment": payment.as_dict(), "subscription": subscription_payload(subscription, include_payments=True)})


@csrf_exempt
@api_view(["POST"])
@admin_required
def admin_subscription_remind(request, tenant_id):
    write_audit_log(request.admin["adminId"], request.admin["email"], "SEND_SUBSCRIPTION_REMINDER", tenant_id, "tenant", request)
    return ok({"message": "Reminder queued"})


@csrf_exempt
@api_view(["POST"])
@admin_required
def admin_mikrotik_test(request, tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return err("Tenant not found", 404)
    try:
        profiles = router_items({"id": tenant_id, **tenant}, "ppp", "profile")
        return ok({"success": True, "error": None, "routers_count": len(profiles), "profile_count": len(profiles)})
    except Exception as exc:
        return ok({"success": False, "error": str(exc), "routers_count": 0}, 400)


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_system_migrations(request):
    out = StringIO()
    call_command("showmigrations", stdout=out)
    return ok({"migrations": out.getvalue().splitlines()})


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_system(request):
    return ok({"health": health_payload(), "database": settings.DATABASES["default"]["ENGINE"], "rate_limits": SimpleRateLimitMiddleware.RULES if False else {}})


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_legacy_stats_unreachable(request):
    tenants = list_children("tenants")
    today = iso_now()[:10]
    total_customers = 0
    payments_today = 0
    for tenant in tenants:
        total_customers += len(list_children(f"tenants/{tenant['id']}/customers"))
        for payment in list_children(f"tenants/{tenant['id']}/payments"):
            if payment.get("paid_at") and str(payment["paid_at"])[:10] == today:
                payments_today += float(payment.get("amount") or 0)
    return ok({"totalTenants": len(tenants), "activeTenants": len([t for t in tenants if t.get("status") != "suspended"]), "suspendedTenants": len([t for t in tenants if t.get("status") == "suspended"]), "totalCustomers": total_customers, "paymentsToday": payments_today, "systemHealth": "healthy"})


@csrf_exempt
@api_view(["GET"])
@admin_required
def admin_audit_logs(request):
    logs = sorted(list_children("admin_audit_logs"), key=lambda item: str(item.get("timestamp")), reverse=True)[:100]
    write_audit_log(request.admin["adminId"], request.admin["email"], "VIEW_AUDIT_LOGS", target_type="admin", request=request, metadata={"count": len(logs)})
    return ok(logs)


@csrf_exempt
@api_view(["GET", "PATCH"])
@admin_required
def admin_site(request):
    if method(request, "GET"):
        return ok(ref("site_settings").get() or {})
    data = body(request)
    allowed = ["brand_name", "headline", "subheadline", "about", "phone", "email", "location", "address", "cta_label", "cta_url"]
    updates = {field: data[field] for field in allowed if field in data}
    ref("site_settings").update({**updates, "updated_at": iso_now(), "updated_by": request.admin["adminId"]})
    write_audit_log(request.admin["adminId"], request.admin["email"], "UPDATE_SITE", target_type="site", request=request, metadata={"updated_fields": list(updates)})
    return ok({"message": "Site settings updated"})


@csrf_exempt
@api_view(["GET", "PATCH", "POST"])
@admin_required
def admin_users(request, tenant_id=None, customer_id=None, action=None):
    if method(request, "GET"):
        users = []
        for tenant in list_children("tenants"):
            for customer in list_children(f"tenants/{tenant['id']}/customers"):
                users.append({**customer, "tenant_id": tenant["id"], "tenant_name": tenant.get("business_name")})
        return ok(users)
    tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})}
    customer = ref(f"tenants/{tenant_id}/customers/{customer_id}").get()
    if not tenant or not customer:
        return ok({"error": "Tenant or user not found"}, 404)
    if method(request, "PATCH"):
        data = body(request)
        allowed = ["name", "phone", "username", "package", "status", "expiry_date", "auto_reconnect"]
        updates = {field: data[field] for field in allowed if field in data}
        ref(f"tenants/{tenant_id}/customers/{customer_id}").update(updates)
        write_audit_log(request.admin["adminId"], request.admin["email"], "UPDATE_USER", customer_id, "customer", request, {"tenantId": tenant_id, "updated_fields": list(updates)})
        return ok({"message": "User updated"})
    if action == "reconnect":
        set_customer_enabled(tenant, customer.get("username"), customer.get("service_type", "hotspot"), True)
        return ok({"message": "User reconnected"})
    if action == "disable":
        set_customer_enabled(tenant, customer.get("username"), customer.get("service_type", "hotspot"), False)
        ref(f"tenants/{tenant_id}/customers/{customer_id}").update({"status": "inactive"})
        return ok({"message": "User disabled"})
    return ok({"message": "Method not allowed"}, 405)
