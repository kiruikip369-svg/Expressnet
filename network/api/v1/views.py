import json
import html
import logging
import os
import re
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from urllib.parse import urlencode, urlparse

import jwt
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import close_old_connections, connection
from django.db.utils import OperationalError
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from billing_api.auth import admin_required, tenant_required
from billing_api.models import AdminUser, Customer, InternetPackage, Payment, SubscriptionPayment, Tenant, TenantSubscription, Ticket, User, Voucher
from billing_api.services import (
    admin_token,
    check_password,
    create_hotspot_profile,
    create_ppp_profile,
    configure_router_port,
    create_paystack_subaccount,
    selected_daraja_method,
    initiate_daraja_payment,
    platform_daraja_config,
    tenant_payout_details,
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
    expressnet_hotspot_file_html,
    hotspot_error_redirect_html,
    hotspot_login_redirect_html,
    hotspot_redirect_html,
    routeros_hotspot_fetch_script,
    initiate_paystack_payment,
    initiate_daraja_stk,
    iso_now,
    walled_garden_hosts,
    firebase_backup_configured,
    list_children,
    mikrotik_managed_bridge_name,
    normalize_public_url,
    normalize_phone,
    normalize_rate_limit,
    routeros_duration,
    package_service_type,
    PaymentProviderError,
    ref,
    _rsc_escape,
    _get_jwt_secret,
    router_connect,
    router_interface_status,
    router_items,
    send_sms_message,
    send_whatsapp_message,
    set_customer_enabled,
    tenant_token,
    upsert_customer_access,
    utcnow,
    verify_paystack_signature,
    verify_paystack_transaction,
    write_audit_log,
)

logger = logging.getLogger(__name__)


DEFAULT_SITE = {
    "brand_name": "Expressnet",
    "headline": "Internet billing built for hotspot businesses",
    "subheadline": "Sell packages, collect Paystack payments, and activate MikroTik users automatically.",
    "about": "We help hotspot operators manage customers, packages, payments, and access control from one secure platform.",
    "phone": "+254 701396967/+254 729 281669",
    "email": "expressnet.support@gmail.com",
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


def normalized_package_payload(data, default_service_type="hotspot", include_service_type=True):
    service_type = package_service_type(data or {})
    if service_type not in {"hotspot", "pppoe"}:
        service_type = default_service_type if default_service_type in {"hotspot", "pppoe"} else "hotspot"
    duration_unit = "hours" if str((data or {}).get("duration_unit") or "").lower().startswith("hour") else "days"
    if service_type == "pppoe":
        duration_unit = "days"
    duration_value = float((data or {}).get("duration_value") or (data or {}).get("duration_hours") or (data or {}).get("duration_days") or 1)
    if service_type == "pppoe" and duration_value < 1:
        duration_value = 1
    duration_days = 1 if duration_unit == "hours" else int(duration_value)
    duration_hours = duration_value if duration_unit == "hours" else duration_value * 24
    payload = {
        "duration_unit": duration_unit,
        "duration_value": duration_value,
        "duration_days": duration_days,
        "duration_hours": duration_hours,
    }
    if include_service_type:
        payload["service_type"] = service_type
    return payload


def sync_package_profile(tenant, package):
    service_type = package_service_type(package)
    duration_seconds = int(package_duration_delta(package).total_seconds())
    if service_type == "pppoe":
        return create_ppp_profile(tenant, package.get("name"), package.get("speed"), duration_seconds)
    return create_hotspot_profile(tenant, package.get("name"), package.get("speed"), duration_seconds)


def _package_sync_script_for_request(request, package):
    script = _package_profile_script(package)
    if script and package_service_type(package) == "hotspot":
        script = _hotspot_captive_file_script(
            {"id": request.tenant["id"], **request.tenant},
            public_base_url(request).rstrip("/"),
        ) + script
    return script

#u
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
        "daraja_environment": tenant.get("daraja_environment") or "production",
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
def public_packages(request, tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return ok({"message": "Tenant not found"}, 404)
    if tenant.get("status") == "suspended":
        return ok({"message": "Tenant is not accepting payments"}, 403)
    requested_service = str(request.GET.get("service_type") or "").strip().lower()
    packages = _public_packages_for_tenant(tenant_id, requested_service)
    if requested_service in {"hotspot", "pppoe"} and not packages:
        packages = _public_packages_for_tenant(tenant_id)
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
        from billing_api.models import RadiusSession as RadiusSessionModel
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


def _probe_tenant_id(request):
    requested = str(request.GET.get("tenant_id") or request.GET.get("tenant") or "").strip()
    if requested:
        return requested

    host = request.get_host().split(":", 1)[0].lower()
    for tenant in list_children("tenants"):
        tenant_host = str(tenant.get("captive_portal_host") or tenant.get("portal_host") or "").strip().lower()
        if tenant_host and tenant_host == host:
            return str(tenant.get("id") or "")

    active_tenants = [
        tenant
        for tenant in list_children("tenants")
        if tenant.get("status") not in {"suspended", "pending_setup"}
    ]
    if len(active_tenants) == 1:
        return str(active_tenants[0].get("id") or "")
    return ""


def _html_page(title, body, status=200):
    return HttpResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    *{{box-sizing:border-box}}
    body{{margin:0;font-family:Arial,sans-serif;background:#000;color:#fff;line-height:1.45;overflow-x:hidden}}
    header{{width:min(100% - 24px,760px);margin:12px auto 0;background:var(--portal-accent,#2600d8);color:white;padding:16px 18px 24px;border-radius:10px 10px 22px 22px;text-align:center;box-shadow:0 18px 38px var(--portal-accent-shadow,rgba(38,0,216,.28))}}
    header h1{{margin:8px 0 0;font-size:clamp(18px,5vw,22px);line-height:1.15;overflow-wrap:anywhere}}
    header p{{margin:10px 0 0;color:rgba(255,255,255,.9);font-size:14px;font-weight:700}}
    main{{width:100%;max-width:760px;margin:0 auto;padding:20px clamp(14px,4vw,22px) 28px}}
    .hero-logo{{width:82px;height:58px;margin:0 auto;border-radius:10px;background:rgba(255,255,255,.14);display:flex;align-items:center;justify-content:center;overflow:hidden;font-size:24px;font-weight:800}}
    .hero-logo img{{width:100%;height:100%;object-fit:cover;display:block}}
    .steps{{display:flex;align-items:center;justify-content:center;gap:9px;margin-top:12px;font-size:16px;font-weight:800}}
    .chev{{opacity:.75}}
    .call{{display:inline-flex;align-items:center;gap:9px;margin-top:16px;min-height:44px;border-radius:7px;background:rgba(0,0,0,.28);padding:10px 22px;color:#fff;text-decoration:none;font-size:17px;font-weight:800;letter-spacing:.02em}}
    .card{{background:#242424;border:1px solid rgba(255,255,255,.11);border-radius:10px;padding:16px;margin:12px 0;box-shadow:0 10px 26px rgba(0,0,0,.35);min-width:0}}
    .card strong{{font-size:15px;color:#fff;font-weight:700}}
    .quick{{display:grid;gap:10px}}
    .quick form{{display:grid;grid-template-columns:1fr;gap:8px}}
    .pkg{{display:flex;gap:14px;align-items:center;justify-content:space-between;min-height:88px;padding:17px 20px}}
    .pkg > *{{min-width:0}}
    .pkg-title{{font-size:16px;font-weight:750;text-transform:uppercase;line-height:1.22;overflow-wrap:anywhere}}
    .pkg-meta{{margin-top:5px;font-size:14px;color:#cbd5e1}}
    form{{width:100%}}
    input,button,.buy-btn,.close-link{{width:100%;min-height:42px;font:inherit;border-radius:7px;border:1px solid rgba(255,255,255,.12);padding:10px 12px;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}}
    input{{background:#000;color:#fff;outline:none}}
    input::placeholder{{color:#8b93a1}}
    input:focus{{border-color:var(--portal-accent,#2600d8)}}
    button,.buy-btn{{background:var(--portal-accent,#2600d8);color:var(--portal-accent-contrast,#fff);border-color:var(--portal-accent,#2600d8);font-weight:700;cursor:pointer;box-shadow:0 12px 22px rgba(0,0,0,.45)}}
    .secondary,.close-link{{background:transparent;border-color:var(--portal-accent,#2600d8);color:#fff;box-shadow:none}}
    .muted{{color:#cbd5e1;font-size:13px}} .price{{font-weight:800;color:#fff}}
    .section-title{{margin:22px 0 12px;font-size:20px;font-weight:750;color:#fff}}
    .alert{{background:#2b1806;border:1px solid #9a5b16;color:#fed7aa;border-radius:8px;padding:12px;margin:12px 0}}
    .buy-btn{{width:auto;min-width:84px;padding-left:22px;padding-right:22px}}
    .pay-modal{{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.72);padding:18px;z-index:20}}
    .pay-modal:target{{display:flex}}
    .pay-box{{width:min(100%,420px);background:#242424;border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:18px;box-shadow:0 24px 60px rgba(0,0,0,.6)}}
    .pay-head{{display:flex;align-items:start;justify-content:space-between;gap:12px;margin-bottom:14px}}
    .pay-head h2{{margin:0;font-size:17px;font-weight:750}}
    .pay-head p{{margin:4px 0 0;color:#cbd5e1;font-size:13px}}
    .close-btn{{width:38px;min-width:38px;min-height:38px;padding:0;background:transparent;border:1px solid rgba(255,255,255,.16);box-shadow:none;font-size:22px;line-height:1;color:#fff;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;border-radius:7px}}
    .modal-actions{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}}
    @media(min-width:600px){{.quick form.row{{grid-template-columns:1fr auto}} .quick form.credentials{{grid-template-columns:1fr 1fr auto}} .quick button{{width:auto;min-width:118px}}}}
    @media(max-width:520px){{header{{width:100%;margin-top:0;border-radius:0 0 18px 18px}}main{{padding-left:14px;padding-right:14px}}.pkg{{padding:16px;gap:10px}}.pkg-title{{font-size:15px}}.pkg-meta{{font-size:13px}}.buy-btn{{min-width:72px;padding-left:16px;padding-right:16px}}.modal-actions{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>{body}</body>
</html>""",
        status=status,
        content_type="text/html",
    )


def _portal_theme_vars(tenant):
    color = str((tenant or {}).get("theme_color") or (tenant or {}).get("dashboard_color") or "#2600d8").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#2600d8"
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    contrast = "#111827" if luminance > 0.62 else "#ffffff"
    return (
        f"--portal-accent:{color};"
        f"--portal-accent-contrast:{contrast};"
        f"--portal-accent-shadow:rgba({red},{green},{blue},.28);"
    )


def _router_is_agent_linked(tenant):
    return bool((tenant or {}).get("mikrotik_last_seen_at") or (tenant or {}).get("mikrotik_router_snapshot") or (tenant or {}).get("mikrotik_provisioning_status") in {"script_downloaded", "completed"})


def _router_connection_status(tenant, live=False):
    if (tenant or {}).get("mikrotik_router_suspended"):
        return "suspended"
    if live:
        return "online"
    last_seen = parse_date((tenant or {}).get("mikrotik_last_seen_at"))
    if last_seen and utcnow() - last_seen <= timedelta(minutes=3):
        return "online"
    return "offline"


def _router_login_form_html(username, password, router_ip="", link_login="", dst="", delay_ms=800):
    router_ip_value = str(router_ip or "").strip()
    login_action_value = str(link_login or "").strip() or (f"http://{router_ip_value}/login" if router_ip_value else "")
    if not login_action_value:
        return ""
    username_value = html.escape(str(username or ""), quote=True)
    password_value = html.escape(str(password or ""), quote=True)
    action_value = html.escape(login_action_value, quote=True)
    dst_value = html.escape(str(dst or "http://connectivitycheck.gstatic.com/generate_204"), quote=True)
    try:
        delay_ms = max(0, int(delay_ms))
    except (TypeError, ValueError):
        delay_ms = 800
    return (
        f"<form id='login' method='post' action='{action_value}'>"
        f"<input type='hidden' name='username' value='{username_value}'>"
        f"<input type='hidden' name='password' value='{password_value}'>"
        f"<input type='hidden' name='dst' value='{dst_value}'>"
        "</form>"
        f"<script>setTimeout(function(){{document.getElementById('login').submit();}}, {delay_ms});</script>"
        "<noscript><button form='login' type='submit'>Connect now</button></noscript>"
    )


def _router_ip_from_captive_data(data, tenant=None, fallback_payment=None):
    """Return the MikroTik login host, never the client's leased hotspot IP."""
    return str(
        data.get("router_ip")
        or data.get("server-address")
        or (fallback_payment or {}).get("router_ip")
        or (tenant or {}).get("mikrotik_hotspot_address")
        or "172.31.0.1"
    ).strip()


def _is_captive_form_request(request, data=None):
    data = data or {}
    content_type = str(request.META.get("CONTENT_TYPE") or "").lower()
    return (
        request.method.upper() == "POST"
        and (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
            or data.get("link_login")
            or data.get("link-login")
            or data.get("router_ip")
            or data.get("ip")
        )
    )


def _limited_router_status_payload(snapshot, assignments=None, source="provisioning_snapshot", message="Showing the latest configuration reported by the router agent.", tenant=None, live=False):
    payload = {**_empty_router_snapshot(), **(snapshot or {})}
    bridge_ports = payload.get("bridge_ports") or []
    addresses = payload.get("addresses") or []
    bridge_by_interface = {
        str(item.get("interface") or item.get("name") or ""): str(item.get("bridge") or "")
        for item in bridge_ports
    }
    address_by_interface = defaultdict(list)
    for item in addresses:
        if item.get("interface") and item.get("address"):
            address_by_interface[str(item.get("interface"))].append(str(item.get("address")))

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
    wan_interface = str((tenant or {}).get("mikrotik_wan_interface") or "ether1")
    for item in interfaces:
        name = str(item.get("name") or "")
        item["bridge"] = bridge_by_interface.get(name, item.get("bridge") or "")
        item["addresses"] = address_by_interface.get(name, [])
        item["customer_assignable"] = name != wan_interface and name.lower() != "ether1"
        if not item["customer_assignable"]:
            item["assignment_warning"] = "WAN/uplink port"
    payload["interfaces"] = interfaces[:6]
    payload["assignments"] = assignments or {}
    payload["source"] = source
    payload["message"] = message
    payload["live"] = bool(live)
    payload["sampled_at"] = iso_now() if live else (payload.get("sampled_at") or "")
    if tenant is not None:
        payload["connection_status"] = _router_connection_status(tenant, live=live)
        payload["last_seen_at"] = (tenant or {}).get("mikrotik_last_seen_at") or ""
        payload["last_seen_ip"] = (tenant or {}).get("mikrotik_last_seen_ip") or ""
    return payload


def _linked_router_from_tenant(tenant):
    snapshot = (tenant or {}).get("mikrotik_router_snapshot") or {}
    device = snapshot.get("device") or {}
    addresses = snapshot.get("addresses") or []
    bridge_name = (tenant or {}).get("mikrotik_bridge_name") or mikrotik_managed_bridge_name(tenant)
    lan_ip = next(
        (
            str(item.get("address") or "").split("/", 1)[0]
            for item in addresses
            if str(item.get("interface") or "") == bridge_name and item.get("address")
        ),
        "",
    )
    wan_interface = (tenant or {}).get("mikrotik_wan_interface") or "ether1"
    wan_ip = next(
        (
            str(item.get("address") or "").split("/", 1)[0]
            for item in addresses
            if str(item.get("interface") or "") == wan_interface and item.get("address")
        ),
        "",
    )
    return {
        "id": "primary",
        "board_name": device.get("board_name") or (tenant or {}).get("mikrotik_detected_board") or "MikroTik Router",
        "identity": (tenant or {}).get("mikrotik_detected_identity") or "",
        "version": device.get("version") or (tenant or {}).get("mikrotik_detected_version") or "",
        "status": _router_connection_status(tenant),
        "provisioning_status": (tenant or {}).get("mikrotik_provisioning_status") or "",
        "last_seen_at": (tenant or {}).get("mikrotik_last_seen_at") or "",
        "last_seen_ip": (tenant or {}).get("mikrotik_last_seen_ip") or "",
        "tunnel_ip": (tenant or {}).get("mikrotik_vpn_tunnel_ip") or (tenant or {}).get("mikrotik_host") or "",
        "wan_ip": wan_ip,
        "lan_ip": lan_ip,
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


def find_payment_by_paystack_reference(reference, tenant_id=None, payment_id=None):
    if not reference and not payment_id:
        return None, None, None
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
            router_ip = _router_ip_from_captive_data(request.GET, tenant, payment)
            link_login = payment.get("link_login") or request.GET.get("link_login") or request.GET.get("link-login") or ""
            dst = payment.get("dst") or request.GET.get("dst") or request.GET.get("link-orig") or "http://connectivitycheck.gstatic.com/generate_204"
            username = payment.get("access_username") or payment.get("username") or ""
            password = payment.get("access_password") or ""
            login_action = link_login or (f"http://{router_ip}/login" if router_ip else "")
            auto_redirect = ""
            if login_action and username and password:
                auto_redirect = (
                    f"<form id='paid-login' method='post' action='{html.escape(str(login_action), quote=True)}'>"
                    f"<input type='hidden' name='username' value='{html.escape(str(username), quote=True)}'>"
                    f"<input type='hidden' name='password' value='{html.escape(str(password), quote=True)}'>"
                    f"<input type='hidden' name='dst' value='{html.escape(str(dst), quote=True)}'>"
                    "</form><script>setTimeout(function(){document.getElementById('paid-login').submit();}, 800);</script>"
                )
            payment_notice = f"""
              <div class="card">
                <strong>Payment successful. Internet access is ready.</strong>
                <p class="muted">Package: {html.escape(str(payment.get('package_name') or ''))}</p>
                <p>Username: <strong>{html.escape(str(username))}</strong></p>
                <p>Password: <strong>{html.escape(str(password))}</strong></p>
                {f"<p><button form='paid-login' type='submit'>Connect now</button></p>" if auto_redirect else ""}
              </div>
              {auto_redirect}
            """
        else:
            payment_notice = "<div class='alert'>Payment is not confirmed yet. If you have paid, wait a moment and refresh this page.</div>"

    packages = sorted(_captive_packages(tenant_id), key=lambda item: float(item.get("price") or 0))
    hidden = "".join(
        f"<input type='hidden' name='{html.escape(key)}' value='{html.escape(str(request.GET.get(key) or ''))}'>"
        for key in ["ip", "mac", "router_ip", "link_login", "link-orig", "dst", "error"]
        if request.GET.get(key)
    )
    selected_payment_method = selected_daraja_method(tenant)
    if packages:
        package_html_v2 = "".join(
            f"""
            <div class="card pkg">
              <div>
                <div class="pkg-title">{html.escape(str(pkg.get('name') or 'Package'))}</div>
                <div class="pkg-meta"><span class="price">Ksh {html.escape(str(pkg.get('price') or 0))}</span> for {html.escape(str(pkg.get('duration_label') or ''))}</div>
                {f"<div class='muted'>{html.escape(str(pkg.get('speed') or ''))}</div>" if pkg.get('speed') else ""}
              </div>
              <a class="buy-btn" href="#pay-{html.escape(str(pkg.get('id')), quote=True)}">Buy</a>
            </div>"""
            for pkg in packages
        )
        payment_modals_v2 = "".join(
            f"""
            <div id="pay-{html.escape(str(pkg.get('id')), quote=True)}" class="pay-modal" aria-hidden="true">
              <form class="pay-box" method="post" action="/api/captive/{html.escape(str(tenant_id))}/pay">
                <div class="pay-head">
                  <div>
                    <h2>{html.escape(str(pkg.get('name') or 'Buy package'))}</h2>
                    <p>Enter your M-Pesa phone number.</p>
                  </div>
                  <a class="close-btn" href="#" aria-label="Close">x</a>
                </div>
                <input type="hidden" name="package_id" value="{html.escape(str(pkg.get('id')))}">
                <input type="hidden" name="service_type" value="{html.escape(str(pkg.get('service_type') or 'hotspot'))}">
                <input type="hidden" name="payment_method" value="{html.escape(selected_payment_method)}">
                {hidden}
                {('<input name="username" required placeholder="PPPoE username">' if pkg.get('service_type') == 'pppoe' else '')}
                <input name="phone" inputmode="tel" required placeholder="M-Pesa/phone number" autocomplete="tel">
                <div class="modal-actions">
                  <a class="secondary close-link" href="#">Cancel</a>
                  <button type="submit">Send prompt</button>
                </div>
              </form>
            </div>"""
            for pkg in packages
        )
    else:
        total_packages = len(list_children(f"tenants/{tenant_id}/packages"))
        package_html_v2 = (
            "<div class='alert'>Packages exist, but none are active. Please contact the provider.</div>"
            if total_packages
            else "<div class='alert'>No packages are configured yet. Please contact the provider.</div>"
        )
        payment_modals_v2 = ""

    link_login_v2 = str(request.GET.get("link_login") or request.GET.get("link-login") or "").strip()
    voucher_autocomplete = ' autocomplete="one-time-code"' if link_login_v2 else ""
    voucher_html_v2 = f"""
      <div class="card quick">
        <strong>Quick access</strong>
        <p class="muted">Use a voucher, M-Pesa code, or your username and password.</p>
        <form class="row" method="post" action="/api/public/{html.escape(str(tenant_id))}/voucher-login">
          {hidden}
          <input name="code" required placeholder="Voucher code"{voucher_autocomplete}>
          <button type="submit">Login</button>
        </form>
        <form class="row" method="post" action="/api/public/{html.escape(str(tenant_id))}/redeem">
          {hidden}
          <input name="receipt_code" required placeholder="M-Pesa code"{voucher_autocomplete}>
          <button class="secondary" type="submit">Connect</button>
        </form>
        <form class="credentials" method="post" action="/api/public/{html.escape(str(tenant_id))}/voucher-login">
          {hidden}
          <input name="username" required placeholder="Username">
          <input name="password" required type="password" placeholder="Password">
          <button class="secondary" type="submit">Sign in</button>
        </form>
      </div>
    """    
    logo_url = html.escape(str(tenant.get("logo_url") or ""), quote=True)
    phone_value = html.escape(str(tenant.get("phone") or tenant.get("support_phone") or "0797443584"), quote=True)
    logo_html = f"<img src='{logo_url}' alt=''>" if logo_url else "WiFi"
    theme_vars = html.escape(_portal_theme_vars(tenant), quote=True)
    body_html_v3 = f"""
      <div style="{theme_vars}">
      <header>
        <div class="hero-logo">{logo_html}</div>
        <h1>{html.escape(str(tenant.get('business_name') or 'Internet packages'))}</h1>
        <div class="steps"><span>Select</span><span class="chev">&gt;</span><span>Pay</span><span class="chev">&gt;</span><span>Connect</span></div>
        <a class="call" href="tel:{phone_value}">Call {phone_value}</a>
      </header>
      <main>
        {payment_notice}
        {voucher_html_v2}
        <div class="section-title">Unlimited packages</div>
        {package_html_v2}
        {payment_modals_v2}
      </main>
      </div>
    """
    response = _html_page(f"{tenant.get('business_name') or 'Hotspot'} packages", body_html_v3)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response

    body_html_v2 = f"""
      <div style="{theme_vars}">
      <header>
        <div class="hero-logo">{logo_html}</div>
        <h1>{html.escape(str(tenant.get('business_name') or 'Internet packages'))}</h1>
        <div class="steps"><span>Select</span><span class="chev">›</span><span>Pay</span><span class="chev">›</span><span>Connect</span></div>
        <a class="call" href="tel:{phone_value}">☎ {phone_value}</a>
      </header>
      <main>
        {payment_notice}
        {voucher_html_v2}
        <div class="section-title">Unlimited packages</div>
        {package_html_v2}
        <div id="pay-modal" class="pay-modal" aria-hidden="true">
          <form id="pay-form" class="pay-box" method="post" action="/api/captive/{html.escape(str(tenant_id))}/pay">
            <div class="pay-head">
              <div>
                <h2 id="pay-title">Buy package</h2>
                <p>Enter your M-Pesa phone number.</p>
              </div>
              <button class="close-btn" type="button" id="pay-close" aria-label="Close">×</button>
            </div>
            <input type="hidden" name="package_id" id="pay-package-id">
            <input type="hidden" name="service_type" id="pay-service-type" value="hotspot">
            <input type="hidden" name="payment_method" value="{html.escape(selected_payment_method)}">
            <span id="pay-hidden-fields"></span>
            <input name="phone" inputmode="tel" required placeholder="M-Pesa/phone number" autocomplete="tel">
            <div class="modal-actions">
              <button class="secondary" type="button" id="pay-cancel">Cancel</button>
              <button type="submit">Send prompt</button>
            </div>
          </form>
        </div>
        <script>
          (function() {{
            var modal = document.getElementById('pay-modal');
            var form = document.getElementById('pay-form');
            var title = document.getElementById('pay-title');
            var packageId = document.getElementById('pay-package-id');
            var serviceType = document.getElementById('pay-service-type');
            var hiddenFields = document.getElementById('pay-hidden-fields');
            var hiddenHtml = {hidden_js};
            function close() {{
              modal.className = 'pay-modal';
              modal.setAttribute('aria-hidden', 'true');
            }}
            function open(button) {{
              packageId.value = button.getAttribute('data-package-id') || '';
              serviceType.value = button.getAttribute('data-service-type') || 'hotspot';
              title.textContent = button.getAttribute('data-package-name') || 'Buy package';
              hiddenFields.innerHTML = hiddenHtml;
              modal.className = 'pay-modal open';
              modal.setAttribute('aria-hidden', 'false');
              setTimeout(function() {{
                var phone = form.querySelector('input[name="phone"]');
                if (phone) phone.focus();
              }}, 40);
            }}
            var buttons = document.querySelectorAll('.buy-btn');
            for (var i = 0; i < buttons.length; i++) {{
              buttons[i].onclick = function() {{ open(this); }};
            }}
            document.getElementById('pay-close').onclick = close;
            document.getElementById('pay-cancel').onclick = close;
            modal.onclick = function(event) {{ if (event.target === modal) close(); }};
          }})();
        </script>
      </main>
      </div>
    """
    response = _html_page(f"{tenant.get('business_name') or 'Hotspot'} packages", body_html_v2)
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response

    if packages:
        selected_payment_method = selected_daraja_method(tenant)
        package_html = "".join(
            f"""
            <form class="card pkg" method="post" action="/api/captive/{html.escape(str(tenant_id))}/pay">
              <input type="hidden" name="package_id" value="{html.escape(str(pkg.get('id')))}">
              <input type="hidden" name="service_type" value="{html.escape(str(pkg.get('service_type') or 'hotspot'))}">
              <input type="hidden" name="payment_method" value="{html.escape(selected_payment_method)}">
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

    link_login = str(request.GET.get("link_login") or request.GET.get("link-login") or "").strip()
    dst_value = str(request.GET.get("dst") or request.GET.get("link-orig") or "").strip()
    if link_login:
        voucher_html = f"""
      <div class="card">
        <strong>Use a voucher</strong>
        <p class="muted">Enter the voucher code provided by your provider.</p>
        <form method="post" action="/api/public/{html.escape(str(tenant_id))}/voucher-login">
          {hidden}
          <input name="code" required placeholder="Voucher code" autocomplete="one-time-code">
          <button type="submit">Login with voucher</button>
        </form>
        <p class="muted">Already bought a package? Sign in with the username and password sent to you.</p>
        <form method="post" action="/api/public/{html.escape(str(tenant_id))}/voucher-login">
          {hidden}
          <div style="display:flex;flex-direction;row; align-items:center;justify-content:center;justify-content:space-around;">
          <input name="username" required placeholder="Username">
          <input name="password" required type="password" placeholder="Password">
          </div>
          <button type="submit">Sign in</button>
        </form>
        <p class="muted">Disconnected after paying? Enter your M-Pesa confirmation code.</p>
        <form method="post" action="/api/public/{html.escape(str(tenant_id))}/redeem">
          {hidden}
          <input name="receipt_code" required placeholder="M-Pesa code e.g. RAB12C3D4E" autocomplete="one-time-code">
          <button type="submit">Sign in with M-Pesa code</button>
        </form>
      </div>
    """
    else:
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
          <div style="display:flex;flex-direction;row; align-items:center;justify-content:center;justify-content:space-around;">
          <input name="username" required placeholder="Username">
          <input name="password" required type="password" placeholder="Password">
          </div>
          <button type="submit">Sign in</button>
        </form>
        <p class="muted">Disconnected after paying? Enter your M-Pesa confirmation code.</p>
        <form method="post" action="/api/public/{html.escape(str(tenant_id))}/redeem">
          {hidden}
          <input name="receipt_code" required placeholder="M-Pesa code e.g. RAB12C3D4E">
          <button type="submit">Sign in with M-Pesa code</button>
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
        "login.html": expressnet_hotspot_file_html("login.html", portal_url) or hotspot_login_redirect_html(portal_url),
        "alogin.html": expressnet_hotspot_file_html("alogin.html", portal_url) or hotspot_alogin_redirect_html(portal_url),
        "redirect.html": expressnet_hotspot_file_html("redirect.html", portal_url) or redirect_html,
        "error.html": expressnet_hotspot_file_html("error.html", portal_url) or hotspot_error_redirect_html(portal_url),
        "status.html": expressnet_hotspot_file_html("status.html", portal_url) or redirect_html,
        "rlogin.html": expressnet_hotspot_file_html("rlogin.html", portal_url) or redirect_html,
        "radvert.html": expressnet_hotspot_file_html("radvert.html", portal_url) or redirect_html,
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
    try:
        response = _public_pay_impl(request, tenant_id)
    except Exception as exc:
        logger.exception("Captive portal payment failed before provider response tenant=%s error=%s", tenant_id, exc)
        return _html_page(
            "Payment unavailable",
            "<main><div class='alert'>M-Pesa payment could not be started. Please confirm the phone number and try again. If it continues, contact the provider.</div></main>",
            503,
        )
    payload = getattr(response, "data", {}) or {}
    if response.status_code >= 400:
        back = f"/api/captive/{html.escape(str(tenant_id))}"
        if data.get("ip"):
            back = f"{back}?ip={html.escape(str(data.get('ip')))}"
        return _html_page("Payment unavailable", f"<main><div class='alert'>{html.escape(str(payload.get('message') or payload.get('error') or 'Could not start payment'))}</div><p><a href='{back}'>Back to packages</a></p></main>", response.status_code)
    authorization_url = payload.get("authorizationUrl")
    if authorization_url:
        return redirect(authorization_url)
    if payload.get("provider") == "mpesa":
        return _html_page(
            "Confirm payment",
            f"<main><div class='card'><strong>{html.escape(str(payload.get('message') or 'Check your phone and enter your M-Pesa PIN to complete payment.'))}</strong><p class='muted'>After payment is confirmed, your access will be activated automatically. If nothing happens after a minute, reconnect to WiFi and open the portal again.</p></div></main>",
            201,
        )
    return _html_page("Payment unavailable", "<main><div class='alert'>Payment checkout was not returned. Please try again.</div></main>", 502)


@csrf_exempt
@api_view(["POST"])
def _public_pay_impl(request, tenant_id):
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
    router_ip = _router_ip_from_captive_data(data, tenant)
    router_mac = str(data.get("mac") or data.get("router_mac") or "").strip()
    link_login = str(data.get("link_login") or data.get("link-login") or "").strip()
    dst = str(data.get("dst") or data.get("link-orig") or "").strip()
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
        if username:
            customer = next(
                (
                    item
                    for item in list_children(f"tenants/{tenant_id}/customers")
                    if str(item.get("username") or "").lower() == username.lower()
                ),
                None,
            )
        phone = normalize_phone(data.get("phone") or (customer or {}).get("phone"))
    elif service_type == "tv":
        mac_address = normalize_mac(data.get("mac_address"))
        if not mac_address:
            return ok({"message": "Enter a valid TV MAC address"}, 400)
    daraja_method = selected_daraja_method(tenant, data.get("payment_method"))
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
            "username": (customer or {}).get("username") or (username if service_type == "pppoe" else None),
            "mac_address": mac_address,
            "router_ip": router_ip,
            "router_mac": router_mac,
            "link_login": link_login,
            "dst": dst,
            "source": "customer_portal",
            "provider": "mpesa",
            "payment_method": daraja_method,
            "collection_account": "platform_daraja",
            "tenant_settlement_status": "pending_payment",
            "tenant_payout": tenant_payout_details(tenant),
        }
    )
    try:
        checkout = initiate_daraja_payment(
            platform_daraja_config(tenant),
            payment_ref.key,
            pkg.get("price"),
            phone=phone,
            description=f"{pkg.get('name')} internet package",
            metadata={
                "package_id": data["package_id"],
                "package_name": pkg.get("name"),
                "service_type": service_type,
                "username": (customer or {}).get("username") or (username if service_type == "pppoe" else None),
                "mac_address": mac_address,
                "router_ip": router_ip,
                "router_mac": router_mac,
                "link_login": link_login,
                "dst": dst,
            },
            payment_method=daraja_method,
        )
        payment_ref.update({"daraja_checkout_request_id": checkout.get("checkout_request_id"), "daraja_merchant_request_id": checkout.get("merchant_request_id"), "checkout_requested_at": iso_now()})
        return ok({
            "success": True,
            "message": checkout.get("customer_message") or "Check your phone and enter your M-Pesa PIN to complete payment.",
            "paymentId": payment_ref.key,
            "provider": "mpesa",
            "checkoutRequestId": checkout.get("checkout_request_id"),
        }, 201)
    except PaymentProviderError as exc:
        logger.warning(
            "Daraja payment provider error for tenant=%s payment=%s detail=%s",
            tenant_id,
            payment_ref.key,
            exc.detail,
        )
        payment_ref.update({"status": "failed", "failed_at": iso_now(), "callback_result_desc": exc.detail})
        return ok({"success": False, "message": exc.public_message, "paymentId": payment_ref.key}, exc.status_code)
    except Exception as exc:
        logger.exception(
            "Unexpected Daraja payment initiation error for tenant=%s payment=%s",
            tenant_id,
            payment_ref.key,
        )
        payment_ref.update({"status": "failed", "failed_at": iso_now(), "callback_result_desc": str(exc)})
        return ok({"success": False, "message": "M-Pesa payment could not be started. Please confirm the phone number and try again. If it continues, contact support.", "paymentId": payment_ref.key}, 503)


@csrf_exempt
@api_view(["POST"])
def public_pay(request, tenant_id):
    """Keep every public checkout failure as a useful API response."""
    try:
        return _public_pay_impl(request, tenant_id)
    except PaymentProviderError as exc:
        return ok({"success": False, "message": exc.public_message}, exc.status_code)
    except Exception:
        return ok({"success": False, "message": "M-Pesa payment could not be started. Please confirm the phone number and try again. If it continues, contact support."}, 503)


@csrf_exempt
@api_view(["POST"])
def public_redeem(request, tenant_id):
    data = body(request)
    receipt_code = data.get("receipt_code") or data.get("payment_code")
    if not receipt_code:
        return ok({"message": "Payment reference is required"}, 400)
    payment = None
    for item in list_children(f"tenants/{tenant_id}/payments"):
        candidates = [item.get("payment_code"), item.get("daraja_receipt_number"), item.get("mpesa_receipt_number"), item.get("paystack_reference")]
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
    payload = {
            "success": True,
            "package_name": payment.get("package_name"),
            "service_type": payment.get("service_type"),
            "phone": payment.get("phone"),
            "username": payment.get("access_username"),
            "password": payment.get("access_password"),
            "mac_address": payment.get("access_mac_address") or payment.get("mac_address"),
            "router_ip": _router_ip_from_captive_data(data, tenant, payment),
            "router_mac": payment.get("router_mac"),
            "link_login": data.get("link_login") or data.get("link-login") or payment.get("link_login"),
            "dst": data.get("dst") or data.get("link-orig") or payment.get("dst"),
            "expires_at": payment.get("access_expires_at"),
        }
    accept_header = str(request.headers.get("Accept") or "")
    wants_html = _is_captive_form_request(request, data) or ("text/html" in accept_header and not request.headers.get("X-Requested-With"))
    if wants_html:
        link_login = payload.get("link_login") or (f"http://{payload.get('router_ip')}/login" if payload.get("router_ip") else "")
        if link_login and payload.get("username") and payload.get("password"):
            return _html_page(
                "Access restored",
                (
                    "<main><div class='card'><strong>Payment found. Connecting...</strong>"
                    f"<p class='muted'>Package: {html.escape(str(payload.get('package_name') or ''))}</p></div>"
                    f"<form id='paid-login' method='post' action='{html.escape(str(link_login), quote=True)}'>"
                    f"<input type='hidden' name='username' value='{html.escape(str(payload.get('username')), quote=True)}'>"
                    f"<input type='hidden' name='password' value='{html.escape(str(payload.get('password')), quote=True)}'>"
                    f"<input type='hidden' name='dst' value='{html.escape(str(payload.get('dst') or 'http://connectivitycheck.gstatic.com/generate_204'), quote=True)}'>"
                    "</form><script>setTimeout(function(){document.getElementById('paid-login').submit();}, 800);</script></main>"
                ),
            )
        return _html_page("Access restored", "<main><div class='card'><strong>Payment found. Your access is active.</strong></div></main>")
    return ok(payload)


@csrf_exempt
@api_view(["POST"])
@renderer_classes([JSONRenderer])
def public_voucher_login(request, tenant_id):
    data = body(request)
    code = str(data.get("code") or "").strip()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "").strip()
    if not code and (not username or not password):
        return ok({"message": "Voucher code is required"}, 400)
    tenant_obj = Tenant.objects.filter(pk=tenant_id).first()
    if not tenant_obj:
        return ok({"message": "Tenant not found"}, 404)
    tenant = {"id": str(tenant_obj.pk), **tenant_obj.as_dict()}
    if code:
        voucher_obj = Voucher.objects.filter(tenant=tenant_obj, code__iexact=code).first()
    else:
        voucher_obj = Voucher.objects.filter(tenant=tenant_obj, username__iexact=username, password=password).first()
    voucher = voucher_obj.as_dict() if voucher_obj else None
    access_payload = None
    package = None
    credential_kind = "voucher"
    credential_label = code or username
    reject_reason = "not_found"
    if voucher and str(voucher.get("status") or "").strip().lower() == "active":
        package_obj = None
        if voucher_obj and voucher_obj.package_id and str(voucher_obj.package_id).isdigit():
            package_obj = InternetPackage.objects.filter(tenant=tenant_obj, pk=voucher_obj.package_id).first()
        if not package_obj and voucher.get("package"):
            package_obj = InternetPackage.objects.filter(tenant=tenant_obj, name=voucher.get("package")).first()
        package = package_obj.as_dict() if package_obj else None
        expires_at = utcnow() + package_duration_delta(package)
        access_payload = {
            "name": voucher.get("username"),
            "phone": data.get("phone") or "",
            "username": voucher.get("username"),
            "password": voucher.get("password"),
            "package": voucher.get("package"),
            "package_name": voucher.get("package"),
            "service_type": "hotspot",
            "status": "active",
            "speed": (package or {}).get("speed"),
            "expiry_date": expires_at.isoformat(),
            "provisioning_status": "radius_ready" if tenant.get("radius_enabled") else "active",
            "provisioning_message": "Voucher validated by captive portal",
        }
    elif not code:
        customer_obj = Customer.objects.filter(tenant=tenant_obj, username__iexact=username).first()
        customer = None
        if customer_obj and str(customer_obj.password or "").strip() == password:
            customer = customer_obj.as_dict()
            customer_service_type = str(customer_obj.service_type or "hotspot").strip().lower()
            customer_status = str(customer_obj.status or "").strip().lower()
            if customer_service_type != "hotspot":
                reject_reason = f"service_type_is_{customer_service_type or 'missing'}"
                customer = None
            elif customer_status != "active":
                reject_reason = f"status_is_{customer_status or 'missing'}"
                customer = None
            else:
                expiry = parse_date(customer.get("expiry_date"))
                now = utcnow()
                if expiry and expiry.tzinfo is None:
                    now = now.replace(tzinfo=None)
                if expiry and expiry < now:
                    reject_reason = "expired"
                    customer = None
        else:
            reject_reason = "password_mismatch" if customer_obj else "customer_not_found"
        if customer:
            package_obj = InternetPackage.objects.filter(tenant=tenant_obj, name=customer.get("package")).first()
            package = package_obj.as_dict() if package_obj else None
            access_payload = {
                "name": customer.get("name"),
                "phone": customer.get("phone") or "",
                "username": customer.get("username"),
                "password": customer.get("password"),
                "package": customer.get("package"),
                "package_name": customer.get("package"),
                "service_type": "hotspot",
                "status": "active",
                "expiry_date": customer.get("expiry_date"),
                "speed": (package or {}).get("speed"),
            }
            credential_kind = "customer"
        else:
            payment_obj = (
                Payment.objects.filter(
                    tenant=tenant_obj,
                    access_password=password,
                    status__iexact="success",
                    service_type__iexact="hotspot",
                )
                .filter(Q(access_username__iexact=username) | Q(extra__username__iexact=username))
                .order_by("-id")
                .first()
            )
            payment = payment_obj.as_dict() if payment_obj else None
            if payment:
                expiry = parse_date(payment.get("access_expires_at"))
                now = utcnow()
                if expiry and expiry.tzinfo is None:
                    now = now.replace(tzinfo=None)
                if expiry and expiry < now:
                    reject_reason = "paid_access_expired"
                    payment = None
            if payment:
                package_obj = InternetPackage.objects.filter(tenant=tenant_obj, name=payment.get("package_name")).first()
                package = package_obj.as_dict() if package_obj else None
                access_payload = {
                    "name": payment.get("customer_name") or payment.get("phone") or username,
                    "phone": payment.get("phone") or "",
                    "username": payment.get("access_username") or payment.get("username"),
                    "password": payment.get("access_password"),
                    "package": payment.get("package_name"),
                    "package_name": payment.get("package_name"),
                    "service_type": "hotspot",
                    "status": "active",
                    "expiry_date": payment.get("access_expires_at"),
                    "speed": (package or {}).get("speed"),
                    "last_payment_id": payment.get("id"),
                    "last_payment_code": payment.get("payment_code") or payment.get("daraja_receipt_number") or payment.get("mpesa_receipt_number"),
                    "provisioning_status": "radius_ready" if tenant.get("radius_enabled") else "active",
                    "provisioning_message": "Paid access recovered from payment record",
                }
                credential_kind = "paid_access"
    if not access_payload:
        logger.info("Captive login rejected tenant=%s kind=%s code=%s username=%s reason=%s", tenant_id, credential_kind, code, username, reject_reason)
        wants_html = _is_captive_form_request(request, data) or ("text/html" in str(request.headers.get("Accept") or "") and not request.headers.get("X-Requested-With"))
        back = f"/api/captive/{html.escape(str(tenant_id))}"
        if code:
            if wants_html:
                return _html_page("Wrong voucher code", f"<main><div class='alert'>Voucher code is wrong, inactive, or expired.</div><p><a href='{back}'>Back to portal</a></p></main>", 401)
            return ok({"message": "Voucher code is wrong, inactive, or expired"}, 401)
        if wants_html:
            return _html_page("Wrong credentials", f"<main><div class='alert'>Username or password is wrong, inactive, or expired.</div><p><a href='{back}'>Back to portal</a></p></main>", 401)
        return ok({"message": "Username or password is wrong, inactive, or expired"}, 401)

    access_payload["duration_seconds"] = int(package_duration_delta(package).total_seconds()) if package else None
    router_status = "radius_ready" if tenant.get("radius_enabled") else "pending"
    if tenant.get("radius_enabled"):
        try:
            from billing_api.radius_provisioning import sync_radius_customer, upsert_pg_customer, upsert_pg_package

            if package:
                upsert_pg_package(tenant_obj, package)
            pg_customer = upsert_pg_customer(tenant_obj, access_payload)
            if pg_customer:
                sync_radius_customer(tenant_obj, pg_customer)
            logger.info(
                "Captive login prepared for RADIUS tenant=%s kind=%s username=%s package=%s expires_at=%s",
                tenant_id,
                credential_kind,
                access_payload.get("username"),
                access_payload.get("package"),
                access_payload.get("expiry_date"),
            )
        except Exception as exc:
            logger.warning("Captive RADIUS sync failed tenant=%s kind=%s credential=%s error=%s", tenant_id, credential_kind, credential_label, exc, exc_info=True)
            return ok({"message": "Credentials were accepted, but authentication could not be prepared. Please contact support."}, 503)
    if has_mikrotik_credentials(tenant):
        try:
            profile_name = str((package or {}).get("name") or access_payload.get("package") or "").strip()
            if profile_name:
                create_hotspot_profile({"id": tenant_id, **tenant}, profile_name, (package or {}).get("speed"), access_payload.get("duration_seconds"))
            upsert_customer_access({"id": tenant_id, **tenant}, access_payload, disabled=False)
            set_customer_enabled({"id": tenant_id, **tenant}, access_payload.get("username"), "hotspot", True)
            router_status = "active"
        except Exception as exc:
            if _is_captive_form_request(request, data) or ("text/html" in str(request.headers.get("Accept") or "") and not request.headers.get("X-Requested-With")):
                return _html_page("Login unavailable", f"<main><div class='alert'>Credentials were accepted, but router access could not be prepared: {html.escape(str(exc))}</div><p><a href='/api/captive/{html.escape(str(tenant_id))}'>Back to portal</a></p></main>", 503)
            return ok({"message": f"Credentials were accepted, but router access could not be prepared: {exc}"}, 503)
    elif _router_is_agent_linked(tenant):
        script = _customer_secret_script(
            {
                **access_payload,
                "router_client_ip": data.get("ip") or "",
                "router_client_mac": data.get("mac") or "",
            }
        )
        if script:
            try:
                _queue_router_command_for_tenant(tenant_id, {"type": "sync_hotspot_login", "script": script}, tenant)
                router_status = "queued"
                if voucher_obj:
                    voucher_obj.router_status = "queued"
                    voucher_obj.router_error = ""
                    voucher_obj.save(update_fields=["router_status", "router_error"])
            except Exception as exc:
                logger.warning("Captive router queue failed tenant=%s kind=%s credential=%s error=%s", tenant_id, credential_kind, credential_label, exc)
    result = {
        "success": True,
        "username": access_payload.get("username"),
        "password": access_payload.get("password"),
        "router_ip": _router_ip_from_captive_data(data, tenant),
        "link_login": data.get("link_login") or data.get("link-login") or "",
        "dst": data.get("dst") or data.get("link-orig") or "http://connectivitycheck.gstatic.com/generate_204",
        "package_name": access_payload.get("package"),
        "credential_type": credential_kind,
        "router_status": router_status,
    }
    # Captive requests are browser form posts from MikroTik. Return a page
    # that submits the actual credentials to the router, rather than JSON.
    # MikroTik submits application/x-www-form-urlencoded and often sends
    # Accept: */*. Do not return JSON for that captive-browser request.
    accept_header = str(request.headers.get("Accept") or "")
    wants_html = (
        _is_captive_form_request(request, data)
        or (
            not request.headers.get("X-Requested-With")
            and (
                "text/html" in accept_header
                or "application/json" not in accept_header
            )
        )
    )
    if wants_html:
        logger.info(
            "Voucher login accepted tenant=%s voucher=%s username=%s mode=%s link_login_present=%s",
            tenant_id,
            (voucher or {}).get("code"),
            result.get("username"),
            "radius" if tenant.get("radius_enabled") else "local_router",
            bool(result.get("link_login")),
        )
        login_form = _router_login_form_html(
            result.get("username"),
            result.get("password"),
            router_ip=result.get("router_ip"),
            link_login=result.get("link_login"),
            dst=result.get("dst"),
            delay_ms=12000 if result.get("router_status") == "queued" else 800,
        )
        if not login_form:
            return _html_page("Voucher accepted", "<main><div class='card'>Voucher accepted. Please open the router login page to connect.</div></main>")
        return _html_page("Connecting", f"<main><div class='card'>Voucher accepted. Connecting you to the internet...</div>{login_form}</main>")
    return ok(result)


def captive_probe(request):
    tenant_id = _probe_tenant_id(request)
    if tenant_id:
        request.GET = request.GET.copy()
        request.GET["probe"] = "1"
        return captive_portal_page(request, tenant_id)
    return HttpResponse("", status=204)




@csrf_exempt
@api_view(["POST"])
@tenant_required
def customer_provision(request, customer_id):
    customer = ref(f"tenants/{request.tenant['id']}/customers/{customer_id}").get()
    if not customer:
        return ok({"message": "Customer not found"}, 404)
    if not has_mikrotik_credentials(request.tenant) and not _router_is_agent_linked(request.tenant):
        return ok({"message": "Link a MikroTik router before provisioning customers"}, 400)
    service_type = customer.get("service_type") or "hotspot"
    pkg = find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", customer.get("package"))
    provisioning_status = "queued"
    provisioning_message = f"{service_type.upper()} access queued for MikroTik sync"
    if has_mikrotik_credentials(request.tenant):
        try:
            if pkg:
                if service_type == "pppoe":
                    create_ppp_profile(request.tenant, pkg["name"], pkg.get("speed"))
                elif service_type == "hotspot":
                    create_hotspot_profile(request.tenant, pkg["name"], pkg.get("speed"))
            upsert_customer_access(request.tenant, {**customer, "package_name": customer.get("package"), "service_type": service_type}, disabled=customer.get("status") != "active")
            provisioning_status = "provisioned"
            provisioning_message = f"{service_type.upper()} access synced on MikroTik"
        except (TimeoutError, OSError):
            _queue_router_command(request, {
                "type": "sync_secrets",
                "customer_ids": [customer_id],
                "script": _customer_secret_script({**customer, "package_name": customer.get("package"), "speed": (pkg or {}).get("speed"), "service_type": service_type}),
            })
    else:
        _queue_router_command(request, {
            "type": "sync_secrets",
            "customer_ids": [customer_id],
            "script": _customer_secret_script({**customer, "package_name": customer.get("package"), "speed": (pkg or {}).get("speed"), "service_type": service_type}),
        })
    ref(f"tenants/{request.tenant['id']}/customers/{customer_id}").update(
        {"provisioning_status": provisioning_status, "service_type": service_type, "auto_reconnect": True, "provisioning_message": provisioning_message, "provisioned_at": iso_now()}
    )
    # Sync to Postgres + RADIUS if tenant has RADIUS enabled --
    if request.tenant.get("radius_enabled"):
        try:
            from billing_api.radius_provisioning import upsert_pg_customer, sync_radius_customer
            from billing_api.models import Tenant as TenantModel
            tenant_obj = TenantModel.objects.get(pk=request.tenant["id"])
            pg_customer = upsert_pg_customer(
                tenant_obj,
                {
                    **customer,
                    "service_type": service_type,
                    "status": customer.get("status") or "active",
                    "provisioning_status": provisioning_status,
                    "provisioning_message": provisioning_message,
                },
            )
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
            "hotspot_profile": "Expressnet-profile",
            "description": "Assign the customer-facing router port as Hotspot. The Expressnet-profile redirects unpaid users to this portal so they can select a package and pay before access is activated.",
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
        existing = ref(f"tenants/{tenant_id}/packages/{package_id}").get()
        if not existing:
            return ok({"message": "Package not found"}, 404)
        updates = {key: data[key] for key in ["name", "speed", "duration_days", "duration_unit", "duration_value", "duration_hours", "price", "is_active", "service_type"] if key in data}
        if not updates:
            return ok({"message": "No package fields provided"}, 400)
        if "service_type" in updates:
            requested_service_type = str(updates["service_type"] or "").strip().lower()
            if requested_service_type not in {"hotspot", "pppoe"}:
                return ok({"message": "Package type must be Hotspot or PPPoE"}, 400)
            updates["service_type"] = requested_service_type
        if "price" in updates:
            updates["price"] = float(updates["price"])
        if "is_active" in updates:
            updates["is_active"] = bool(updates["is_active"])
        if any(key in data for key in ["service_type", "duration_unit", "duration_value", "duration_days", "duration_hours"]):
            updates.update(normalized_package_payload(
                {**existing, **data, **updates},
                package_service_type(existing),
                include_service_type="service_type" in data,
            ))
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
        if request.tenant.get("radius_enabled"):
            try:
                from billing_api.radius_provisioning import upsert_pg_package

                upsert_pg_package(Tenant.objects.get(pk=tenant_id), {"id": package_id, **existing, **updates, **router_updates})
            except Exception:
                logger.warning("RADIUS package mirror failed tenant=%s package=%s", tenant_id, package_id, exc_info=True)
        return ok({"success": True, "message": "Package and MikroTik profile updated"})
    if method(request, "DELETE") and package_id:
        existing = ref(f"tenants/{tenant_id}/packages/{package_id}").get()
        if not existing:
            return ok({"message": "Package not found"}, 404)
        router_error = None
        package_for_router = {"id": package_id, **existing}
        try:
            _delete_package_profile_from_router(request.tenant, package_for_router)
        except Exception as exc:
            router_error = str(exc)
            script = _package_profile_delete_script(package_for_router)
            if script and _router_is_agent_linked(request.tenant):
                try:
                    _queue_router_command(request, {"type": "delete_package_profile", "script": script, "package_ids": [package_id]})
                except Exception as queue_exc:
                    router_error = f"{router_error}; queue failed: {queue_exc}"
        if not has_mikrotik_credentials(request.tenant) and _router_is_agent_linked(request.tenant):
            script = _package_profile_delete_script(package_for_router)
            if script:
                try:
                    _queue_router_command(request, {"type": "delete_package_profile", "script": script, "package_ids": [package_id]})
                except Exception as queue_exc:
                    router_error = str(queue_exc)
        ref(f"tenants/{tenant_id}/packages/{package_id}").delete()
        response = {"success": True, "message": "Package deleted"}
        if router_error:
            response["router_error"] = router_error
        return ok(response)
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
    if request.tenant.get("radius_enabled"):
        try:
            from billing_api.radius_provisioning import upsert_pg_package

            upsert_pg_package(Tenant.objects.get(pk=request.tenant["id"]), {"id": new_ref.key, **data, **package_payload})
        except Exception:
            logger.warning("RADIUS package mirror failed tenant=%s package=%s", request.tenant["id"], new_ref.key, exc_info=True)
    message = "Package and MikroTik profile created" if router_synced else "Package created and queued for MikroTik sync" if router_queued else "Package created. Sync router after MikroTik is connected."
    return ok({"success": True, "message": message, "packageId": new_ref.key}, 201)


@csrf_exempt
@api_view(["GET", "POST", "PATCH", "DELETE"])
@tenant_required
def vouchers(request, voucher_id=None):
    tenant_id = request.tenant["id"]
    voucher_path = f"tenants/{tenant_id}/vouchers"
    if method(request, "GET") and not voucher_id:
        return ok(list_children(voucher_path))
    if method(request, "PATCH") and voucher_id:
        voucher = ref(f"{voucher_path}/{voucher_id}").get()
        if not voucher:
            return ok({"message": "Voucher not found"}, 404)
        data = body(request)
        status = str(data.get("status") or "").strip().lower()
        if status not in {"expired", "inactive"}:
            return ok({"message": "Voucher status must be expired or inactive"}, 400)
        updates = {"status": status, "expired_at": iso_now(), "router_status": "expired"}
        try:
            _disable_voucher_on_router(request, voucher)
            updates.update({"router_error": ""})
        except Exception as exc:
            updates.update({"router_status": "queued", "router_error": str(exc)})
            try:
                _queue_router_command(request, {"type": "expire_voucher", "script": _voucher_disable_script(voucher)})
            except Exception as queue_exc:
                updates["router_error"] = f"{updates['router_error']}; queue failed: {queue_exc}"
        ref(f"{voucher_path}/{voucher_id}").update(updates)
        return ok({"success": True, "message": "Voucher expired", "voucher": {"id": voucher_id, **voucher, **updates}})
    if method(request, "DELETE") and voucher_id:
        voucher_obj = Voucher.objects.filter(tenant_id=tenant_id, pk=voucher_id).first()
        if not voucher_obj:
            return ok({"message": "Voucher not found"}, 404)
        voucher = voucher_obj.as_dict()
        router_error = None
        router_queued = False
        script = _voucher_delete_script(voucher)
        try:
            voucher_obj.delete()
            try:
                from core.services.shared import backup_delete

                backup_delete(f"{voucher_path}/{voucher_id}")
            except Exception:
                logger.warning("Voucher backup delete failed tenant=%s voucher=%s", tenant_id, voucher_id, exc_info=True)
        except Exception as exc:
            logger.exception("Voucher database delete failed tenant=%s voucher=%s", tenant_id, voucher_id)
            return ok({"message": f"Voucher could not be deleted: {exc}"}, 500)
        if _router_is_agent_linked(request.tenant) and script:
            try:
                _queue_router_command(request, {"type": "delete_voucher", "script": script, "voucher_id": voucher_id})
                router_queued = True
            except Exception as queue_exc:
                router_error = str(queue_exc)
        elif has_mikrotik_credentials(request.tenant):
            try:
                _delete_voucher_from_router(request, voucher)
            except Exception as exc:
                router_error = str(exc)
        response = {"success": True, "message": "Voucher deleted"}
        if router_queued:
            response["router_status"] = "queued"
        if router_error:
            response["router_error"] = router_error
            response["message"] = "Voucher deleted, but router cleanup could not be completed automatically."
        return ok(response)
    data = body(request)
    package_id = str(data.get("package_id") or "")
    package = ref(f"tenants/{tenant_id}/packages/{package_id}").get() if package_id else None
    if not package or package_service_type(package) != "hotspot":
        return ok({"message": "Select a valid Hotspot package"}, 400)
    code = secrets.token_hex(4).upper()
    while find_child_by_field(voucher_path, "code", code):
        code = secrets.token_hex(4).upper()
    voucher_username = f"vch-{secrets.token_hex(4).lower()}"
    voucher_password = secrets.token_urlsafe(8)
    voucher = {"code": code, "username": voucher_username, "password": voucher_password, "package": package.get("name"), "package_id": package_id, "price": float(package.get("price") or 0), "status": "active", "service_type": "hotspot", "router_status": "pending", "created_at": iso_now()}
    if not has_mikrotik_credentials(request.tenant) and not _router_is_agent_linked(request.tenant):
        return ok({"message": "Configure MikroTik credentials before creating vouchers"}, 400)
    saved = ref(voucher_path).push(voucher)
    voucher_id = saved.key
    try:
        ensure_hotspot_captive_portal({"id": tenant_id, **request.tenant}, public_base_url(request).rstrip("/"))
        api = router_connect(request.tenant)
        try:
            profile_name = str(package.get("name") or "default")
            try:
                create_hotspot_profile(request.tenant, profile_name, package.get("speed"))
            except Exception:
                # A missing/invalid profile must not prevent the credential
                # itself from reaching the router.
                profile_name = "default"
            users = api.path("ip", "hotspot", "user")
            existing = next((item for item in users.select() if str(item.get("name") or "") == voucher_username), None)
            if existing and existing.get(".id"):
                users.update(**{".id": existing[".id"], "name": voucher_username, "password": voucher_password, "profile": profile_name, "disabled": "no", "comment": f"billing-saas-voucher:{code}"})
            else:
                users.add(name=voucher_username, password=voucher_password, profile=profile_name, disabled="no", comment=f"billing-saas-voucher:{code}")
        finally:
            api.close()
        voucher["router_status"] = "provisioned"
        ref(f"{voucher_path}/{voucher_id}").update({"router_status": voucher["router_status"], "router_synced_at": iso_now(), "router_error": ""})
    except Exception as exc:
        # Live push failed (e.g. WireGuard tunnel temporarily down) — still
        # save the voucher and queue the router-side creation for the
        # agent's next ~30s poll, instead of losing the voucher entirely.
        voucher["router_status"] = "queued"
        voucher["router_error"] = str(exc)
        script = _voucher_hotspot_user_script(voucher, package)
        try:
            _queue_router_command(request, {"type": "sync_voucher", "script": script})
        except Exception as queue_exc:
            voucher["router_error"] = f"{voucher['router_error']}; queue failed: {queue_exc}"
        ref(f"{voucher_path}/{voucher_id}").update({"router_status": voucher["router_status"], "router_error": voucher.get("router_error")})
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
            return ok(_limited_router_status_payload(snapshot, assignments, "agent_report", "Showing the latest configuration reported by the linked MikroTik agent.", request.tenant))
        return ok({"message": "Live MikroTik status requires reachable RouterOS API credentials. Stored provisioning data is not being shown as live status."}, 503)
    try:
        status = router_interface_status(tenant)
        return ok(_limited_router_status_payload(status, assignments, "routeros_api", "Router configuration loaded from the live RouterOS API.", request.tenant, live=True))
    except (TimeoutError, OSError) as exc:
        if _router_is_agent_linked(request.tenant) and snapshot:
            return ok(_limited_router_status_payload(snapshot, assignments, "agent_report", f"Showing the latest configuration reported by the linked MikroTik agent. Direct RouterOS API is unavailable: {exc}", request.tenant))
        return ok({"message": f"Unable to pull live MikroTik status on port {tenant.get('mikrotik_port') or 8728}: {exc}"}, 503)
    except Exception as exc:
        if _router_is_agent_linked(request.tenant) and snapshot:
            return ok(_limited_router_status_payload(snapshot, assignments, "agent_report", f"Showing the latest configuration reported by the linked MikroTik agent. Direct RouterOS API failed: {exc}", request.tenant))
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
    wan_interface = str(request.tenant.get("mikrotik_wan_interface") or "ether1").strip()
    if interface_name == wan_interface or interface_name.lower() == "ether1":
        return ok({"message": f"{interface_name} looks like the WAN/uplink port. Choose a customer LAN port instead."}, 400)
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
            + _build_port_command_script(interface_name, "hotspot", "Expressnet-profile", portal_url, bridge_name)
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
    service_type = customer.get("service_type") or "hotspot"
    if service_type not in {"pppoe", "hotspot"}:
        service_type = "hotspot"
    username = _rsc_escape(customer.get("username") or "")
    password = _rsc_escape(customer.get("password") or "")
    profile = _rsc_escape(customer.get("package") or customer.get("package_name") or "default")
    client_ip = _rsc_escape(customer.get("router_client_ip") or customer.get("client_ip") or customer.get("ip") or "")
    client_mac = _rsc_escape(normalize_mac(customer.get("router_client_mac") or customer.get("mac_address") or customer.get("mac") or ""))
    if not username:
        return ""
    status = str(customer.get("status") or "active").strip().lower()
    if service_type == "pppoe":
        disabled = "yes" if status in {"inactive", "paused", "suspended"} else "no"
    else:
        disabled = "no" if status == "active" else "yes"
    rate_limit = _rsc_escape(normalize_rate_limit(customer.get("speed")) or "")
    rate_limit_field = f' rate-limit="{rate_limit}"' if rate_limit else ""
    ppp_profile_script = (
        f':if ("{profile}" != "default") do={{ '
        f':if ([:len [/ppp profile find name="{profile}"]] = 0) do={{'
        f' /ppp profile add name="{profile}" local-address=172.31.0.1 remote-address=Expressnet-pool{rate_limit_field} }} '
        f'else={{ /ppp profile set [find name="{profile}"] local-address=172.31.0.1 remote-address=Expressnet-pool{rate_limit_field} }}; }};'
        if rate_limit
        else (
            f':if ("{profile}" != "default") do={{ '
            f':if ([:len [/ppp profile find name="{profile}"]] = 0) do={{'
            f' /ppp profile add name="{profile}" local-address=172.31.0.1 remote-address=Expressnet-pool }} '
            f'else={{ /ppp profile set [find name="{profile}"] local-address=172.31.0.1 remote-address=Expressnet-pool }}; }};'
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
        f':if ("{disabled}" = "no" && "{client_ip}" != "") do={{ '
        f':do {{ /ip hotspot active login user="{username}" password="{password}" ip="{client_ip}" mac-address="{client_mac}" }} '
        f'on-error={{ :log warning "Billing SaaS agent: automatic Hotspot login failed for {username}" }}; '
        f'}};'
    )


def _voucher_hotspot_user_script(voucher, package=None):
    profile_name = _rsc_escape((package or {}).get("name") or voucher.get("package") or "default")
    username = _rsc_escape(voucher.get("username") or "")
    password = _rsc_escape(voucher.get("password") or "")
    code = _rsc_escape(voucher.get("code") or "")
    speed = normalize_rate_limit((package or {}).get("speed")) or ""
    rate_limit = f' rate-limit="{_rsc_escape(speed)}"' if speed else ""
    if not username:
        return ""
    return (
        f':do {{ :if ([:len [/ip hotspot user profile find name="{profile_name}"]] = 0) do={{'
        f' /ip hotspot user profile add name="{profile_name}"{rate_limit} }} else={{'
        f' /ip hotspot user profile set [find name="{profile_name}"]{rate_limit} }} }} on-error={{}};'
        f':if ([:len [/ip hotspot user find name="{username}"]] = 0) do={{'
        f' /ip hotspot user add name="{username}" password="{password}" '
        f'profile="{profile_name}" disabled=no comment="billing-saas-voucher:{code}" }} else={{'
        f' /ip hotspot user set [find name="{username}"] password="{password}" profile="{profile_name}" disabled=no comment="billing-saas-voucher:{code}" }};'
    )


def _voucher_disable_script(voucher):
    username = _rsc_escape(voucher.get("username") or "")
    if not username:
        return ""
    return f':do {{ /ip hotspot user set [find name="{username}"] disabled=yes }} on-error={{}};'


def _voucher_delete_script(voucher):
    username = _rsc_escape(voucher.get("username") or "")
    code = _rsc_escape(voucher.get("code") or "")
    if not username:
        return f':do {{ /ip hotspot user remove [find comment="billing-saas-voucher:{code}"] }} on-error={{}};' if code else ""
    return (
        f':do {{ /ip hotspot active remove [find user="{username}"] }} on-error={{}};'
        f':do {{ /ip hotspot user remove [find name="{username}"] }} on-error={{}};'
        f':do {{ /ip hotspot user remove [find comment="billing-saas-voucher:{code}"] }} on-error={{}};'
    )


def _disable_voucher_on_router(request, voucher):
    if not has_mikrotik_credentials(request.tenant):
        raise RuntimeError("Router API credentials are not configured")
    username = str(voucher.get("username") or "").strip()
    if not username:
        return
    api = router_connect(request.tenant)
    try:
        users = api.path("ip", "hotspot", "user")
        existing = next((item for item in users.select() if str(item.get("name") or "") == username), None)
        if existing and existing.get(".id"):
            users.update(**{".id": existing[".id"], "disabled": "yes"})
    finally:
        api.close()


def _delete_voucher_from_router(request, voucher):
    if not has_mikrotik_credentials(request.tenant):
        raise RuntimeError("Router API credentials are not configured")
    username = str(voucher.get("username") or "").strip()
    comment = f"billing-saas-voucher:{voucher.get('code') or ''}".strip()
    if not username and not voucher.get("code"):
        return
    api = router_connect(request.tenant)
    try:
        active = api.path("ip", "hotspot", "active")
        if username:
            for item in list(active.select()):
                if str(item.get("user") or "") == username and item.get(".id"):
                    try:
                        active.remove(item[".id"])
                    except Exception:
                        pass
        users = api.path("ip", "hotspot", "user")
        for item in list(users.select()):
            matches_username = username and str(item.get("name") or "") == username
            matches_comment = voucher.get("code") and str(item.get("comment") or "") == comment
            if (matches_username or matches_comment) and item.get(".id"):
                try:
                    users.remove(item[".id"])
                except Exception:
                    pass
    finally:
        api.close()


def _package_profile_script(package):
    """Generate an .rsc snippet that upserts one PPPoE or Hotspot package profile."""
    name = _rsc_escape(package.get("name") or "")
    if not name:
        return ""
    service_type = package_service_type(package)
    rate_limit = _rsc_escape(normalize_rate_limit(package.get("speed")) or "")
    rate_limit_field = f' rate-limit="{rate_limit}"' if rate_limit else ""
    session_timeout = _rsc_escape(routeros_duration(package_duration_delta(package)) or "")
    session_timeout_field = f' session-timeout="{session_timeout}"' if session_timeout else ""
    if service_type == "pppoe":
        return (
            f':do {{ '
            f':if ([:len [/ppp profile find name="{name}"]] = 0) do={{'
            f' /ppp profile add name="{name}" local-address=172.31.0.1 remote-address=Expressnet-pool{rate_limit_field}{session_timeout_field} comment="billing-saas-package" }} '
            f'else={{ /ppp profile set [find name="{name}"] local-address=172.31.0.1 remote-address=Expressnet-pool{rate_limit_field}{session_timeout_field} comment="billing-saas-package" }}; '
            f'}} on-error={{ :log warning "Billing SaaS agent: PPPoE profile sync failed for {name}"; :error "PPPoE profile sync failed for {name}" }};'
            f':if ([:len [/ppp profile find name="{name}"]] = 0) do={{ :error "PPPoE profile missing after sync: {name}" }};'
        )
    return (
        f':do {{ /ppp profile remove [find name="{name}" comment="billing-saas-package"] }} on-error={{}};'
        f':do {{ '
        f':if ([:len [/ip hotspot user profile find name="{name}"]] = 0) do={{'
        f' /ip hotspot user profile add name="{name}"{rate_limit_field}{session_timeout_field} }} '
        f'else={{ /ip hotspot user profile set [find name="{name}"]{rate_limit_field}{session_timeout_field} }}; '
        f'}} on-error={{ :log warning "Billing SaaS agent: Hotspot profile sync failed for {name}"; :error "Hotspot profile sync failed for {name}" }};'
        f':if ([:len [/ip hotspot user profile find name="{name}"]] = 0) do={{ :error "Hotspot profile missing after sync: {name}" }};'
    )


def _package_profile_delete_script(package):
    """Generate an .rsc snippet that removes the package profile from the right RouterOS store."""
    name = _rsc_escape(package.get("name") or "")
    if not name:
        return ""
    if package_service_type(package) == "pppoe":
        return f':do {{ /ppp profile remove [find name="{name}" comment="billing-saas-package"] }} on-error={{}};'
    return f':do {{ /ip hotspot user profile remove [find name="{name}"] }} on-error={{}};'


def _delete_package_profile_from_router(tenant, package):
    if not has_mikrotik_credentials(tenant):
        return None
    name = str(package.get("name") or "").strip()
    if not name:
        return None
    service_type = package_service_type(package)
    api = router_connect(tenant)
    try:
        path = ("ppp", "profile") if service_type == "pppoe" else ("ip", "hotspot", "user", "profile")
        router_path = api.path(*path)
        for item in list(router_path.select()):
            if str(item.get("name") or "") != name or not item.get(".id"):
                continue
            if service_type == "pppoe" and str(item.get("comment") or "") != "billing-saas-package":
                continue
            router_path.remove(item[".id"])
        return True
    finally:
        api.close()


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
        _queue_router_command(request, {
            "type": "sync_secrets",
            "script": script,
            "customer_ids": [customer.get("id") for customer in customers if customer.get("id")],
        })


def _queue_router_command(request, command_data):
    tenant_id = request.tenant["id"]
    return _queue_router_command_for_tenant(tenant_id, command_data, request.tenant)


def _queue_router_command_for_tenant(tenant_id, command_data, tenant_data=None):
    tenant_data = tenant_data or ref(f"tenants/{tenant_id}").get() or {}
    commands = [c for c in (tenant_data.get("pending_router_commands") or []) if c.get("status") == "pending"][-19:]
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


def _tenant_value(tenant, *keys):
    for key in keys:
        value = (tenant or {}).get(key)
        if value not in (None, ""):
            return str(value).strip()
    extra = (tenant or {}).get("extra") or {}
    if isinstance(extra, dict):
        for key in keys:
            value = extra.get(key)
            if value not in (None, ""):
                return str(value).strip()
    return ""


def _config_value(tenant, env_names, tenant_keys=(), default=""):
    for name in env_names:
        value = os.getenv(name)
        if value not in (None, ""):
            return str(value).strip()
    value = _tenant_value(tenant, *tenant_keys)
    return value if value else default


def _wireguard_server_config(tenant):
    public_key = _config_value(
        tenant,
        ("WG_SERVER_PUBLIC_KEY", "WIREGUARD_SERVER_PUBLIC_KEY", "VPN_SERVER_PUBLIC_KEY"),
        ("wg_server_public_key", "wireguard_server_public_key", "vpn_server_public_key"),
    )
    endpoint = _config_value(
        tenant,
        ("WG_SERVER_ENDPOINT", "WG_SERVER_PUBLIC_IP", "WIREGUARD_SERVER_ENDPOINT", "WIREGUARD_ENDPOINT", "VPN_SERVER_ENDPOINT"),
        ("wg_server_endpoint", "wg_server_public_ip", "wireguard_server_endpoint", "vpn_server_endpoint"),
    )
    port = _config_value(
        tenant,
        ("WG_SERVER_PORT", "WIREGUARD_SERVER_PORT", "WIREGUARD_PORT", "VPN_SERVER_PORT"),
        ("wg_server_port", "wireguard_server_port", "vpn_server_port"),
        "443",
    )
    tunnel_ip = _config_value(
        tenant,
        ("WG_SERVER_TUNNEL_IP", "WIREGUARD_SERVER_TUNNEL_IP", "VPN_SERVER_TUNNEL_IP"),
        ("wg_server_tunnel_ip", "wireguard_server_tunnel_ip", "vpn_server_tunnel_ip"),
    ).split("/")[0]
    missing = []
    if not public_key:
        missing.append("WG_SERVER_PUBLIC_KEY")
    if not endpoint:
        missing.append("WG_SERVER_ENDPOINT")
    if not tunnel_ip:
        missing.append("WG_SERVER_TUNNEL_IP")
    return {
        "public_key": public_key,
        "endpoint": endpoint,
        "port": port,
        "tunnel_ip": tunnel_ip,
        "missing": missing,
        "ready": not missing,
    }


def _radius_server_config(tenant, wg_config=None):
    wg_config = wg_config or _wireguard_server_config(tenant)
    server_ip = _config_value(
        tenant,
        ("RADIUS_SERVER_IP", "RADIUS_SERVER_HOST", "RADIUS_HOST_IP"),
        ("radius_server_ip", "radius_server_host", "radius_host_ip"),
    )
    source_ip = _config_value(
        tenant,
        ("RADIUS_SOURCE_IP", "RADIUS_CLIENT_SOURCE_IP"),
        ("radius_source_ip", "radius_client_source_ip"),
    )
    if not server_ip and wg_config.get("ready"):
        server_ip = wg_config.get("tunnel_ip") or ""
        source_ip = source_ip or str((tenant or {}).get("wg_tunnel_ip") or os.getenv("WG_ROUTER_TUNNEL_IP") or os.getenv("WIREGUARD_ROUTER_TUNNEL_IP") or "10.9.202.70/16").split("/", 1)[0]
    auth_port = _config_value(tenant, ("RADIUS_AUTH_PORT",), ("radius_auth_port",), "1812")
    acct_port = _config_value(tenant, ("RADIUS_ACCT_PORT",), ("radius_acct_port",), "1813")
    return {
        "server_ip": server_ip,
        "source_ip": source_ip,
        "auth_port": auth_port,
        "acct_port": acct_port,
        "ready": bool(server_ip),
        "mode": "wireguard" if wg_config.get("ready") and server_ip == wg_config.get("tunnel_ip") else "direct",
    }

@csrf_exempt
@api_view(["GET"])
@tenant_required
def router_provision_command(request):
    fresh_router = request.GET.get("fresh") == "1"
    expires_at = utcnow() + timedelta(hours=2)
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
        from billing_api.radius_provisioning import backfill_radius_data
        from billing_api.models import Tenant as TenantModel
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
    wg_config = _wireguard_server_config(request.tenant)
    # NOTE: the imported .rsc script (router_provision_script) already performs
    # the full device/interface/profile snapshot AND calls the /complete
    # callback internally. Do not duplicate that work here — doing so doubles
    # the number of sequential HTTPS/TLS handshakes the router has to make,
    # which is enough to exhaust RouterOS's SSL session pool on low-resource
    # hardware (RB9xx-class devices) and surfaces as "SSL: internal error (6)".
    command = (
        f'/tool fetch check-certificate=no url="{script_url}" dst-path=billing-saas.rsc; '
        ':delay 2s; '
        '/import billing-saas.rsc;'
    )
    return ok({
        "command": command,
        "script_url": script_url,
        "script_host": script_host,
        "callback_url": callback_url,
        "vpn_ready": wg_config["ready"],
        "missing_vpn_settings": wg_config["missing"],
        "vpn_message": (
            "WireGuard server configuration is ready."
            if wg_config["ready"]
            else "WireGuard is not configured; Expressnet will provision the router automatically using cloud agent mode."
        ),
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
    def resource_payload(status, source="routeros_api", message="Live MikroTik resource sample."):
        device = status.get("device", {})
        total = float(device.get("total_memory") or 0)
        free = float(device.get("free_memory") or 0)
        traffic = status.get("traffic") or {}
        rx_bps = int(traffic.get("rx_bps") or 0)
        tx_bps = int(traffic.get("tx_bps") or 0)
        wireless_signals = [
            item.get("signal_strength")
            for item in status.get("interfaces", [])
            if item.get("signal_strength") not in {None, ""}
        ]
        numeric_signals = []
        for signal in wireless_signals:
            try:
                numeric_signals.append(int(float(signal)))
            except (TypeError, ValueError):
                pass
        strongest_signal = max(numeric_signals) if numeric_signals else None
        internet_strength = max(0, min(100, round((strongest_signal + 90) / 40 * 100))) if strongest_signal is not None else (100 if source == "routeros_api" else 0)
        active_sessions = status.get("active_sessions") or {}
        active_session_items = active_sessions.get("items") if isinstance(active_sessions, dict) else []
        return {
            "cpu_load_percent": device.get("cpu_load"),
            "uptime": device.get("uptime"),
            "memory_used_bytes": max(0, total - free),
            "memory_total_bytes": total,
            "memory_used_percent": round((1 - free / total) * 100, 1) if total else None,
            "traffic": traffic,
            "network_traffic_bps": rx_bps + tx_bps,
            "network_rx_bps": rx_bps,
            "network_tx_bps": tx_bps,
            "traffic_percent": round(min(((rx_bps + tx_bps) / 1_000_000), 100), 1) if rx_bps or tx_bps else 0,
            "active_sessions": active_sessions,
            "top_active_sessions": active_session_items[:5] if isinstance(active_session_items, list) else [],
            "wireless_signal_strength": strongest_signal,
            "internet_strength_percent": internet_strength,
            "interfaces": [
                {
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "running": item.get("running"),
                    "traffic": item.get("traffic") or {},
                    "signal_strength": item.get("signal_strength"),
                    "wireless": item.get("wireless") or {},
                }
                for item in status.get("interfaces", [])
            ],
            "board_name": device.get("board_name"),
            "version": device.get("version"),
            "source": source,
            "live": source == "routeros_api",
            "sampled_at": iso_now(),
            "message": message,
        }

    try:
        status = router_interface_status(request.tenant)
        return ok(resource_payload(status))
    except (TimeoutError, OSError) as exc:
        snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
        return ok(resource_payload(snapshot, "provisioning_snapshot", f"Live API unreachable, showing last snapshot: {exc}"))
    except Exception as exc:
        snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
        return ok(resource_payload(snapshot, "provisioning_snapshot", f"Live API failed, showing last snapshot: {exc}"))


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
        return HttpResponse(':error "Billing SaaS: provisioning token expired. Generate a fresh MikroTik provisioning command from the dashboard and run it again.";\n', content_type="text/plain")
    except jwt.InvalidTokenError:
        return HttpResponse(':error "Billing SaaS: provisioning token is invalid. Make sure the command was generated by this exact deployed app and JWT_SECRET has not changed.";\n', content_type="text/plain")

    if payload.get("purpose") != "mikrotik_provision":
        return HttpResponse(':error "Billing SaaS: invalid provisioning token purpose.";\n', content_type="text/plain")

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
    portal_wg_hosts = [h for h in walled_garden_hosts(tenant, portal_host) if h]
    captive_hosts_script = "".join(
        f':do {{ /ip hotspot walled-garden add action=allow dst-host="{_rsc_escape(host)}" comment="billing-saas captive portal access" }} on-error={{}}\n'
        for host in portal_wg_hosts
    )
    callback_base_url = f"{app_base_url}/api/router/provision/{token}/complete"
    snapshot_url = f"{app_base_url}/api/router/provision/{token}/snapshot"
    snapshot_interface_url = f"{app_base_url}/api/router/provision/{token}/snapshot/interface"
    snapshot_pppoe_profile_url = f"{app_base_url}/api/router/provision/{token}/snapshot/pppoe-profile"
    snapshot_hotspot_profile_url = f"{app_base_url}/api/router/provision/{token}/snapshot/hotspot-profile"
    snapshot_pppoe_server_url = f"{app_base_url}/api/router/provision/{token}/snapshot/pppoe-server"
    snapshot_hotspot_server_url = f"{app_base_url}/api/router/provision/{token}/snapshot/hotspot-server"

    agent_token = jwt.encode({"purpose": "mikrotik_agent", "tenant_id": tenant_id}, _get_jwt_secret("JWT_SECRET"), algorithm="HS256")
    agent_poll_url = f"{app_base_url}/api/router/agent/{agent_token}/poll"

    hotspot_file_script = _hotspot_captive_file_script(tenant, app_base_url)
    tenant_packages = [pkg for pkg in list_children(f"tenants/{tenant_id}/packages") if pkg.get("name")]
    package_profile_script = "".join(_package_profile_script(pkg) for pkg in tenant_packages)
    package_profile_ids = [str(pkg.get("id")) for pkg in tenant_packages if pkg.get("id")]
    package_by_name = {str(pkg.get("name") or ""): pkg for pkg in tenant_packages}
    tenant_customers = [
        {
            **customer,
            "speed": (package_by_name.get(str(customer.get("package") or "")) or {}).get("speed"),
        }
        for customer in list_children(f"tenants/{tenant_id}/customers")
        if customer.get("username") and str(customer.get("service_type") or "hotspot").lower() in {"pppoe", "hotspot"}
    ]
    customer_secret_script = "".join(_customer_secret_script(customer) for customer in tenant_customers)
    snapshot_script = _router_snapshot_fetch_script(snapshot_url)

    wg_config = _wireguard_server_config(tenant)
    radius_config = _radius_server_config(tenant, wg_config)
    vpn_peer_enabled = bool(wg_config["ready"])
    radius_enabled_for_router = bool(radius_config["ready"])
    wg_server_public_key = wg_config["public_key"]
    wg_server_endpoint = wg_config["endpoint"]
    wg_server_port = wg_config["port"]
    wg_server_tunnel_ip = wg_config["tunnel_ip"]
    wg_router_tunnel_ip = str(tenant.get("wg_tunnel_ip") or os.getenv("WG_ROUTER_TUNNEL_IP") or os.getenv("WIREGUARD_ROUTER_TUNNEL_IP") or "10.9.202.70/16").strip()
    wg_router_api_ip = wg_router_tunnel_ip.split("/", 1)[0]
    wg_router_private_key = str(tenant.get("wg_private_key") or "").strip()
    callback_url = f"{callback_base_url}?vpn={'1' if vpn_peer_enabled else '0'}&vpn_peer={'1' if vpn_peer_enabled else '0'}&radius={'1' if radius_enabled_for_router else '0'}&hotspot=1"
    callback_join = "&" if "?" in callback_url else "?"
    bridge_name = mikrotik_managed_bridge_name(tenant)
    wan_interface = str(os.getenv("MIKROTIK_WAN_INTERFACE") or tenant.get("mikrotik_wan_interface") or "ether1").strip()
    lan_cidr = str(os.getenv("MIKROTIK_LAN_CIDR") or tenant.get("mikrotik_lan_cidr") or "172.31.0.1/16").strip()
    lan_gateway = lan_cidr.split("/", 1)[0]
    lan_network = str(os.getenv("MIKROTIK_LAN_NETWORK") or tenant.get("mikrotik_lan_network") or "172.31.0.0/16").strip()
    dhcp_pool = str(os.getenv("MIKROTIK_DHCP_POOL") or tenant.get("mikrotik_dhcp_pool") or "172.31.0.2-172.31.255.254").strip()
    hotspot_dns_name = str(os.getenv("MIKROTIK_HOTSPOT_DNS_NAME") or tenant.get("mikrotik_hotspot_dns_name") or "hot.spot").strip()
    hotspot_profile_name = "Expressnet-profile"
    hotspot_server_name = "Expressnet-hotspot"
    hotspot_pool_name = "Expressnet-pool"
    ppp_profile_name = "INTERNET"
    pppoe_service_name = "Expressnet-pppoe"
    wireguard_name = "Expressnet-wireguard"
    radius_comment = "Expressnet radius"
    radius_fallback_ip = str(os.getenv("RADIUS_FALLBACK_IP") or tenant.get("radius_fallback_ip") or "142.93.39.55").strip()

    vpn_private_key_set = (
        f':do {{ /interface wireguard set [find name="{wireguard_name}"] private-key="{_rsc_escape(wg_router_private_key)}" }} on-error={{}}\n'
        if wg_router_private_key
        else ""
    )
    vpn_peer_script = (
        f""":do {{ /interface wireguard peers remove [find name="peer1"] }} on-error={{}}
        :do {{ /interface wireguard peers add name=peer1 interface={wireguard_name} public-key="{_rsc_escape(wg_server_public_key)}" endpoint-address="{_rsc_escape(wg_server_endpoint)}" endpoint-port={_rsc_escape(wg_server_port)} allowed-address=0.0.0.0/0 persistent-keepalive=15s }} on-error={{ :log warning "Expressnet: WireGuard peer setup failed"; :error "WireGuard peer setup failed" }}"""
        if vpn_peer_enabled
        else ""
    )
    vpn_script = f"""
        :log info "Billing SaaS: configuring WireGuard VPN";
        :do {{ /interface wireguard add name={wireguard_name} listen-port=19923 mtu=1360 }} on-error={{ /interface wireguard set [find name={wireguard_name}] listen-port=19923 mtu=1360 }}
        {vpn_private_key_set}
        :do {{ /ip address remove [find interface={wireguard_name}] }} on-error={{}}
        :do {{ /ip address add address={_rsc_escape(wg_router_tunnel_ip)} interface={wireguard_name} }} on-error={{ :log warning "Expressnet: WireGuard address setup failed"; :error "WireGuard address setup failed" }}
        {vpn_peer_script}
        :do {{ /ip firewall filter remove [find comment="Expressnet WireGuard hub"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept src-address={_rsc_escape(wg_server_tunnel_ip)} comment="Expressnet WireGuard hub" }} on-error={{}}
        :do {{ /ip firewall filter add chain=output action=accept dst-address={_rsc_escape(wg_server_tunnel_ip)} comment="Expressnet WireGuard hub" }} on-error={{}}
        """
    if not vpn_peer_enabled:
        missing = ", ".join(wg_config["missing"] or ["server VPN settings"])
        logger.warning("MikroTik provisioning tenant=%s WireGuard unavailable because %s is missing; radius_ready=%s", tenant_id, missing, radius_enabled_for_router)
        vpn_script = (
            ':log info "Billing SaaS: using automatic local agent mode";\n'
            f':do {{ /interface wireguard remove [find name="{wireguard_name}"] }} on-error={{}}\n'
            ':do { /ip firewall filter remove [find comment="billing-saas allow api over vpn"] } on-error={}\n'
            ':do { /ip firewall filter remove [find comment="billing-saas allow api over vpn only"] } on-error={}\n'
            ':do { /ip firewall filter remove [find comment="billing-saas allow radius"] } on-error={}\n'
        )

    # --- Extracted from field export: /system scheduler Expressnet-wg-watchdog ---
    # Bounces the WireGuard interface if the VPN server stops responding to ping,
    # so a dropped tunnel self-heals instead of silently orphaning RADIUS/API access.
    watchdog_script = (
        f"""
        :do {{ /system scheduler remove [find name="Expressnet-wg-watchdog"] }} on-error={{}}
        /system scheduler add name="Expressnet-wg-watchdog" interval=1m comment="Expressnet WireGuard watchdog. Do not delete." on-event=":if ([/ping {_rsc_escape(wg_server_tunnel_ip)} count=10 interval=1s]=0) do={{/interface wireguard disable [find name=\\"{wireguard_name}\\"];:delay 2s;/interface wireguard enable [find name=\\"{wireguard_name}\\"];:log info \\"Expressnet-wg-watchdog: bounced unreachable tunnel\\"}}"
        """
        if vpn_peer_enabled
        else (
            ':do { /system scheduler remove [find name="Expressnet-wg-watchdog"] } on-error={}\n'
            ':log info "Billing SaaS: WireGuard watchdog skipped, agent mode active";'
        )
    )

    radius_shared_secret = ""
    if radius_enabled_for_router:
        from billing_api.models import RadiusNasClient
        tenant_obj = Tenant.objects.get(pk=tenant_id)
        existing_extra = tenant_obj.extra or {}
        radius_shared_secret = existing_extra.get("radius_shared_secret_pending") or RadiusNasClient.generate_secret()
        tenant_obj.extra = {**existing_extra, "radius_shared_secret_pending": radius_shared_secret}
        tenant_obj.save(update_fields=["extra"])

    radius_script = f"""
        :log info "Expressnet: configuring RADIUS client for Hotspot and PPPoE";
        :do {{ /radius remove [find comment="{radius_comment}"] }} on-error={{}}
        :do {{ /radius add service=ppp,hotspot address={_rsc_escape(radius_config["server_ip"])} secret="{_rsc_escape(radius_shared_secret)}" timeout=3s comment="{radius_comment}" }} on-error={{ :log warning "Expressnet: primary RADIUS setup failed"; :error "Primary RADIUS setup failed" }}
        :do {{ /radius add service=ppp,hotspot address={_rsc_escape(radius_fallback_ip)} secret="{_rsc_escape(radius_shared_secret)}" realm={_rsc_escape(wg_router_api_ip)} timeout=3s comment="{radius_comment}" }} on-error={{ :log warning "Expressnet: fallback RADIUS setup failed" }}
        :if ([:len [/radius find comment="{radius_comment}"]] = 0) do={{ :log error "Expressnet: RADIUS client missing after configuration"; :error "RADIUS client missing after configuration" }}
        :do {{ /radius incoming set accept=yes port=1700 }} on-error={{ :log warning "Expressnet: RADIUS incoming setup failed" }}
        /ppp aaa set use-radius=yes interim-update=1h;
        :log info "Expressnet: RADIUS client configured";
        """
    if not radius_enabled_for_router:
        radius_script = """
        :log info "Billing SaaS: RADIUS server IP unavailable; using local Hotspot users synced by cloud agent";
        :do { /radius remove [find comment="Expressnet radius"] } on-error={}
        /ppp aaa set use-radius=no accounting=no;
        :do { /ip hotspot profile set [find name="Expressnet-profile"] use-radius=no } on-error={}
        :do { /ip firewall filter remove [find comment="billing-saas allow radius"] } on-error={}
        """
    hotspot_radius_flags = "yes" if radius_enabled_for_router else "no"
    api_vpn_firewall_script = (
        f':do {{ /ip firewall filter remove [find comment="Expressnet allow api over vpn only"] }} on-error={{}}\n'
        f':do {{ /ip firewall filter add chain=input action=accept in-interface={wireguard_name} protocol=tcp dst-port=8728 comment="Expressnet allow api over vpn only" }} on-error={{}}\n'
        if vpn_peer_enabled
        else ':do { /ip firewall filter remove [find comment="Expressnet allow api over vpn only"] } on-error={}\n'
    )

    # --- Extracted from field export: /ip service set api address=10.9.0.1/32 ---
    # Plus /tool mac-server and /tool mac-server mac-winbox restricted to LAN list only.
    # Locks the router's management plane so Winbox/MAC-Telnet discovery and the RouterOS
    # API can't be reached from the open hotspot bridge — only from LAN or the VPN tunnel.
    mgmt_lockdown_script = f"""
        :log info "Billing SaaS: locking down management plane";
        :do {{ /ip service set api address={_rsc_escape(wg_server_tunnel_ip + '/32') if vpn_peer_enabled else '0.0.0.0/0'} }} on-error={{ :log warning "Billing SaaS: api service lockdown failed" }}
        :do {{ /ip service set api-ssl disabled=yes }} on-error={{}}
        :do {{ /ip service set telnet disabled=yes }} on-error={{}}
        :do {{ /ip service set ftp disabled=yes }} on-error={{}}
        :do {{ /tool mac-server set allowed-interface-list=LAN }} on-error={{ :log warning "Billing SaaS: mac-server lockdown failed" }}
        :do {{ /tool mac-server mac-winbox set allowed-interface-list=LAN }} on-error={{ :log warning "Billing SaaS: mac-winbox lockdown failed" }}
        """

    provisioning_callback_script = f""":local billingWgPub "";
        :do {{ :set billingWgPub [/interface wireguard get [find name={wireguard_name}] public-key] }} on-error={{}}
        :do {{ /tool fetch keep-result=no url=("{callback_url}{callback_join}wg_public_key=" . $billingWgPub . "&wg_tunnel_ip={_rsc_escape(wg_router_api_ip)}&bridge={_rsc_escape(bridge_name)}") }} on-error={{ :log warning "Billing SaaS provisioning callback failed" }}"""
    if not vpn_peer_enabled:
        provisioning_callback_script = f':do {{ /tool fetch keep-result=no url=("{callback_url}{callback_join}bridge={_rsc_escape(bridge_name)}&mode=agent") }} on-error={{ :log warning "Billing SaaS provisioning callback failed" }}'
    vpn_health_script = f"""
        :log info "Billing SaaS Step 5: verifying VPN connectivity";
        :local billingVpnPing 0;
        :do {{ :set billingVpnPing [/ping {_rsc_escape(wg_server_tunnel_ip)} count=3 interval=1s] }} on-error={{ :log warning "Billing SaaS: VPN ping failed" }}
        :if ($billingVpnPing = 0) do={{ :log warning "Billing SaaS: VPN server did not respond to ping; RADIUS may be unreachable until the tunnel is up" }} else={{ :log info ("Billing SaaS: VPN ping replies=" . $billingVpnPing) }}
        :local billingPeerId [/interface wireguard peers find name=peer1];
        :if ([:len $billingPeerId] > 0) do={{ :log info ("Billing SaaS: WireGuard peer configured, last-handshake=" . [/interface wireguard peers get $billingPeerId last-handshake]) }} else={{ :log warning "Billing SaaS: WireGuard peer not found after setup" }}
    """
    if not vpn_peer_enabled:
        vpn_health_script = ':log info "Billing SaaS Step 5: VPN health check skipped; local agent mode is active";'
    verification_script = """
        :log info ("Expressnet verify: /radius count=" . [:len [/radius find comment="Expressnet radius"]]);
        :if ([:len [/radius find comment="Expressnet radius"]] = 0) do={ :log error "Expressnet verify failed: /radius print has no Expressnet radius client"; :error "RADIUS client missing" }
        :log info ("Expressnet verify: hotspot profile use-radius=" . [/ip hotspot profile get [find name=Expressnet-profile] use-radius]);
        :if ([/ip hotspot profile get [find name=Expressnet-profile] use-radius] != "yes") do={ :log warning "Expressnet verify warning: /ip hotspot profile use-radius is not yes; continuing with local synced Hotspot users until RADIUS is confirmed" }
        :log info ("Billing SaaS verify: /ip hotspot host count=" . [:len [/ip hotspot host find]]);
        :log info ("Billing SaaS verify: /ip hotspot active count=" . [:len [/ip hotspot active find]]);
        :log info ("Billing SaaS verify: PPP profiles=" . [:len [/ppp profile find]] . " Hotspot user profiles=" . [:len [/ip hotspot user profile find]]);
        :foreach p in=[/ppp profile find comment="billing-saas-package"] do={
            :local pn [/ppp profile get $p name];
            :if ([:len [/ip hotspot user profile find name=$pn comment="billing-saas-package"]] > 0) do={ :log warning ("Billing SaaS verify: package name exists in both PPP and Hotspot profile stores: " . $pn) }
        }
    """
    if not vpn_peer_enabled:
        verification_script = """
        :log info "Billing SaaS verify: local agent mode active";
        :log info ("Expressnet verify: hotspot profile use-radius=" . [/ip hotspot profile get [find name=Expressnet-profile] use-radius]);
        :log info ("Billing SaaS verify: /ip hotspot host count=" . [:len [/ip hotspot host find]]);
        :log info ("Billing SaaS verify: /ip hotspot active count=" . [:len [/ip hotspot active find]]);
        """
    radius_verify_script = (
        ':if ([:len [/ip hotspot profile find name=Expressnet-profile use-radius=yes]] = 0) do={ :log warning "Expressnet: hotspot profile use-radius=yes was not confirmed; local synced users may be used until RADIUS is reachable" } else={ :log info "Expressnet: hotspot profile use-radius=yes confirmed" }'
        if radius_enabled_for_router
        else ':log info "Billing SaaS: local Hotspot profile does not use RADIUS because local agent mode is active";'
    )
    hotspot_server_script = f"""
        :log info "Billing SaaS: binding Hotspot server to billing bridge";
        :local billingHotspotServer [/ip hotspot find interface=$billingBridge];
        :if ([:len $billingHotspotServer] = 0) do={{ :set billingHotspotServer [/ip hotspot find name="{hotspot_server_name}"] }}
        :if ([:len $billingHotspotServer] > 0) do={{
            /ip hotspot set $billingHotspotServer name="{hotspot_server_name}" interface=$billingBridge address-pool={hotspot_pool_name} profile={hotspot_profile_name} disabled=no
        }} else={{
            /ip hotspot add name="{hotspot_server_name}" interface=$billingBridge address-pool={hotspot_pool_name} profile={hotspot_profile_name} disabled=no
        }}
        :foreach hs in=[/ip hotspot find name="{hotspot_server_name}"] do={{ :if ([/ip hotspot get $hs interface] != $billingBridge) do={{ /ip hotspot disable $hs }} }}
        :if ([:len [/ip hotspot find interface=$billingBridge disabled=no]] = 0) do={{ :log error "Billing SaaS: Hotspot server is not active on billing bridge"; :error "Hotspot server missing on billing bridge" }}
        """

    try:
        ref(f"tenants/{tenant_id}").update({
            "mikrotik_provisioning_status": "script_downloaded",
            "mikrotik_script_downloaded_at": iso_now(),
            "mikrotik_vpn_enabled": vpn_peer_enabled,
            "mikrotik_vpn_peer_enabled": vpn_peer_enabled,
            "mikrotik_vpn_status": "configured" if vpn_peer_enabled else "agent_mode",
            "mikrotik_vpn_peer_status": "configured" if vpn_peer_enabled else "agent_mode",
            "radius_enabled": radius_enabled_for_router,
            "radius_nas_configured": radius_enabled_for_router,
            "mikrotik_vpn_tunnel_ip": wg_router_tunnel_ip if vpn_peer_enabled else "",
            "mikrotik_host": wg_router_api_ip if vpn_peer_enabled else tenant.get("mikrotik_host", ""),
            "mikrotik_port": int(tenant.get("mikrotik_port") or 8728),
            "mikrotik_bridge_name": bridge_name,
            "mikrotik_wan_interface": wan_interface,
        })
        for package_id_value in package_profile_ids:
            ref(f"tenants/{tenant_id}/packages/{package_id_value}").update({
                "ppp_profile_status": "queued",
                "ppp_profile_queued_at": iso_now(),
                "ppp_profile_error": None,
            })
    except OperationalError:
        close_old_connections()
        try:
            ref(f"tenants/{tenant_id}").update({
                "mikrotik_provisioning_status": "script_downloaded",
                "mikrotik_script_downloaded_at": iso_now(),
                "mikrotik_vpn_enabled": vpn_peer_enabled,
                "mikrotik_vpn_peer_enabled": vpn_peer_enabled,
                "mikrotik_vpn_status": "configured" if vpn_peer_enabled else "agent_mode",
                "mikrotik_vpn_peer_status": "configured" if vpn_peer_enabled else "agent_mode",
                "radius_enabled": radius_enabled_for_router,
                "radius_nas_configured": radius_enabled_for_router,
                "mikrotik_vpn_tunnel_ip": wg_router_tunnel_ip if vpn_peer_enabled else "",
                "mikrotik_host": wg_router_api_ip if vpn_peer_enabled else tenant.get("mikrotik_host", ""),
                "mikrotik_port": int(tenant.get("mikrotik_port") or 8728),
                "mikrotik_bridge_name": bridge_name,
                "mikrotik_wan_interface": wan_interface,
            })
            for package_id_value in package_profile_ids:
                ref(f"tenants/{tenant_id}/packages/{package_id_value}").update({
                    "ppp_profile_status": "queued",
                    "ppp_profile_queued_at": iso_now(),
                    "ppp_profile_error": None,
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
        :foreach p in=[/ip hotspot user profile find] do={{
            :local pn [/ip hotspot user profile get $p name];
            :local pr "";
            :do {{ :set pr [/ip hotspot user profile get $p rate-limit] }} on-error={{}}
            :do {{ /tool fetch keep-result=no url=("{snapshot_hotspot_profile_url}?name=" . $pn . "&rate_limit=" . $pr) }} on-error={{}}
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

    pppoe_script = f"""
        :log info "Billing SaaS Step 8: provisioning PPPoE service without mixing Hotspot profiles";
        :do {{ /ppp profile add name="{ppp_profile_name}" local-address={_rsc_escape(lan_gateway)} remote-address={hotspot_pool_name} dns-server=8.8.8.8 only-one=yes comment="Added by Expressnet" }} on-error={{ /ppp profile set [find name="{ppp_profile_name}"] local-address={_rsc_escape(lan_gateway)} remote-address={hotspot_pool_name} dns-server=8.8.8.8 only-one=yes comment="Added by Expressnet" }}
        :do {{ /interface pppoe-server server add service-name="{pppoe_service_name}" authentication=pap,chap,mschap1,mschap2 interface=$billingBridge default-profile="{ppp_profile_name}" one-session-per-host=yes disabled=no }} on-error={{ /interface pppoe-server server set [find service-name="{pppoe_service_name}"] authentication=pap,chap,mschap1,mschap2 interface=$billingBridge default-profile="{ppp_profile_name}" one-session-per-host=yes disabled=no }}
    """

    # --- Extracted from field export: /ip firewall address-list + /ip hotspot walled-garden ip
    # (Expressnet-portal-ips list of FQDNs, matched by dst-address-list rather than a single
    # one-time :resolve'd IP). RouterOS re-resolves FQDN address-list entries on its own, so the
    # walled garden survives DNS/IP changes on the portal host without re-provisioning.
    walled_garden_fqdn_script = "".join(
        f':do {{ /ip firewall address-list add address="{_rsc_escape(h)}" list=Expressnet-portal-ips comment="Expressnet-hotspot-ip-wg" }} on-error={{}}\n'
        for h in portal_wg_hosts
    ) + """
        :do { /ip hotspot walled-garden ip remove [find comment="billing-saas captive portal access"] } on-error={}
        :do { /ip hotspot walled-garden ip add action=accept dst-address-list=Expressnet-portal-ips dst-port=80 protocol=tcp comment=Expressnet-hotspot-ip-wg } on-error={}
        :do { /ip hotspot walled-garden ip add action=accept dst-address-list=Expressnet-portal-ips dst-port=443 protocol=tcp comment=Expressnet-hotspot-ip-wg } on-error={}
        """

    # --- Extracted from field export: /ip firewall raw hotspot-proxy-lockdown + dns-flood-cap ---
    # Blocks direct client access to the hotspot's internal proxy port range and rate-limits
    # DNS queries per-client (100 packets, burst 200, per source IP per minute) before dropping
    # the rest, so a single compromised customer device can't flood the router's DNS/proxy.
    hotspot_abuse_protection_script = """
        :do { /ip firewall raw remove [find comment~"Expressnet-hotspot"] } on-error={}
        :do { /ip firewall raw add action=drop chain=prerouting dst-port=64872-64875 in-interface=$billingBridge protocol=tcp comment=Expressnet-hotspot-proxy-lockdown } on-error={}
        :do { /ip firewall raw add action=accept chain=prerouting dst-limit=100,200,src-address/1m dst-port=53 in-interface=$billingBridge protocol=udp comment=Expressnet-hotspot-dns-flood-cap } on-error={}
        :do { /ip firewall raw add action=drop chain=prerouting dst-port=53 in-interface=$billingBridge protocol=udp comment=Expressnet-hotspot-dns-flood-cap } on-error={}
        :do { /ip firewall raw add action=accept chain=prerouting dst-limit=100,200,src-address/1m dst-port=53 in-interface=$billingBridge protocol=tcp comment=Expressnet-hotspot-dns-flood-cap } on-error={}
        :do { /ip firewall raw add action=drop chain=prerouting dst-port=53 in-interface=$billingBridge protocol=tcp comment=Expressnet-hotspot-dns-flood-cap } on-error={}
        """

    script = f""":log info "Billing SaaS Step 1: provisioning token validated by Expressnet server";
        :local billingVer [/system resource get version];
        :local billingVerNum $billingVer;
        :local billingSpaceIdx [:find $billingVer " "];
        :if ([:len $billingSpaceIdx] > 0) do={{ :set billingVerNum [:pick $billingVer 0 $billingSpaceIdx] }}
        :local billingDot1 [:find $billingVerNum "."];
        :local billingMajor 0;
        :if ([:len $billingDot1] > 0) do={{ :set billingMajor [:tonum [:pick $billingVerNum 0 $billingDot1]] }} else={{ :set billingMajor [:tonum $billingVerNum] }}
        :if ($billingMajor < 7) do={{
            :log warning ("Billing SaaS: RouterOS " . $billingVer . " detected; WireGuard VPN will be skipped if unsupported.");
        }}
        :log info "Billing SaaS Step 2: creating bridge, DHCP, IP pool, firewall, and NAT";
        :local billingBridge "{_rsc_escape(bridge_name)}";
        :do {{ /interface bridge add name=$billingBridge comment="Created by Expressnet" }} on-error={{ /interface bridge set [find name=$billingBridge] comment="Created by Expressnet" }}
        :do {{ /interface list member add list=LAN interface=$billingBridge comment="Added by Expressnet" }} on-error={{ /interface list member set [find interface=$billingBridge] list=LAN comment="Added by Expressnet" }}
        :do {{ /ip address add address={_rsc_escape(lan_cidr)} interface=$billingBridge comment="Added by Expressnet" }} on-error={{ /ip address set [find comment="Added by Expressnet" interface=$billingBridge] address={_rsc_escape(lan_cidr)} interface=$billingBridge }}
        :do {{ /ip pool add name={hotspot_pool_name} ranges={_rsc_escape(dhcp_pool)} comment="IP Pool created by Expressnet" }} on-error={{ /ip pool set [find name={hotspot_pool_name}] ranges={_rsc_escape(dhcp_pool)} comment="IP Pool created by Expressnet" }}
        :do {{ /ip dhcp-server add name=Expressnet-dhcp interface=$billingBridge address-pool={hotspot_pool_name} disabled=no lease-time=4h }} on-error={{ /ip dhcp-server set [find name=Expressnet-dhcp] interface=$billingBridge address-pool={hotspot_pool_name} disabled=no lease-time=4h }}
        :do {{ /ip dhcp-server network add address={_rsc_escape(lan_network)} gateway={_rsc_escape(lan_gateway)} dns-server=8.8.8.8,8.8.4.4 }} on-error={{ /ip dhcp-server network set [find address={_rsc_escape(lan_network)}] gateway={_rsc_escape(lan_gateway)} dns-server=8.8.8.8,8.8.4.4 }}
        :log info "Billing SaaS: configuring only Expressnet-managed firewall and NAT rules";
        /ip service enable api;
        :do {{ /ip service set api disabled=no }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow established"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept connection-state=established,related comment="billing-saas allow established" }} on-error={{}}
        {api_vpn_firewall_script}
        :do {{ /ip firewall nat remove [find comment="billing-saas masquerade"] }} on-error={{}}
        :do {{ /ip firewall nat add chain=srcnat action=masquerade comment="billing-saas masquerade" }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow hotspot dns"] }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow hotspot dhcp"] }} on-error={{}}
        :do {{ /ip firewall filter remove [find comment="billing-saas allow hotspot web-proxy"] }} on-error={{}}
        :do {{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=udp dst-port=53 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot dns" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=udp dst-port=53 comment="billing-saas allow hotspot dns" }}
        :do {{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=tcp dst-port=53 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot dns" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=tcp dst-port=53 comment="billing-saas allow hotspot dns" }}
        :do {{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=udp dst-port=67,68 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot dhcp" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=udp dst-port=67,68 comment="billing-saas allow hotspot dhcp" }}
        :do {{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=tcp dst-port=64872-64875 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot web-proxy" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface=$billingBridge protocol=tcp dst-port=64872-64875 comment="billing-saas allow hotspot web-proxy" }}
        {hotspot_abuse_protection_script}
        :do {{ /ip dns static remove [find comment="billing-saas hotspot dns"] }} on-error={{}}
        :do {{ /system ntp client set enabled=yes servers=pool.ntp.org }} on-error={{}}
        :do {{ /system clock set time-zone-name="Africa/Nairobi" }} on-error={{}}
        :log info "Billing SaaS: preparing WAN internet";
        :do {{ /ip dhcp-client add interface="{_rsc_escape(wan_interface)}" add-default-route=yes use-peer-dns=no disabled=no comment="billing-saas wan" }} on-error={{ /ip dhcp-client set [find interface="{_rsc_escape(wan_interface)}"] add-default-route=yes use-peer-dns=no disabled=no comment="billing-saas wan" }}
        :do {{ /ip dns set servers=8.8.8.8,8.8.4.4 allow-remote-requests=yes }} on-error={{}}
        :delay 3s;
        :log info "Billing SaaS: preserving existing PPP secrets and Hotspot users";
        :log info "Billing SaaS Step 3: preparing Hotspot profile and captive portal files without assigning customer ports";
        :do {{ /ip hotspot profile add name={hotspot_profile_name} hotspot-address={_rsc_escape(lan_gateway)} dns-name="{_rsc_escape(hotspot_dns_name)}" login-by=cookie,http-pap,trial,mac-cookie use-radius={hotspot_radius_flags} radius-interim-update=10m html-directory=Expressnet-hotspot }} on-error={{ /ip hotspot profile set [find name={hotspot_profile_name}] hotspot-address={_rsc_escape(lan_gateway)} dns-name="{_rsc_escape(hotspot_dns_name)}" login-by=cookie,http-pap,trial,mac-cookie use-radius={hotspot_radius_flags} radius-interim-update=10m html-directory=Expressnet-hotspot }}
        :do {{ /ip hotspot user profile set [find default=yes] idle-timeout=2h shared-users=2 }} on-error={{}}
        :do {{ /ip hotspot walled-garden remove [find comment="billing-saas captive portal access"] }} on-error={{}}
        :do {{ /ip hotspot walled-garden ip remove [find comment="billing-saas captive portal access"] }} on-error={{}}
        :do {{ /ip firewall address-list remove [find list=Expressnet-portal-ips] }} on-error={{}}
        {captive_hosts_script}
        {walled_garden_fqdn_script}
        {hotspot_file_script}
        {hotspot_server_script}
        :log info "Billing SaaS Step 4: configuring WireGuard and router tunnel keys";
        {vpn_script}
        {watchdog_script}
        {vpn_health_script}
        :log info "Billing SaaS Step 6: configuring RADIUS client";
        {radius_script}
        :log info "Billing SaaS Step 7: enabling and verifying Hotspot RADIUS authentication";
        :foreach hp in=[/ip hotspot profile find name={hotspot_profile_name}] do={{ :do {{ /ip hotspot profile set $hp use-radius={hotspot_radius_flags} radius-accounting={hotspot_radius_flags} }} on-error={{ :log warning "Expressnet: hotspot radius flag update failed" }} }}
        {radius_verify_script}
        :foreach hs in=[/ip hotspot find interface=$billingBridge] do={{ :do {{ /ip hotspot set $hs profile={hotspot_profile_name} disabled=no }} on-error={{ :log warning "Expressnet: hotspot server radius profile update failed" }} }}
        :log info "Billing SaaS: Hotspot server is active on the billing bridge.";
        :log info "Billing SaaS Step 7b: locking down management plane";
        {mgmt_lockdown_script}
        :log info "Billing SaaS Step 8: syncing package profiles by service type";
        {package_profile_script or ':log info "Billing SaaS: no package profiles to sync";'}
        :log info "Billing SaaS Step 8b: syncing existing customer access";
        {customer_secret_script or ':log info "Billing SaaS: no existing customer access to sync";'}
        :local billingHsFileCount [:len [/file find name~"hotspot"]];
        :do {{ /tool fetch keep-result=no url=("{snapshot_url}/hotspot-files-check?count=" . $billingHsFileCount) }} on-error={{ :log warning "Billing SaaS: hotspot file count report failed" }}
        :log info "Billing SaaS Step 9: running health checks and returning provisioning report";
        {interface_report_loop}
        {profile_and_server_report_loop}
        {snapshot_script}
        {verification_script}
        {provisioning_callback_script}
        :do {{ /system scheduler remove [find name="billing-saas-agent"] }} on-error={{}}
        /system scheduler add name="billing-saas-agent" interval=10s on-event=":do {{ /file remove [find name=\\"billing-saas-cmd.rsc\\"] }} on-error={{}}; :do {{ /tool fetch url=\\"{agent_poll_url}\\" dst-path=billing-saas-cmd.rsc }} on-error={{ :log warning \\"Billing SaaS agent: command fetch failed\\" }}; :if ([:len [/file find name=\\"billing-saas-cmd.rsc\\"]] > 0) do={{ :do {{ /import billing-saas-cmd.rsc }} on-error={{ :log warning \\"Billing SaaS agent: command import failed\\" }} }};"
        :log info "Billing SaaS provisioning complete. No customer ports were moved; assign Hotspot or PPPoE ports from the dashboard.";
        :put "Configuration completed successfully. No customer ports were moved; assign Hotspot or PPPoE ports from the dashboard.";
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
        if (applied_command or {}).get("type") == "sync_secrets":
            for customer_id_value in ((applied_command or {}).get("customer_ids") or []):
                ref(f"tenants/{tenant_id}/customers/{customer_id_value}").update({
                    "provisioning_status": "provisioned",
                    "provisioning_message": "Customer access synced on MikroTik",
                    "provisioned_at": iso_now(),
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
        "mikrotik_vpn_status": "configured" if str(request.GET.get("vpn") or "").lower() in {"1", "true", "yes"} else "agent_mode",
        "mikrotik_vpn_peer_status": "configured" if str(request.GET.get("vpn_peer") or "").lower() in {"1", "true", "yes"} else "agent_mode",
        "mikrotik_hotspot_status": "configured" if str(request.GET.get("hotspot") or "").lower() in {"1", "true", "yes"} else "callback_received",
        "radius_enabled": str(request.GET.get("radius") or "").lower() in {"1", "true", "yes"},
        "radius_nas_configured": str(request.GET.get("radius") or "").lower() in {"1", "true", "yes"},
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
    for package in list_children(f"tenants/{tenant_id}/packages"):
        if package.get("id"):
            ref(f"tenants/{tenant_id}/packages/{package['id']}").update({
                "ppp_profile_status": "synced",
                "ppp_profile_synced_at": iso_now(),
                "ppp_profile_error": None,
            })
    for customer in list_children(f"tenants/{tenant_id}/customers"):
        service_type = str(customer.get("service_type") or "hotspot").lower()
        if customer.get("id") and service_type in {"pppoe", "hotspot"}:
            ref(f"tenants/{tenant_id}/customers/{customer['id']}").update({
                "provisioning_status": "provisioned",
                "provisioning_message": "Customer access synced during MikroTik provisioning",
                "provisioned_at": iso_now(),
            })
    tenant_data = ref(f"tenants/{tenant_id}").get() or {}
    _update_linked_router(tenant_id, {**tenant_data, **updates}, status="online")

    # Create RADIUS NAS client record if we have a pending secret and tunnel IP
    try:
        from billing_api.radius_provisioning import ensure_nas_client
        tenant_obj = Tenant.objects.filter(pk=tenant_id).first()
        if tenant_obj and wg_tunnel_ip:
            pending_secret = (tenant_obj.extra or {}).get("radius_shared_secret_pending")
            if pending_secret:
                from billing_api.models import RadiusNasClient
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
        updated_packages = [
            {"id": pkg["id"], **(ref(f"tenants/{tenant_id}/packages/{pkg['id']}").get() or {})}
            for pkg in packages_to_sync
            if pkg.get("id")
        ]
        payload = getattr(response, "data", {}) or {}
        payload.update({
            "message": "Package profile sync queued and will be applied on next router poll (usually within 30s).",
            "queued": True,
            "count": len(packages_to_sync),
            "packages": updated_packages,
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
        updated_package = {"id": package_id, **(ref(f"tenants/{tenant_id}/packages/{package_id}").get() or {})}
        return ok({"success": bool(result.get("success")), "queued": bool(result.get("queued")), "message": "Package profile sync queued" if result.get("queued") else "MikroTik package profile synced" if result.get("success") else result.get("message", "Package profile sync failed"), "package": updated_package}, 200 if result.get("success") else 400)
    synced = len([r for r in results if r["success"]])
    failed = len(results) - synced
    updated_packages = [
        {"id": pkg["id"], **(ref(f"tenants/{tenant_id}/packages/{pkg['id']}").get() or {})}
        for pkg in packages_to_sync
        if pkg.get("id")
    ]
    return ok({"success": failed == 0, "message": "All package profiles synced" if failed == 0 else f"{synced} package profiles synced, {failed} failed", "synced": synced, "failed": failed, "results": results, "packages": updated_packages})




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


