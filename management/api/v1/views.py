import json
import html
import logging
import os
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
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from billing_api.auth import admin_required, tenant_required
from billing_api.models import AdminUser, Customer, InternetPackage, Payment, SubscriptionPayment, Tenant, TenantSubscription, Ticket, User
from billing_api.services import (
    admin_token,
    check_password,
    create_hotspot_profile,
    create_ppp_profile,
    configure_router_port,
    selected_daraja_method,
    initiate_daraja_payment,
    initiate_daraja_b2c,
    platform_daraja_config,
    query_daraja_stk_payment,
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
    write_audit_log,
)
from network.api.v1.views import (
    _customer_secret_script,
    _hotspot_captive_file_script,
    _linked_router_from_tenant,
    _package_profile_script,
    _queue_router_command,
    _queue_router_command_for_tenant,
    _router_is_agent_linked,
)

logger = logging.getLogger(__name__)


DEFAULT_SITE = {
    "brand_name": "Expressnet",
    "headline": "Internet billing built for hotspot businesses",
    "subheadline": "Sell packages, collect M-Pesa payments, and activate MikroTik users automatically.",
    "about": "We help hotspot operators manage customers, packages, payments, and access control from one secure platform.",
    "phone": "+254 701396967/+254 729 281669",
    "email": "expressnet.support@gmail.com",
    "location": "Thika , Kenya",
    "address": "Nairobi, Kenya",
    "cta_label": "Register your business",
    "cta_url": "/register",
}
MASKED = "••••••••"
SENSITIVE_FIELDS = {"password", "mikrotik_pass", }


def _customer_username_seed(name, phone):
    base = "".join(ch for ch in str(name or "").lower() if ch.isalnum())
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if base:
        return base[:12]
    if digits:
        return f"user{digits[-6:]}"
    return f"user{secrets.token_hex(3)}"


def _generate_customer_username(tenant_id, name, phone):
    existing = {
        str(customer.get("username") or "").lower()
        for customer in list_children(f"tenants/{tenant_id}/customers")
    }
    seed = _customer_username_seed(name, phone)
    username = seed
    while username.lower() in existing:
        username = f"{seed}{secrets.randbelow(900) + 100}"
    return username


def _generate_customer_password():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _customer_credentials_sms(customer, tenant):
    brand = tenant.get("business_name") or tenant.get("name") or "Expressnet"
    service_type = str(customer.get("service_type") or "internet").upper()
    return (
        f"{brand} {service_type} login details: "
        f"Username: {customer.get('username')}. "
        f"Password: {customer.get('password')}. "
        "Please keep them safe."
    )


def _find_team_member_by_label(tenant, label):
    needle = str(label or "").strip().lower()
    if not needle:
        return None
    members = tenant.get("team_members") if isinstance(tenant, dict) else {}
    if not isinstance(members, dict):
        members = {}
    for member_id, member in members.items():
        values = [
            member_id,
            member.get("name"),
            member.get("email"),
            member.get("phone"),
            member.get("role"),
        ]
        if any(str(value or "").strip().lower() == needle for value in values):
            return {"id": member_id, **member}
    return None


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
    if unit in {"month", "months"}:
        try:
            return timedelta(days=31 * float((package or {}).get("duration_value") or (package or {}).get("duration_months") or 1))
        except (TypeError, ValueError):
            return timedelta(days=31)
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


def _add_calendar_months(start, months):
    whole_months = max(1, int(months))
    month_index = start.month - 1 + whole_months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    next_month_index = month if month < 12 else 0
    next_month_year = year if month < 12 else year + 1
    last_day = (datetime(next_month_year, next_month_index + 1, 1) - timedelta(days=1)).day
    return start.replace(year=year, month=month, day=min(start.day, last_day))


def package_expiry_date(start, package):
    unit = str((package or {}).get("duration_unit") or "").strip().lower()
    if unit in {"month", "months"}:
        try:
            return _add_calendar_months(start, float((package or {}).get("duration_value") or (package or {}).get("duration_months") or 1))
        except (TypeError, ValueError):
            return _add_calendar_months(start, 1)
    return start + package_duration_delta(package)


def normalized_package_payload(data, default_service_type="hotspot", include_service_type=True):
    service_type = package_service_type(data or {})
    if service_type not in {"hotspot", "pppoe"}:
        service_type = default_service_type if default_service_type in {"hotspot", "pppoe"} else "hotspot"
    raw_unit = str((data or {}).get("duration_unit") or "").lower()
    if raw_unit.startswith("hour"):
        duration_unit = "hours"
    elif raw_unit.startswith("month"):
        duration_unit = "months"
    else:
        duration_unit = "days"
    if duration_unit == "months":
        duration_value = float((data or {}).get("duration_value") or (data or {}).get("duration_months") or 1)
    elif duration_unit == "hours":
        duration_value = float((data or {}).get("duration_value") or (data or {}).get("duration_hours") or 1)
    else:
        duration_value = float((data or {}).get("duration_value") or (data or {}).get("duration_days") or 1)
    if service_type == "pppoe" and duration_value < 1:
        duration_value = 1
    if duration_unit == "hours":
        duration_days = 1
        duration_hours = duration_value
    elif duration_unit == "months":
        duration_days = int(duration_value * 31)
        duration_hours = duration_days * 24
    else:
        duration_days = int(duration_value)
        duration_hours = duration_value * 24
    payload = {
        "duration_unit": duration_unit,
        "duration_value": duration_value,
        "duration_days": duration_days,
        "duration_hours": duration_hours,
        "duration_months": duration_value if duration_unit == "months" else "",
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
    unit = str((package or {}).get("duration_unit") or "").strip().lower()
    if unit in {"month", "months"}:
        value = float(package.get("duration_value") or 1)
        return f"{value:g} month{'s' if float(value) != 1 else ''}"
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
        getattr(settings, "PUBLIC_APP_URL", ""),
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
        "payout_phone": tenant.get("payout_phone") or tenant.get("phone") or "",
        "bank_code": tenant.get("bank_code") or "",
        "bank_name": tenant.get("bank_name") or "",
        "bank_account_number": tenant.get("bank_account_number") or "",
        "payment_methods": tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else ["daraja_paybill"],
        "daraja_consumer_key": tenant.get("daraja_consumer_key") or "",
        "daraja_consumer_secret": tenant.get("daraja_consumer_secret") or "",
        "daraja_shortcode": tenant.get("daraja_shortcode") or "",
        "daraja_passkey": tenant.get("daraja_passkey") or "",
        "daraja_till_number": tenant.get("daraja_till_number") or "",
        "daraja_shortcode_type": tenant.get("daraja_shortcode_type") or "CustomerPayBillOnline",
        "daraja_environment": tenant.get("daraja_environment") or "production",
        "settlement_status": tenant.get("settlement_status") or ("ready" if tenant_payout_details(tenant).get("payout_phone") else "missing_payout_details"),
        "settlement_pending_amount": float(tenant.get("settlement_pending_amount") or 0),
    }



@csrf_exempt
@api_view(["GET"])
def public_tenant(request, tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get()
    if not tenant:
        return ok({"message": "Tenant not found"}, 404)
    payment_methods = tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else []
    public_payment_methods = [
        str(method).strip().lower()
        for method in payment_methods
        if str(method).strip().lower() in {"daraja_paybill", "daraja_buygoods"}
    ] or [selected_daraja_method(tenant)]
    return ok({
        "id": tenant_id,
        "business_name": tenant.get("business_name"),
        "phone": tenant.get("phone"),
        "status": tenant.get("status"),
        "logo_url": tenant.get("logo_url") or "",
        "payment_methods": public_payment_methods,
    })




def _public_package_payload(pkg):
    amount_payable = float(pkg.get("amount_payable") or pkg.get("price") or 0)
    return {
        **{key: pkg.get(key) for key in ["id", "name", "speed", "duration_days", "duration_unit", "duration_value", "duration_hours", "price", "service_type"]},
        "amount_payable": amount_payable,
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
    router_client_ip = str(data.get("ip") or data.get("client_ip") or "").strip()
    router_ip = str(data.get("router_ip") or "").strip()
    router_mac = str(data.get("mac") or data.get("router_mac") or "").strip()
    router_client_mac = normalize_mac(router_mac)
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
    amount_payable = float(pkg.get("amount_payable") or pkg.get("price") or 0)
    customer = None
    username = ""
    mac_address = router_client_mac if service_type == "hotspot" else ""
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
    access_username = mac_address if service_type == "tv" else ((customer or {}).get("username") or username or to_access_username(phone))
    pending_access_password = str((customer or {}).get("password") or secrets.token_hex(3).upper())
    daraja_method = selected_daraja_method(tenant, data.get("payment_method"))
    daraja_config = platform_daraja_config(tenant, daraja_method)
    daraja_source = daraja_config.get("daraja_credential_source") or "platform"
    collection_account = "tenant_daraja" if daraja_source == "tenant" else "platform_daraja"
    payment_ref = ref(f"tenants/{tenant_id}/payments").push(
        {
            "customer_id": customer.get("id") if customer else None,
            "customer_name": customer.get("name") if customer else None,
            "package_id": data["package_id"],
            "package_name": pkg.get("name"),
            "amount": amount_payable,
            "amount_payable": amount_payable,
            "payment_code": None,
            "phone": phone,
            "status": "pending",
            "paid_at": None,
            "initiated_at": iso_now(),
            "service_type": service_type,
            "username": access_username,
            "pending_access_password": pending_access_password,
            "mac_address": mac_address,
            "router_ip": router_ip,
            "router_client_ip": router_client_ip,
            "router_client_mac": router_client_mac,
            "router_mac": router_mac,
            "link_login": link_login,
            "dst": dst,
            "source": "customer_portal",
            "provider": "mpesa",
            "payment_method": daraja_method,
            "collection_account": collection_account,
            "daraja_credential_source": daraja_source,
            "tenant_settlement_status": "not_required" if collection_account == "tenant_daraja" else "pending_payment",
            "tenant_payout": tenant_payout_details(tenant),
        }
    )
    try:
        checkout = initiate_daraja_payment(
            daraja_config,
            payment_ref.key,
            amount_payable,
            phone=phone,
            description=f"{pkg.get('name')} internet package",
            metadata={
                "package_id": data["package_id"],
                "package_name": pkg.get("name"),
                "service_type": service_type,
                "username": access_username,
                "pending_access_password": pending_access_password,
                "mac_address": mac_address,
                "router_ip": router_ip,
                "router_client_ip": router_client_ip,
                "router_client_mac": router_client_mac,
                "router_mac": router_mac,
                "link_login": link_login,
                "dst": dst,
            },
            payment_method=daraja_method,
        )
        payment_ref.update({"daraja_checkout_request_id": checkout.get("checkout_request_id"), "daraja_merchant_request_id": checkout.get("merchant_request_id"), "daraja_callback_url": checkout.get("callback_url"), "checkout_requested_at": iso_now()})
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
@api_view(["GET"])
def public_verify(request, tenant_id):
    requested_payment_id = request.GET.get("payment_id") or request.GET.get("paymentId")
    if not requested_payment_id:
        return ok({"message": "Payment ID is required"}, 400)
    payment_id = str(requested_payment_id)
    payment = ref(f"tenants/{tenant_id}/payments/{payment_id}").get()
    if not payment:
        return ok({"message": "Payment not found"}, 404)
    if payment.get("status") != "success":
        checkout_request_id = payment.get("daraja_checkout_request_id") or payment.get("checkout_request_id")
        if checkout_request_id:
            tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})}
            try:
                result = query_daraja_stk_payment(tenant, checkout_request_id, payment.get("payment_method"))
                result_code = str(result.get("ResultCode") if result.get("ResultCode") is not None else "")
                if result_code == "0":
                    receipt = payment.get("daraja_receipt_number") or payment.get("payment_code") or checkout_request_id
                    complete_daraja_payment(
                        tenant_id,
                        payment_id,
                        {},
                        payment.get("amount") or payment.get("amount_payable"),
                        receipt,
                        iso_now(),
                        payment.get("phone"),
                    )
                    payment = ref(f"tenants/{tenant_id}/payments/{payment_id}").get() or payment
                elif result_code:
                    ref(f"tenants/{tenant_id}/payments/{payment_id}").update({
                        "status": "failed",
                        "failed_at": iso_now(),
                        "callback_result_code": result_code,
                        "callback_result_desc": result.get("ResultDesc") or "M-Pesa payment was not successful",
                    })
                    return ok({"success": False, "status": "failed", "message": result.get("ResultDesc") or "M-Pesa payment was not successful"}, 400)
            except PaymentProviderError as exc:
                return ok({"success": False, "status": payment.get("status") or "pending", "message": exc.public_message}, 202)
            except Exception:
                logger.exception("Daraja STK verification failed tenant=%s payment=%s checkout=%s", tenant_id, payment_id, checkout_request_id)
        if payment.get("status") != "success":
            return ok({"success": False, "status": payment.get("status") or "pending", "message": "Waiting for M-Pesa confirmation."}, 202)
    if payment.get("status") == "success" and not payment.get("access_username"):
        tenant = {"id": tenant_id, **(ref(f"tenants/{tenant_id}").get() or {})}
        try:
            activate_paid_access(
                tenant,
                payment_id,
                payment,
                payment.get("phone"),
                payment.get("payment_code") or payment.get("daraja_receipt_number") or payment.get("daraja_checkout_request_id") or payment_id,
            )
            payment = ref(f"tenants/{tenant_id}/payments/{payment_id}").get() or payment
        except Exception:
            logger.exception("Paid payment activation retry failed tenant=%s payment=%s", tenant_id, payment_id)
            return ok({"success": False, "status": "activation_failed", "message": "Payment confirmed, but internet activation is still pending. Please wait a moment."}, 202)
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
            "link_login": payment.get("link_login"),
            "dst": payment.get("dst"),
            "expires_at": payment.get("access_expires_at"),
            "paymentId": payment_id,
        }
    )


def _truthy(value):
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _pppoe_grace_payload(data, service_type):
    if service_type != "pppoe" or not _truthy(data.get("grace_period_enabled")):
        return {
            "grace_period_enabled": False,
            "grace_period_value": "",
            "grace_period_unit": "",
            "grace_started_at": "",
            "grace_expires_at": "",
            "grace_source": "",
            "expiry_date": None,
        }
    try:
        value = float(data.get("grace_period_value") or 0)
    except (TypeError, ValueError):
        return ok({"message": "Grace period value must be a number"}, 400)
    if value <= 0:
        return ok({"message": "Grace period must be greater than zero"}, 400)
    unit = str(data.get("grace_period_unit") or "days").strip().lower()
    if unit not in {"hours", "days", "weeks", "months"}:
        return ok({"message": "Grace period unit must be hours, days, weeks, or months"}, 400)
    multipliers = {
        "hours": timedelta(hours=value),
        "days": timedelta(days=value),
        "weeks": timedelta(weeks=value),
        "months": timedelta(days=value * 30),
    }
    started_at = utcnow()
    expires_at = started_at + multipliers[unit]
    return {
        "grace_period_enabled": True,
        "grace_period_value": value,
        "grace_period_unit": unit,
        "grace_started_at": started_at.isoformat(),
        "grace_expires_at": expires_at.isoformat(),
        "grace_source": "tenant_free_access",
        "expiry_date": expires_at.isoformat(),
        "status": "active",
        "auto_reconnect": True,
    }


@csrf_exempt
@api_view(["GET", "PATCH", "DELETE"])
@tenant_required
def customers(request, customer_id=None):
    tenant = request.tenant
    if method(request, "GET") and not customer_id:
        ensure_expired_customer_invoices(tenant)
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
        allowed = [
            "name",
            "phone",
            "location",
            "username",
            "password",
            "amount_payable",
            "package",
            "service_type",
            "status",
            "expiry_date",
            "auto_reconnect",
            "technician",
            "router_serial_number",
            "mikrotik_router_id",
            "support",
            "grace_period_enabled",
            "grace_period_value",
            "grace_period_unit",
            "grace_started_at",
            "grace_expires_at",
            "grace_source",
        ]
        updates = {field: data[field] for field in allowed if field in data}
        service_type_for_update = str(updates.get("service_type") or customer.get("service_type") or "hotspot").lower()
        adjustment_applied = False
        if service_type_for_update == "hotspot" and any(field in data for field in ["session_adjustment_value", "session_adjustment_unit", "session_adjustment_direction"]):
            raw_value = data.get("session_adjustment_value")
            if raw_value not in (None, ""):
                try:
                    adjustment_value = float(raw_value)
                except (TypeError, ValueError):
                    return ok({"message": "Session adjustment must be a valid number"}, 400)
                if adjustment_value <= 0:
                    return ok({"message": "Session adjustment must be greater than zero"}, 400)
                unit = str(data.get("session_adjustment_unit") or "hours").strip().lower()
                if unit not in {"minutes", "hours", "days"}:
                    return ok({"message": "Session adjustment unit must be minutes, hours, or days"}, 400)
                direction = str(data.get("session_adjustment_direction") or "add").strip().lower()
                if direction not in {"add", "subtract"}:
                    return ok({"message": "Session adjustment direction must be add or subtract"}, 400)
                current_expiry = parse_datetime(str(customer.get("expiry_date") or "")) if customer.get("expiry_date") else None
                if current_expiry and current_expiry.tzinfo is None:
                    current_expiry = timezone.make_aware(current_expiry, timezone.get_current_timezone())
                base_expiry = current_expiry or timezone.now()
                delta_kwargs = {unit: adjustment_value}
                delta = timedelta(**delta_kwargs)
                next_expiry = base_expiry + delta if direction == "add" else base_expiry - delta
                updates["expiry_date"] = next_expiry.isoformat()
                updates["session_adjusted_at"] = iso_now()
                updates["session_adjustment_note"] = f"{direction} {adjustment_value:g} {unit}"
                adjustment_applied = True
        if service_type_for_update == "pppoe" and any(field in data for field in ["grace_period_enabled", "grace_period_value", "grace_period_unit"]):
            grace_payload = _pppoe_grace_payload(data, service_type_for_update)
            if isinstance(grace_payload, Response):
                return grace_payload
            updates.update(grace_payload)
        elif service_type_for_update != "pppoe" and any(field in data for field in ["grace_period_enabled", "grace_period_value", "grace_period_unit"]):
            updates.update({
                "grace_period_enabled": False,
                "grace_period_value": "",
                "grace_period_unit": "",
                "grace_started_at": "",
                "grace_expires_at": "",
                "grace_source": "",
            })
        if not updates:
            return ok({"message": "No customer fields provided"}, 400)
        if "username" in updates:
            duplicate = any(
                str(item.get("id")) != str(customer_id)
                and str(item.get("username", "")).lower() == str(updates["username"]).lower()
                for item in list_children(f"tenants/{tenant['id']}/customers")
            )
            if duplicate:
                return ok({"message": "A customer with this username already exists"}, 409)
        updates["updated_at"] = iso_now()
        ref(f"tenants/{tenant['id']}/customers/{customer_id}").update(updates)
        should_sync_router = any(field in updates for field in ["username", "password", "package", "service_type", "status", "expiry_date"]) or adjustment_applied
        if should_sync_router and service_type_for_update in {"pppoe", "hotspot"}:
            synced_customer = {**customer, **updates}
            pkg = find_child_by_field(f"tenants/{tenant['id']}/packages", "name", synced_customer.get("package"))
            sync_payload = {
                **synced_customer,
                "package_name": synced_customer.get("package"),
                "speed": (pkg or {}).get("speed"),
                "duration_seconds": int(package_duration_delta(pkg).total_seconds()) if pkg and service_type_for_update == "hotspot" else None,
            }
            try:
                if has_mikrotik_credentials(tenant):
                    upsert_customer_access(tenant, sync_payload, disabled=sync_payload.get("status") != "active")
                    set_customer_enabled(tenant, sync_payload.get("username"), service_type_for_update, sync_payload.get("status") == "active")
                elif _router_is_agent_linked(tenant):
                    _queue_router_command(request, {
                        "type": "sync_secrets",
                        "customer_ids": [customer_id],
                        "script": _customer_secret_script(sync_payload),
                    })
            except Exception:
                logger.exception("Failed to sync customer access update")
        if "password" in updates or "username" in updates:
            try:
                send_sms_message(
                    updates.get("phone") or customer.get("phone"),
                    _customer_credentials_sms({**customer, **updates}, tenant),
                    tenant,
                )
            except Exception:
                logger.exception("Failed to send customer credential SMS")
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
    required = ["name", "phone", "package_name"]
    if any(not data.get(field) for field in required):
        return ok({"message": "Name, phone, and package are required"}, 400)
    data["username"] = str(data.get("username") or "").strip() or _generate_customer_username(request.tenant["id"], data.get("name"), data.get("phone"))
    data["password"] = str(data.get("password") or "").strip() or _generate_customer_password()
    if any(str(c.get("username", "")).lower() == str(data["username"]).lower() for c in list_children(f"tenants/{request.tenant['id']}/customers")):
        return ok({"message": "A customer with this username already exists"}, 409)
    service_type = str(data.get("service_type") or "hotspot").strip().lower()
    if service_type not in {"pppoe", "hotspot", "static"}:
        return ok({"message": "Customer service type must be PPPoE, Hotspot, or Static"}, 400)
    customer_status = str(data.get("status") or "active").strip().lower()
    if customer_status not in {"active", "inactive", "paused", "suspended"}:
        return ok({"message": "Customer status must be active, inactive, paused, or suspended"}, 400)
    router_disabled = customer_status != "active"
    provision = data.get("provision_mikrotik")
    if provision is None:
        provision = service_type in {"pppoe", "hotspot"}
    provision = bool(provision)
    if provision and service_type == "static":
        return ok({"message": "Static customers can be saved here, but MikroTik auto-provisioning is only available for PPPoE and Hotspot customers"}, 400)
    linked_routers = request.tenant.get("linked_routers") or {}
    mikrotik_router_id = str(data.get("mikrotik_router_id") or "").strip()
    if service_type in {"pppoe", "static"} and not str(data.get("technician") or "").strip():
        return ok({"message": "Select the technician assigned to this customer"}, 400)
    if service_type in {"pppoe", "static"} and linked_routers:
        if not mikrotik_router_id:
            return ok({"message": "Select the MikroTik for this customer"}, 400)
        if mikrotik_router_id not in linked_routers:
            return ok({"message": "Selected MikroTik was not found"}, 400)
    if provision and linked_routers:
        if not mikrotik_router_id:
            return ok({"message": "Select the MikroTik router for this customer"}, 400)
        if mikrotik_router_id not in linked_routers:
            return ok({"message": "Selected MikroTik router was not found"}, 400)
    pkg = find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", data["package_name"])
    if not pkg:
        return ok({"message": f"Package \"{data['package_name']}\" was not found"}, 404)
    amount_payable = float(data.get("amount_payable") or pkg.get("amount_payable") or pkg.get("price") or 0)
    grace_payload = _pppoe_grace_payload(data, service_type)
    if isinstance(grace_payload, Response):
        return grace_payload
    if grace_payload.get("grace_period_enabled"):
        customer_status = "active"
        router_disabled = False
    provisioning_status = "not_requested"
    provisioning_message = None
    if provision:
        if not has_mikrotik_credentials(request.tenant) and not _router_is_agent_linked(request.tenant):
            return ok({"message": "Link a MikroTik router before provisioning customers"}, 400)
        if has_mikrotik_credentials(request.tenant):
            try:
                if service_type == "pppoe":
                    create_ppp_profile(request.tenant, pkg["name"], pkg.get("speed"))
                else:
                    create_hotspot_profile(request.tenant, pkg["name"], pkg.get("speed"))
                upsert_customer_access(request.tenant, {**data, "service_type": service_type, "status": customer_status}, disabled=router_disabled)
                provisioning_status = "provisioned"
                provisioning_message = f"{service_type.upper()} access created on MikroTik"
            except (TimeoutError, OSError):
                _queue_router_command(request, {
                    "type": "sync_secrets",
                    "router_id": mikrotik_router_id,
                    "script": _customer_secret_script({**data, "package": data["package_name"], "speed": pkg.get("speed"), "service_type": service_type, "status": customer_status}),
                })
                provisioning_status = "queued"
                provisioning_message = f"{service_type.upper()} access queued for MikroTik sync"
        else:
            _queue_router_command(request, {
                "type": "sync_secrets",
                "router_id": mikrotik_router_id,
                "script": _customer_secret_script({**data, "package": data["package_name"], "speed": pkg.get("speed"), "service_type": service_type, "status": customer_status}),
            })
            provisioning_status = "queued"
            provisioning_message = f"{service_type.upper()} access queued for MikroTik sync"
    customer_payload = {
            "name": data["name"],
            "phone": data["phone"],
            "location": data.get("location") or "",
            "username": data["username"],
            "password": data["password"],
            "technician": data.get("technician") or "",
            "router_serial_number": data.get("router_serial_number") or "",
            "mikrotik_router_id": mikrotik_router_id,
            "support": data.get("support") or "",
            "package": data["package_name"],
            "amount_payable": amount_payable,
            "service_type": service_type,
            "provisioning_status": provisioning_status,
            "provisioning_message": provisioning_message,
            "status": customer_status,
            "expiry_date": grace_payload.get("expiry_date"),
            **grace_payload,
            "auto_reconnect": True,
            "created_at": iso_now(),
        }
    new_ref = ref(f"tenants/{request.tenant['id']}/customers").push(customer_payload)
    notification_result = None
    if service_type in {"pppoe", "static"}:
        try:
            notification_result = notify_customer_created(request.tenant, customer_payload)
            ref(f"tenants/{request.tenant['id']}/customers/{new_ref.key}").update({
                "customer_created_notification_status": "sent" if notification_result.get("customer_whatsapp", {}).get("sent") else "skipped",
                "customer_created_notification_result": notification_result,
                "customer_created_notification_at": iso_now(),
            })
        except Exception:
            logger.exception("Failed to send customer creation WhatsApp notification")
    # Sync to Postgres + RADIUS if tenant has RADIUS enabled
    if request.tenant.get("radius_enabled"):
        try:
            from billing_api.radius_provisioning import upsert_pg_customer, sync_radius_customer
            from billing_api.models import Tenant as TenantModel
            tenant_obj = TenantModel.objects.get(pk=request.tenant["id"])
            pg_customer = upsert_pg_customer(tenant_obj, {**data, "service_type": service_type, "status": customer_status})
            if pg_customer:
                sync_radius_customer(tenant_obj, pg_customer)
        except Exception:
            pass
    return ok({"success": True, "message": "Customer added", "customerId": new_ref.key, "notification": notification_result})


def _normalize_permissions(value):
    return value if isinstance(value, dict) else {}


def _team_member_payload(member_id, member):
    return {
        "id": member_id,
        "name": member.get("name") or "",
        "email": member.get("email") or "",
        "phone": member.get("phone") or "",
        "role": member.get("role") or "",
        "status": member.get("status") or "active",
        "permissions": _normalize_permissions(member.get("permissions")),
        "created_at": member.get("created_at") or "",
        "updated_at": member.get("updated_at") or "",
    }


def _tenant_team_members(tenant_id):
    tenant = ref(f"tenants/{tenant_id}").get() or {}
    members = tenant.get("team_members") or {}
    return members if isinstance(members, dict) else {}


def _save_tenant_team_members(tenant_id, members):
    ref(f"tenants/{tenant_id}").update({"team_members": members})


@csrf_exempt
@api_view(["GET"])
@tenant_required
def staff_members(request):
    members = request.tenant.get("team_members") if isinstance(request.tenant, dict) else None
    if not isinstance(members, dict):
        members = _tenant_team_members(request.tenant["id"])
    active_members = [
        _team_member_payload(member_id, member)
        for member_id, member in members.items()
        if member.get("status", "active") == "active"
    ]
    return as_collection_response(request, active_members)


@csrf_exempt
@api_view(["GET", "DELETE"])
@tenant_required
def team_members(request, member_id=None):
    tenant_id = request.tenant["id"]
    members = _tenant_team_members(tenant_id)
    if method(request, "GET") and not member_id:
        return as_collection_response(
            request,
            [_team_member_payload(member_id, member) for member_id, member in members.items()],
        )
    if method(request, "DELETE") and member_id:
        if member_id not in members:
            return ok({"message": "User not found"}, 404)
        members.pop(member_id, None)
        _save_tenant_team_members(tenant_id, members)
        return ok({"success": True, "message": "User removed"})
    return ok({"message": "Method not allowed"}, 405)


@csrf_exempt
@api_view(["POST"])
@tenant_required
def team_invite(request):
    data = body(request)
    name = str(data.get("name") or "").strip()
    email = str(data.get("email") or "").lower().strip()
    phone = str(data.get("phone") or "").strip()
    role = str(data.get("role") or "").strip()
    if not name or not email:
        return ok({"message": "Name and email are required"}, 400)
    password = str(data.get("password") or "").strip()
    if len(password) < 6:
        return ok({"message": "Password must be at least 6 characters"}, 400)
    tenant_id = request.tenant["id"]
    members = _tenant_team_members(tenant_id)
    if any(str(member.get("email") or "").lower() == email for member in members.values()):
        return ok({"message": "A user with this email already exists"}, 409)
    member_id = secrets.token_hex(8)
    member = {
        "name": name,
        "email": email,
        "phone": phone,
        "role": role,
        "password": hash_password(password),
        "permissions": _normalize_permissions(data.get("permissions")),
        "status": "active",
        "created_at": iso_now(),
        "updated_at": iso_now(),
    }
    members[member_id] = member
    _save_tenant_team_members(tenant_id, members)
    return ok({"success": True, "message": "User created", "member": _team_member_payload(member_id, member)})


@csrf_exempt
@api_view(["PATCH"])
@tenant_required
def team_member_permissions(request, member_id):
    tenant_id = request.tenant["id"]
    members = _tenant_team_members(tenant_id)
    member = members.get(member_id)
    if not member:
        return ok({"message": "User not found"}, 404)
    updates = {
        "permissions": _normalize_permissions(body(request).get("permissions")),
        "updated_at": iso_now(),
    }
    members[member_id] = {**member, **updates}
    _save_tenant_team_members(tenant_id, members)
    return ok({"success": True, "message": "Permissions saved", "member": _team_member_payload(member_id, {**member, **updates})})




def _tenant_extra_dict(tenant_id, key):
    tenant = ref(f"tenants/{tenant_id}").get() or {}
    value = tenant.get(key) or {}
    return value if isinstance(value, dict) else {}


def _save_tenant_extra_dict(tenant_id, key, value):
    ref(f"tenants/{tenant_id}").update({key: value})


def _current_member_payload(request):
    member = getattr(request, "tenant_member", None) or {}
    return {
        "id": member.get("id") or request.tenant.get("member_id") or "",
        "name": member.get("name") or request.tenant.get("name") or request.tenant.get("email") or "",
        "email": member.get("email") or request.tenant.get("email") or "",
        "phone": member.get("phone") or request.tenant.get("phone") or "",
        "role": member.get("role") or request.tenant.get("role") or "",
        "status": member.get("status") or "active",
        "created_at": member.get("created_at") or "",
        "updated_at": member.get("updated_at") or "",
        "permissions": _normalize_permissions(member.get("permissions")),
    }


def _task_date(value):
    parsed = parse_date(value)
    if parsed:
        return parsed.date()
    return None


def _is_current_week_task(task):
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    candidate = _task_date(task.get("due_date")) or _task_date(task.get("created_at")) or _task_date(task.get("updated_at"))
    return bool(candidate and week_start <= candidate <= week_end)


def _staff_task_visible(task):
    status = str(task.get("status") or "pending").lower()
    return status in {"pending", "in_progress"} or _is_current_week_task(task)


def _assigned_staff_tasks(tenant_id, member_id):
    return [
        item for item in list_children(f"tenants/{tenant_id}/tickets")
        if str(item.get("assigned_to") or "") == str(member_id) and _staff_task_visible(item)
    ]


@csrf_exempt
@api_view(["GET"])
@tenant_required
def staff_profile(request):
    member = _current_member_payload(request)
    tenant_id = request.tenant["id"]
    tasks = _assigned_staff_tasks(tenant_id, member["id"])
    reports = _tenant_extra_dict(tenant_id, "staff_work_reports")
    requisitions = _tenant_extra_dict(tenant_id, "requisitions")
    my_reports = [report for report in reports.values() if str(report.get("staff_id") or "") == str(member["id"])]
    my_requisitions = [item for item in requisitions.values() if str(item.get("requested_by") or "") == str(member["id"])]
    complete_statuses = {"complete", "completed"}
    completed = sum(1 for task in tasks if str(task.get("status") or "").lower() in complete_statuses)
    return ok(
        {
            "staff": member,
            "tasks": tasks,
            "stats": {
                "assigned_tasks": len(tasks),
                "completed_tasks": completed,
                "pending_tasks": max(len(tasks) - completed, 0),
                "reports_submitted": len(my_reports),
                "requisitions_submitted": len(my_requisitions),
            },
        }
    )


@csrf_exempt
@api_view(["GET", "POST", "PATCH", "DELETE"])
@tenant_required
def requisitions(request, requisition_id=None):
    tenant_id = request.tenant["id"]
    items = _tenant_extra_dict(tenant_id, "requisitions")
    if method(request, "GET") and not requisition_id:
        return as_collection_response(request, [{"id": item_id, **item} for item_id, item in items.items()])
    if method(request, "POST") and not requisition_id:
        data = body(request)
        item_type = str(data.get("type") or data.get("item_type") or "").strip()
        title = str(data.get("title") or data.get("item") or "").strip()
        if not item_type or not title:
            return ok({"message": "Requisition type and item are required"}, 400)
        requester = _current_member_payload(request)
        new_id = secrets.token_hex(8)
        item = {
            "type": item_type,
            "title": title,
            "quantity": str(data.get("quantity") or "1").strip(),
            "reason": str(data.get("reason") or "").strip(),
            "status": str(data.get("status") or "pending").strip(),
            "requested_by": str(data.get("requested_by") or requester["id"]).strip(),
            "requested_by_name": str(data.get("requested_by_name") or requester["name"]).strip(),
            "requested_by_role": str(data.get("requested_by_role") or requester["role"]).strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        items[new_id] = item
        _save_tenant_extra_dict(tenant_id, "requisitions", items)
        return ok({"success": True, "message": "Requisition created", "requisition": {"id": new_id, **item}}, 201)
    if not requisition_id:
        return ok({"message": "Requisition id is required"}, 400)
    item = items.get(requisition_id)
    if not item:
        return ok({"message": "Requisition not found"}, 404)
    if method(request, "PATCH"):
        data = body(request)
        allowed = ["type", "title", "quantity", "reason", "status", "admin_note"]
        updates = {field: str(data[field]).strip() for field in allowed if field in data}
        updates["updated_at"] = iso_now()
        if updates.get("status") in {"approved", "rejected", "issued"}:
            updates["reviewed_at"] = iso_now()
        items[requisition_id] = {**item, **updates}
        _save_tenant_extra_dict(tenant_id, "requisitions", items)
        return ok({"success": True, "message": "Requisition updated", "requisition": {"id": requisition_id, **items[requisition_id]}})
    if method(request, "DELETE"):
        items.pop(requisition_id, None)
        _save_tenant_extra_dict(tenant_id, "requisitions", items)
        return ok({"success": True, "message": "Requisition deleted"})
    return ok({"message": "Method not allowed"}, 405)


@csrf_exempt
@api_view(["GET", "PATCH"])
@tenant_required
def staff_tasks(request, ticket_id=None):
    member = _current_member_payload(request)
    member_id = member["id"]
    tenant_id = request.tenant["id"]
    if method(request, "GET") and not ticket_id:
        tasks = _assigned_staff_tasks(tenant_id, member_id)
        return as_collection_response(request, tasks)
    if not ticket_id:
        return ok({"message": "Task id is required"}, 400)
    ticket = ref(f"tenants/{tenant_id}/tickets/{ticket_id}").get()
    if not ticket or str(ticket.get("assigned_to") or "") != str(member_id):
        return ok({"message": "Assigned task not found"}, 404)
    data = body(request)
    updates = {}
    status = str(data.get("status") or "").strip()
    if status in {"pending", "in_progress", "complete", "bounced"}:
        updates["status"] = status
    if "bounce_reason" in data:
        updates["bounce_reason"] = str(data.get("bounce_reason") or "").strip()
        updates["bounced_at"] = iso_now()
        updates["status"] = "bounced"
    if "work_report" in data:
        updates["work_report"] = str(data.get("work_report") or "").strip()
        updates["reported_at"] = iso_now()
    if "work_image" in data:
        updates["work_image"] = str(data.get("work_image") or "").strip()
        updates["work_image_name"] = str(data.get("work_image_name") or "").strip()
        updates["work_image_uploaded_at"] = iso_now()
    if not updates:
        return ok({"message": "No task updates provided"}, 400)
    updates["updated_at"] = iso_now()
    ref(f"tenants/{tenant_id}/tickets/{ticket_id}").update(updates)
    return ok({"success": True, "message": "Task updated", "task": {"id": ticket_id, **ticket, **updates}})


@csrf_exempt
@api_view(["GET", "POST"])
@tenant_required
def staff_reports(request):
    tenant_id = request.tenant["id"]
    member = _current_member_payload(request)
    reports = _tenant_extra_dict(tenant_id, "staff_work_reports")
    if method(request, "GET"):
        mine = [{"id": report_id, **report} for report_id, report in reports.items() if str(report.get("staff_id") or "") == str(member["id"])]
        return as_collection_response(request, mine)
    data = body(request)
    report = str(data.get("report") or "").strip()
    if not report:
        return ok({"message": "Work report is required"}, 400)
    task_id = str(data.get("task_id") or "").strip()
    report_id = secrets.token_hex(8)
    item = {
        "task_id": task_id,
        "task_title": str(data.get("task_title") or "").strip(),
        "staff_id": member["id"],
        "staff_name": member["name"],
        "staff_role": member["role"],
        "report": report,
        "work_image": str(data.get("work_image") or "").strip(),
        "work_image_name": str(data.get("work_image_name") or "").strip(),
        "created_at": iso_now(),
    }
    reports[report_id] = item
    _save_tenant_extra_dict(tenant_id, "staff_work_reports", reports)
    if task_id:
        updates = {"work_report": report, "reported_at": iso_now(), "updated_at": iso_now()}
        if item["work_image"]:
            updates["work_image"] = item["work_image"]
            updates["work_image_name"] = item["work_image_name"]
            updates["work_image_uploaded_at"] = iso_now()
        ref(f"tenants/{tenant_id}/tickets/{task_id}").update(updates)
    return ok({"success": True, "message": "Work report submitted", "report": {"id": report_id, **item}}, 201)


@csrf_exempt
@api_view(["GET", "POST"])
@tenant_required
def staff_requisitions(request):
    if method(request, "GET"):
        member = _current_member_payload(request)
        items = _tenant_extra_dict(request.tenant["id"], "requisitions")
        mine = [{"id": item_id, **item} for item_id, item in items.items() if str(item.get("requested_by") or "") == str(member["id"])]
        return as_collection_response(request, mine)
    data = body(request)
    item_type = str(data.get("type") or data.get("item_type") or "").strip()
    title = str(data.get("title") or data.get("item") or "").strip()
    quantity = str(data.get("quantity") or "1").strip()
    if not item_type or not title:
        return ok({"message": "Requisition type and item are required"}, 400)
    member = _current_member_payload(request)
    tenant_id = request.tenant["id"]
    items = _tenant_extra_dict(tenant_id, "requisitions")
    new_id = secrets.token_hex(8)
    item = {
        "type": item_type,
        "title": title,
        "quantity": quantity,
        "reason": str(data.get("reason") or "").strip(),
        "status": "pending",
        "requested_by": member["id"],
        "requested_by_name": member["name"],
        "requested_by_role": member["role"],
        "created_at": iso_now(),
        "updated_at": iso_now(),
    }
    items[new_id] = item
    _save_tenant_extra_dict(tenant_id, "requisitions", items)
    return ok({"success": True, "message": "Requisition created", "requisition": {"id": new_id, **item}}, 201)


@csrf_exempt
@api_view(["GET", "POST"])
@tenant_required
def payments(request):
    if method(request, "GET"):
        ensure_expired_customer_invoices(request.tenant)
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
    daraja_method = selected_daraja_method(request.tenant, data.get("payment_method"))
    daraja_config = platform_daraja_config(request.tenant, daraja_method)
    daraja_source = daraja_config.get("daraja_credential_source") or "platform"
    collection_account = "tenant_daraja" if daraja_source == "tenant" else "platform_daraja"
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
            "provider": "mpesa",
            "payment_method": daraja_method,
            "collection_account": collection_account,
            "daraja_credential_source": daraja_source,
            "tenant_settlement_status": "not_required" if collection_account == "tenant_daraja" else "pending_payment",
            "tenant_payout": tenant_payout_details(request.tenant),
        }
    )
    try:
        checkout = initiate_daraja_payment(
            daraja_config,
            payment_ref.key,
            data.get("amount"),
            phone=phone,
            description=f"{data.get('package_name') or 'Internet'} payment",
            metadata={
                "customer_id": data.get("customer_id"),
                "customer_name": data.get("customer_name"),
                "package_name": data.get("package_name"),
                "service_type": data.get("service_type") or "pppoe",
            },
            payment_method=daraja_method,
        )
    except PaymentProviderError as exc:
        payment_ref.update({"status": "failed", "failed_at": iso_now(), "callback_result_desc": exc.detail})
        return ok({"success": False, "message": exc.public_message, "paymentId": payment_ref.key}, exc.status_code)
    payment_ref.update(
        {
            "daraja_checkout_request_id": checkout.get("checkout_request_id"),
            "daraja_merchant_request_id": checkout.get("merchant_request_id"),
            "checkout_requested_at": iso_now(),
        }
    )
    return ok({"success": True, "message": checkout.get("customer_message") or "Check your phone and enter your M-Pesa PIN to complete payment.", "paymentId": payment_ref.key, "provider": "mpesa", "checkoutRequestId": checkout.get("checkout_request_id")})


@csrf_exempt
@api_view(["POST"])
@tenant_required
def payment_mark_paid(request, payment_id):
    payment = ref(f"tenants/{request.tenant['id']}/payments/{payment_id}").get()
    if not payment:
        return ok({"message": "Payment not found"}, 404)
    payment_code = payment.get("payment_code") or f"CASH-{secrets.token_hex(4).upper()}"
    updates = {
        "status": "success",
        "provider": payment.get("provider") or "cash",
        "payment_code": payment_code,
        "paid_at": iso_now(),
        "callback_result_code": "manual",
        "callback_result_desc": "Marked as paid by operator",
    }
    ref(f"tenants/{request.tenant['id']}/payments/{payment_id}").update(updates)
    marked_invoice = mark_customer_invoice_paid(request.tenant["id"], payment.get("customer_id"), {"id": payment_id, **payment, **updates})
    try:
        activate_paid_access(request.tenant, payment_id, {**payment, **updates}, payment.get("phone"), payment_code)
    except Exception as exc:
        ref(f"tenants/{request.tenant['id']}/payments/{payment_id}").update({"access_status": "activation_failed", "callback_result_desc": str(exc)})
        return ok({"success": True, "message": "Payment marked paid, but router activation failed", "activation_error": str(exc)})
    return ok({"success": True, "message": "Payment marked as paid and access activated", "invoice": marked_invoice})


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
    amount_payable = float(package.get("amount_payable") or package.get("price") or 0)
    payment_ref = ref(f"tenants/{request.tenant['id']}/payments").push(
        {
            "customer_id": customer_id,
            "customer_name": customer.get("name"),
            "package_id": package_id or package.get("id"),
            "package_name": package.get("name"),
            "service_type": customer.get("service_type") or "pppoe",
            "amount": amount_payable,
            "amount_payable": amount_payable,
            "payment_code": f"MANUAL-{secrets.token_hex(4).upper()}",
            "phone": customer.get("phone"),
            "status": "success",
            "paid_at": iso_now(),
            "initiated_at": iso_now(),
            "provider": data.get("provider") or "cash",
            "source": "manual_renewal",
        }
    )
    mark_customer_invoice_paid(request.tenant["id"], customer_id, {"id": payment_ref.key, **payment_ref.instance.as_dict()})
    try:
        activate_paid_access(request.tenant, payment_ref.key, {**payment_ref.instance.as_dict(), "package_name": package.get("name")}, customer.get("phone"), payment_ref.instance.payment_code)
    except Exception as exc:
        payment_ref.update({"access_status": "activation_failed", "callback_result_desc": str(exc)})
        return ok({"success": True, "message": "Renewal saved, but router activation failed", "paymentId": payment_ref.key, "activation_error": str(exc)})
    return ok({"success": True, "message": "Customer renewed and access activated", "paymentId": payment_ref.key})


@csrf_exempt
@api_view(["GET", "POST", "PATCH"])
@tenant_required
def invoices(request, invoice_id=None):
    tenant_id = request.tenant["id"]
    if method(request, "GET") and not invoice_id:
        ensure_expired_customer_invoices(request.tenant)
        invoices_data = [{"id": item_id, **item} for item_id, item in _tenant_extra_dict(tenant_id, "invoices").items()]
        status_filter = request.GET.get("status")
        if status_filter and status_filter != "all":
            invoices_data = [item for item in invoices_data if item.get("status") == status_filter]
        return as_collection_response(request, invoices_data)
    if method(request, "POST") and not invoice_id:
        data = body(request)
        invoice = {
            "invoice_number": str(data.get("invoice_number") or _invoice_id()).strip(),
            "customer_id": str(data.get("customer_id") or "").strip(),
            "customer": str(data.get("customer") or data.get("customer_name") or "").strip(),
            "customer_name": str(data.get("customer_name") or data.get("customer") or "").strip(),
            "phone": str(data.get("phone") or "").strip(),
            "item": str(data.get("item") or "Internet subscription").strip(),
            "package_name": str(data.get("package_name") or "").strip(),
            "service_type": str(data.get("service_type") or "").strip(),
            "amount": float(data.get("amount") or 0),
            "due_at": str(data.get("due_at") or iso_now()).strip(),
            "status": str(data.get("status") or "sent").strip(),
            "reason": str(data.get("reason") or "manual").strip(),
            "created_at": iso_now(),
            "updated_at": iso_now(),
        }
        new_id = secrets.token_hex(8)
        invoices_data = _tenant_extra_dict(tenant_id, "invoices")
        invoices_data[new_id] = invoice
        _save_tenant_extra_dict(tenant_id, "invoices", invoices_data)
        return ok({"success": True, "message": "Invoice created", "invoice": {"id": new_id, **invoice}}, 201)
    if not invoice_id:
        return ok({"message": "Invoice id is required"}, 400)
    invoices_data = _tenant_extra_dict(tenant_id, "invoices")
    invoice = invoices_data.get(invoice_id)
    if not invoice:
        return ok({"message": "Invoice not found"}, 404)
    if method(request, "PATCH"):
        data = body(request)
        updates = {key: data[key] for key in ["customer", "customer_name", "phone", "item", "package_name", "service_type", "amount", "due_at", "status"] if key in data}
        if "amount" in updates:
            updates["amount"] = float(updates["amount"] or 0)
        updates["updated_at"] = iso_now()
        invoices_data[invoice_id] = {**invoice, **updates}
        _save_tenant_extra_dict(tenant_id, "invoices", invoices_data)
        return ok({"success": True, "message": "Invoice saved", "invoice": {"id": invoice_id, **invoice, **updates}})
    return ok({"message": "Method not allowed"}, 405)


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
    today = now.date()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    def is_daraja_payment(payment):
        method = str(payment.get("payment_method") or payment.get("method") or "").strip().lower()
        provider = str(payment.get("provider") or "").strip().lower()
        return (
            method in {"daraja_paybill", "daraja_buygoods"}
            or bool(payment.get("daraja_receipt_number") or payment.get("mpesa_receipt_number"))
            or bool(payment.get("daraja_checkout_request_id") or payment.get("daraja_merchant_request_id"))
            or (provider == "mpesa" and str(payment.get("collection_account") or "").strip().lower() == "platform_daraja")
        )

    paid_payments = [p for p in payments_data if p.get("status") == "success"]
    daraja_paid_payments = [p for p in paid_payments if is_daraja_payment(p)]
    revenue_this_month = sum(float(p.get("amount") or 0) for p in paid_payments if (payment_date(p) or now) >= month_start)
    revenue_today = sum(float(p.get("amount") or 0) for p in daraja_paid_payments if payment_date(p) and payment_date(p).date() == today)

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
        from billing_api.models import RadiusSession as RadiusSessionModel
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
        from billing_api.models import RadiusSession as RadiusSessionModel
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

    pppoe_customers = [c for c in customers_data if str(c.get("service_type") or "pppoe").lower() == "pppoe"]
    hotspot_customers = [c for c in customers_data if str(c.get("service_type") or "hotspot").lower() == "hotspot"]
    active_hotspot_users = [
        c for c in hotspot_customers
        if str(c.get("status") or "").lower() == "active"
    ]
    def snapshot_active_sessions(router_snapshot):
        migration = ((router_snapshot or {}).get("migration_export") or {})
        ppp_rows = migration.get("ppp_active_sessions") or []
        hotspot_rows = migration.get("hotspot_active_sessions") or []

        def numeric_bytes(item):
            total = 0
            for key in ("bytes_in", "bytes_out", "bytes-in", "bytes-out", "input_octets", "output_octets"):
                try:
                    total += int(float((item or {}).get(key) or 0))
                except (TypeError, ValueError):
                    pass
            return total

        items = [
            {
                "username": item.get("name") or item.get("user") or "-",
                "service_type": "pppoe",
                "address": item.get("address") or item.get("caller_id") or "",
                "mac_address": item.get("caller_id") or "",
                "uptime": item.get("uptime") or "",
                "data_used": numeric_bytes(item),
                "server": item.get("service") or "",
            }
            for item in ppp_rows
            if isinstance(item, dict)
        ] + [
            {
                "username": item.get("user") or item.get("name") or item.get("mac_address") or "-",
                "service_type": "hotspot",
                "address": item.get("address") or "",
                "mac_address": item.get("mac_address") or "",
                "uptime": item.get("uptime") or "",
                "data_used": numeric_bytes(item),
                "server": item.get("server") or "",
            }
            for item in hotspot_rows
            if isinstance(item, dict)
        ]
        items = sorted(items, key=lambda item: item["data_used"], reverse=True)
        return {"pppoe": len(ppp_rows), "hotspot": len(hotspot_rows), "total": len(items), "items": items}

    snapshot = request.tenant.get("mikrotik_router_snapshot") or {}
    router_sample_source = "provisioning_snapshot"
    try:
        if has_mikrotik_credentials(request.tenant):
            snapshot = router_interface_status(request.tenant)
            router_sample_source = "routeros_api"
    except Exception as exc:
        logger.warning("Dashboard live MikroTik sample failed tenant=%s error=%s", tenant_id, exc)
    device = snapshot.get("device") or {}
    cpu_load = device.get("cpu_load")
    last_seen = parse_date(request.tenant.get("mikrotik_last_seen_at"))
    agent_online = bool(last_seen and utcnow() - last_seen <= timedelta(minutes=3))
    router_status = "suspended" if request.tenant.get("mikrotik_router_suspended") else "online" if router_sample_source == "routeros_api" or agent_online else "offline"
    active_ratio = (len([c for c in customers_data if c.get("status") == "active"]) / len(customers_data) * 100) if customers_data else 0
    traffic = snapshot.get("traffic") or {}
    traffic_bps = int(traffic.get("rx_bps") or 0) + int(traffic.get("tx_bps") or 0)
    signal_values = [
        int(item.get("signal_strength"))
        for item in snapshot.get("interfaces", [])
        if str(item.get("signal_strength") or "").lstrip("-").isdigit()
    ]
    if signal_values:
        strongest_signal = max(signal_values)
        internet_strength_percent = max(0, min(100, round((strongest_signal + 90) / 40 * 100)))
        internet_strength_source = "wireless_signal"
    else:
        internet_strength_source = "router_link"
        enabled_interfaces = [
            item for item in snapshot.get("interfaces", [])
            if not item.get("disabled")
        ]
        running_interfaces = [
            item for item in enabled_interfaces
            if item.get("running")
        ]
        internet_strength_percent = round((len(running_interfaces) / len(enabled_interfaces)) * 100) if enabled_interfaces else (100 if router_sample_source == "routeros_api" else 0)
    traffic_percent = round(min((traffic_bps / 1_000_000), 100), 1) if traffic_bps and router_status == "online" else 0
    active_sessions = snapshot.get("active_sessions") if isinstance(snapshot.get("active_sessions"), dict) else snapshot_active_sessions(snapshot)
    active_session_items = active_sessions.get("items") if isinstance(active_sessions, dict) else []
    active_session_usernames = {
        str(item.get("username") or "").strip().lower()
        for item in (active_session_items or [])
        if isinstance(item, dict) and str(item.get("username") or "").strip()
    }
    radius_active_session_count = 0
    radius_active_session_items = []
    try:
        from billing_api.models import RadiusSession as RadiusSessionModel

        radius_active = RadiusSessionModel.objects.filter(tenant_id=tenant_id, stopped_at__isnull=True)
        radius_active_session_count = radius_active.count()
        active_session_usernames.update(
            str(username or "").strip().lower()
            for username in radius_active.values_list("customer__username", flat=True)
            if str(username or "").strip()
        )
        radius_active_session_items = [
            {
                "username": session.customer.username,
                "service_type": session.service_type or session.customer.service_type,
                "address": session.framed_ip or session.nas_ip,
                "mac_address": "",
                "uptime": str(now - session.started_at).split(".", 1)[0] if session.started_at else "",
                "data_used": float((session.input_octets or 0) + (session.output_octets or 0)),
                "server": session.nas_ip,
            }
            for session in radius_active.select_related("customer").order_by("-started_at")[:5]
        ]
    except Exception:
        pass
    router_active_session_count = int((active_sessions or {}).get("total") or len(active_session_items or [])) if isinstance(active_sessions, dict) else 0
    connected_users_count = len(active_session_usernames) or max(router_active_session_count, radius_active_session_count)
    top_active_sessions = [
        {
            "username": item.get("username") or "-",
            "service_type": item.get("service_type") or "",
            "address": item.get("address") or "",
            "mac_address": item.get("mac_address") or "",
            "uptime": item.get("uptime") or "",
            "data_used": float(item.get("data_used") or 0),
            "server": item.get("server") or "",
        }
        for item in (active_session_items or [])[:5]
        if isinstance(item, dict)
    ] or radius_active_session_items

    return ok(
        {
            "summary": {
                "revenue_this_month": round(revenue_this_month, 2),
                "revenue_today": round(revenue_today, 2),
                "sms_balance": float(request.tenant.get("sms_balance") or 0),
                "total_customers": len(customers_data),
                "pppoe_customers": len(pppoe_customers),
                "hotspot_customers": len(hotspot_customers),
                "active_customers": connected_users_count,
                "enabled_customers": len([c for c in customers_data if c.get("status") == "active"]),
                "daraja_revenue_today": round(revenue_today, 2),
            },
            "router_health": {
                "status": router_status,
                "board_name": device.get("board_name") or request.tenant.get("mikrotik_detected_board"),
                "cpu_load_percent": cpu_load,
                "internet_strength_percent": internet_strength_percent,
                "internet_strength_source": internet_strength_source,
                "traffic_percent": traffic_percent,
                "network_traffic_bps": traffic_bps if router_status == "online" else 0,
                "network_rx_bps": traffic.get("rx_bps") if router_status == "online" else 0,
                "network_tx_bps": traffic.get("tx_bps") if router_status == "online" else 0,
                "active_sessions": active_sessions,
                "top_active_sessions": top_active_sessions,
                "sample_source": router_sample_source,
                "sampled_at": traffic.get("sampled_at") or iso_now(),
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
            "top_hotspot_active_users": sorted(
                [
                    {
                        "username": c.get("username") or c.get("phone"),
                        "phone": c.get("phone"),
                        "data_used": radius_data_usage.get(
                            c.get("username"),
                            float(c.get("data_used") or c.get("data_usage") or 0),
                        ),
                    }
                    for c in active_hotspot_users
                ],
                key=lambda item: item["data_used"],
                reverse=True,
            )[:5],
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
        "payout_phone",
        "bank_code",
        "bank_name",
        "bank_account_number",
        "payment_methods",
        "payment_provider",
        "daraja_consumer_key",
        "daraja_consumer_secret",
        "daraja_shortcode",
        "daraja_passkey",
        "daraja_till_number",
        "daraja_shortcode_type",
        "daraja_environment",
    ]
    updates = {}
    for field in allowed:
        if field in data:
            if field == "dark_mode":
                updates[field] = bool(data[field])
            elif field == "payment_methods":
                requested = data[field] if isinstance(data[field], list) else [data[field]]
                normalized_methods = []
                for item in requested:
                    value = str(item).strip().lower()
                    if value in {"paybill", "mpesa_paybill"}:
                        value = "daraja_paybill"
                    elif value in {"buygoods", "buy_goods", "mpesa_buygoods"}:
                        value = "daraja_buygoods"
                    if value in {"daraja_paybill", "daraja_buygoods"}:
                        normalized_methods.append(value)
                updates[field] = normalized_methods or ["daraja_paybill"]
            elif field == "payment_provider":
                updates[field] = "mpesa"
            elif field == "daraja_shortcode_type":
                value = str(data[field] or "").strip()
                updates[field] = value if value in {"CustomerPayBillOnline", "CustomerBuyGoodsOnline"} else "CustomerPayBillOnline"
            elif field == "daraja_environment":
                value = str(data[field] or "production").strip().lower()
                updates[field] = "sandbox" if value == "sandbox" else "production"
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
    updates["payment_provider"] = "mpesa"
    updates["settlement_status"] = "ready" if tenant_payout_details(merged).get("payout_phone") else "missing_payout_details"

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
    channel = str(data.get("channel") or "whatsapp").strip().lower()
    test_message = str(data.get("message") or "This is a test message from Expressnet.").strip()
    if channel == "sms":
        result = send_sms_message(phone, test_message, request.tenant)
    else:
        result = send_whatsapp_message(phone, test_message, request.tenant)
    if result.get("sent"):
        return ok({"success": True, "message": f"Test {channel} sent", "phone": phone, "result": result})
    return ok({"success": False, "message": result.get("skipped") or result.get("error") or f"Failed to send test {channel}", "phone": phone, "result": result}, 400)


@csrf_exempt
@api_view(["POST"])
@tenant_required
def settings_test_whatsapp(request):
    data = body(request)
    phone = normalize_phone(data.get("phone") or request.tenant.get("phone"))
    if not phone:
        return ok({"message": "Phone number is required to test WhatsApp notifications"}, 400)
    test_message = str(data.get("message") or "This is a test WhatsApp notification from Expressnet.").strip()
    tenant = {
        **request.tenant,
        "notification_provider": str(data.get("provider") or request.tenant.get("notification_provider") or "slek").strip(),
        "whatsapp_enabled": data.get("whatsapp_enabled") is not False,
    }
    apiwap_api_key = str(data.get("apiwap_api_key") or "").strip()
    if apiwap_api_key and apiwap_api_key not in {MASKED, "********", "••••••••"}:
        tenant["apiwap_api_key"] = apiwap_api_key
    if data.get("apiwap_base_url"):
        tenant["apiwap_base_url"] = str(data.get("apiwap_base_url")).strip()
    result = send_whatsapp_message(phone, test_message, tenant)
    if result.get("sent"):
        return ok({"success": True, "message": "Test WhatsApp notification sent", "phone": phone, "result": result})
    reason = result.get("skipped") or result.get("error") or "Failed to send test WhatsApp notification"
    return ok({"success": False, "message": reason, "phone": phone, "result": result}, 400)


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
                "assigned_to": str(data.get("assigned_to") or "").strip(),
                "assigned_to_name": str(data.get("assigned_to_name") or "").strip(),
                "assigned_to_role": str(data.get("assigned_to_role") or "").strip(),
                "mikrotik_id": str(data.get("mikrotik_id") or "").strip(),
                "mikrotik_name": str(data.get("mikrotik_name") or "").strip(),
                "status": data.get("status") or "open",
                "priority": data.get("priority") or "medium",
                "created_at": iso_now(),
                "updated_at": iso_now(),
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
        allowed = ["title", "description", "customer_id", "assigned_to", "assigned_to_name", "assigned_to_role", "mikrotik_id", "mikrotik_name", "status", "priority"]
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
                "provider": request.tenant.get("notification_provider") or "slek",
                "sms_enabled": request.tenant.get("sms_enabled") is not False,
                "sms_on_maintenance": request.tenant.get("sms_on_maintenance") is not False,
                "sms_on_promotions": request.tenant.get("sms_on_promotions") is not False,
                "sms_on_payment": request.tenant.get("sms_on_payment") is not False,
                "whatsapp_on_customer_created": request.tenant.get("whatsapp_on_customer_created") is not False,
                "whatsapp_on_expiry": request.tenant.get("whatsapp_on_expiry") is not False,
                "sms_template_maintenance": request.tenant.get("sms_template_maintenance") or "We will be performing scheduled maintenance. Thank you for your patience.",
                "sms_template_promotion": request.tenant.get("sms_template_promotion") or "Special offer from {{business}}: {{message}}",
                "sms_template_hotspot": strip_customer_template_tokens(request.tenant.get("sms_template_hotspot") or "Your hotspot package is active."),
                "sms_template_pppoe": strip_customer_template_tokens(request.tenant.get("sms_template_pppoe") or "Your PPPoE package is active."),
                "sms_balance": int(request.tenant.get("sms_balance") or 0),
                "sms_sent_count": int(request.tenant.get("sms_sent_count") or 0),
                "whatsapp_enabled": request.tenant.get("whatsapp_enabled") is not False,
                "roamtech_sender_id": request.tenant.get("roamtech_sender_id") or "",
                "apiwap_base_url": request.tenant.get("apiwap_base_url") or "https://api.apiwap.com/api/v1",
                "has_apiwap_api_key": bool(request.tenant.get("apiwap_api_key")),
                "customer_created_whatsapp_template": strip_customer_template_tokens(request.tenant.get("customer_created_whatsapp_template") or "Your internet account has been created successfully."),
                "payment_sms_template": strip_customer_template_tokens(request.tenant.get("payment_sms_template") or "Your payment is confirmed."),
                "payment_whatsapp_template": strip_customer_template_tokens(request.tenant.get("payment_whatsapp_template") or "Your internet package is active. Thank you for your payment."),
                "expiry_whatsapp_template": strip_customer_template_tokens(request.tenant.get("expiry_whatsapp_template") or "Your internet package is about to expire. Please renew to stay connected."),
            }
        )
    data = body(request)
    updates = {
        "notification_provider": str(data.get("provider") or "slek").strip(),
        "sms_enabled": data.get("sms_enabled") is not False,
        "sms_on_maintenance": data.get("sms_on_maintenance") is not False,
        "sms_on_promotions": data.get("sms_on_promotions") is not False,
        "sms_on_payment": data.get("sms_on_payment") is not False,
        "whatsapp_on_customer_created": data.get("whatsapp_on_customer_created") is not False,
        "whatsapp_on_expiry": data.get("whatsapp_on_expiry") is not False,
        "sms_template_maintenance": str(data.get("sms_template_maintenance") or "").strip(),
        "sms_template_promotion": str(data.get("sms_template_promotion") or "").strip(),
        "sms_template_hotspot": strip_customer_template_tokens(data.get("sms_template_hotspot") or ""),
        "sms_template_pppoe": strip_customer_template_tokens(data.get("sms_template_pppoe") or ""),
        "whatsapp_enabled": data.get("whatsapp_enabled") is not False,
        "roamtech_sender_id": str(data.get("roamtech_sender_id") or "").strip(),
        "apiwap_base_url": str(data.get("apiwap_base_url") or "https://api.apiwap.com/api/v1").strip(),
        "customer_created_whatsapp_template": strip_customer_template_tokens(data.get("customer_created_whatsapp_template") or ""),
        "payment_sms_template": strip_customer_template_tokens(data.get("payment_sms_template") or ""),
        "payment_whatsapp_template": strip_customer_template_tokens(data.get("payment_whatsapp_template") or ""),
        "expiry_whatsapp_template": strip_customer_template_tokens(data.get("expiry_whatsapp_template") or ""),
        "notifications_updated_at": iso_now(),
    }
    apiwap_api_key = str(data.get("apiwap_api_key") or "").strip()
    if apiwap_api_key and apiwap_api_key not in {MASKED, "********", "••••••••"}:
        updates["apiwap_api_key"] = apiwap_api_key
    ref(f"tenants/{request.tenant['id']}").update(updates)
    try:
        tenant_obj = Tenant.objects.get(pk=request.tenant["id"])
        tenant_obj.apply_data(updates)
        tenant_obj.save()
    except Tenant.DoesNotExist:
        logger.warning("Notification settings saved to Firebase only; tenant model not found tenant=%s", request.tenant["id"])
    saved = {**request.tenant, **updates}
    return ok({
        "success": True,
        "message": "Notification settings saved",
        "config": {key: value for key, value in updates.items() if key != "apiwap_api_key"},
        "provider": saved.get("notification_provider") or "slek",
        "whatsapp_enabled": saved.get("whatsapp_enabled") is not False,
        "has_apiwap_api_key": bool(saved.get("apiwap_api_key")),
        "apiwap_base_url": saved.get("apiwap_base_url") or "https://api.apiwap.com/api/v1",
    })


def to_access_username(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def _payment_customer_name(payment, phone, username):
    return str(phone or username or "Customer").strip()


def _upsert_sql_customer(tenant_id, data):
    try:
        tenant_obj = Tenant.objects.get(pk=tenant_id)
    except Tenant.DoesNotExist:
        logger.warning("Paid access SQL customer sync skipped; tenant model not found tenant=%s", tenant_id)
        return None

    username = str(data.get("username") or "").strip()
    phone = str(data.get("phone") or "").strip()
    customer = None
    if username:
        customer = Customer.objects.filter(tenant=tenant_obj, username=username).first()
    if not customer and phone:
        customer = Customer.objects.filter(tenant=tenant_obj, phone=phone).first()
    if not customer:
        customer = Customer(tenant=tenant_obj)

    customer.apply_data(data)
    if not customer.radius_secret:
        customer.radius_secret = customer.password or ""
    customer.save()
    return customer


def render_notification_template(template, context):
    rendered = str(template or "")
    for key, value in context.items():
        rendered = rendered.replace("{{" + key + "}}", str(value if value is not None else ""))
    return rendered


def strip_customer_template_tokens(template):
    rendered = str(template or "")
    for token in ["name", "username", "package", "amount_payable", "password", "amount", "expires_at"]:
        rendered = rendered.replace("{{" + token + "}}", "")
    return " ".join(rendered.split()).strip()


def append_payment_access_details(message, context):
    base = str(message or "").strip() or "Your internet package is active."
    details = (
        f"Name: {context.get('name') or 'customer'}. "
        f"Package: {context.get('package') or ''}. "
        f"Amount: Ksh {context.get('amount_payable') or context.get('amount') or ''}. "
        f"Username: {context.get('username') or ''}. "
        f"Password: {context.get('password') or ''}."
    )
    return f"{base} {details}"


def append_expiry_details(message, context):
    base = str(message or "").strip() or "Your internet package is about to expire."
    details = (
        f"Name: {context.get('name') or 'customer'}. "
        f"Package: {context.get('package') or ''}. "
        f"Username: {context.get('username') or ''}. "
        f"Expires: {context.get('expires_at') or ''}."
    )
    return f"{base} {details}"


def append_customer_created_details(message, context):
    service_label = str(context.get("service_type") or "internet").upper()
    base = str(message or "").strip() or f"Your {service_label} account has been created."
    details = (
        f"Name: {context.get('name') or 'customer'}. "
        f"Package: {context.get('package') or ''}. "
        f"Amount payable: Ksh {context.get('amount_payable') or ''}. "
        "Your technician will assist with setup."
    )
    return f"{base} {details}"


def append_technician_credentials_details(message, context):
    service_label = str(context.get("service_type") or "internet").upper()
    base = str(message or "").strip() or f"A {service_label} customer account has been created."
    details = (
        f"Customer: {context.get('name') or 'customer'}. "
        f"Phone: {context.get('phone') or ''}. "
        f"Package: {context.get('package') or ''}. "
        f"Amount payable: Ksh {context.get('amount_payable') or ''}. "
        f"Username: {context.get('username') or ''}. "
        f"Password: {context.get('password') or ''}."
    )
    return f"{base} {details}"


def notify_customer_created(tenant, customer):
    service_type = str((customer or {}).get("service_type") or "").strip().lower()
    if service_type not in {"pppoe", "static"}:
        return {"whatsapp": {"sent": False, "skipped": "unsupported_service_type"}}
    if (tenant or {}).get("whatsapp_enabled") is False or (tenant or {}).get("whatsapp_on_customer_created") is False:
        return {"whatsapp": {"sent": False, "skipped": "disabled"}}
    technician = _find_team_member_by_label(tenant, customer.get("technician"))
    context = {
        "name": customer.get("name") or customer.get("phone") or "customer",
        "phone": customer.get("phone") or "",
        "package": customer.get("package") or customer.get("package_name") or "",
        "service_type": service_type,
        "amount_payable": customer.get("amount_payable") or "",
        "username": customer.get("username") or "",
        "password": customer.get("password") or "",
    }
    template = (tenant or {}).get("customer_created_whatsapp_template") or f"Your {service_type.upper()} internet account has been created."
    technician_template = (tenant or {}).get("technician_customer_credentials_template") or f"A {service_type.upper()} customer account has been created."
    results = {
        "customer_whatsapp": send_whatsapp_message(
            customer.get("phone"),
            append_customer_created_details(strip_customer_template_tokens(template), context),
            tenant,
            recipient_name=context["name"],
        )
    }
    if technician and technician.get("phone"):
        results["technician_whatsapp"] = send_whatsapp_message(
            technician.get("phone"),
            append_technician_credentials_details(strip_customer_template_tokens(technician_template), context),
            tenant,
            recipient_name=technician.get("name") or "Technician",
        )
    else:
        results["technician_whatsapp"] = {"sent": False, "skipped": "missing_technician_phone"}
    results["whatsapp"] = results["customer_whatsapp"]
    return results


def _invoice_id():
    return f"INV-{timezone.now().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def create_customer_invoice(tenant, customer, amount, reason="subscription_due", due_at=None):
    tenant_id = tenant.get("id")
    if not tenant_id:
        return None
    invoice_id = secrets.token_hex(8)
    invoice = {
        "invoice_number": _invoice_id(),
        "customer_id": customer.get("id"),
        "customer": customer.get("name") or customer.get("phone") or "Customer",
        "customer_name": customer.get("name") or customer.get("phone") or "Customer",
        "phone": customer.get("phone") or "",
        "item": f"{customer.get('package') or 'Internet'} subscription",
        "package_name": customer.get("package") or "",
        "service_type": customer.get("service_type") or "",
        "amount": float(amount or 0),
        "due_at": due_at or iso_now(),
        "status": "sent",
        "reason": reason,
        "created_at": iso_now(),
        "updated_at": iso_now(),
    }
    invoices_data = _tenant_extra_dict(tenant_id, "invoices")
    invoices_data[invoice_id] = invoice
    _save_tenant_extra_dict(tenant_id, "invoices", invoices_data)
    return {"id": invoice_id, **invoice}


def mark_customer_invoice_paid(tenant_id, customer_id, payment):
    if not customer_id:
        return None
    invoices = [{"id": invoice_id, **invoice} for invoice_id, invoice in _tenant_extra_dict(tenant_id, "invoices").items()]
    open_invoice = next(
        (
            invoice for invoice in sorted(invoices, key=lambda item: item.get("created_at") or "", reverse=True)
            if str(invoice.get("customer_id") or "") == str(customer_id) and invoice.get("status") != "paid"
        ),
        None,
    )
    if not open_invoice:
        return None
    updates = {
        "status": "paid",
        "paid_at": payment.get("paid_at") or iso_now(),
        "payment_id": payment.get("id"),
        "payment_code": payment.get("payment_code"),
        "updated_at": iso_now(),
    }
    invoices_data = _tenant_extra_dict(tenant_id, "invoices")
    invoices_data[open_invoice["id"]] = {**invoices_data.get(open_invoice["id"], {}), **updates}
    _save_tenant_extra_dict(tenant_id, "invoices", invoices_data)
    return {"id": open_invoice["id"], **open_invoice, **updates}


def ensure_expired_customer_invoices(tenant):
    tenant_id = tenant.get("id")
    if not tenant_id:
        return []
    now = timezone.now()
    created = []
    for customer in list_children(f"tenants/{tenant_id}/customers"):
        if str(customer.get("service_type") or "").strip().lower() not in {"pppoe", "static"}:
            continue
        expiry_raw = customer.get("expiry_date") or customer.get("expires_at")
        if not expiry_raw:
            continue
        expiry = parse_datetime(str(expiry_raw))
        if not expiry:
            continue
        if expiry.tzinfo is None:
            expiry = timezone.make_aware(expiry, timezone.get_current_timezone())
        if expiry > now:
            continue
        marker = f"expiry_invoice_{expiry.date().isoformat()}"
        if customer.get(marker):
            continue
        amount_due = customer.get("amount_payable")
        if amount_due in {None, ""}:
            pkg = find_child_by_field(f"tenants/{tenant_id}/packages", "name", customer.get("package"))
            amount_due = (pkg or {}).get("amount_payable") or (pkg or {}).get("price") or 0
        invoice = create_customer_invoice(tenant, customer, amount_due, reason="subscription_expired", due_at=expiry.isoformat())
        if invoice:
            ref(f"tenants/{tenant_id}/customers/{customer['id']}").update({marker: invoice["id"], "last_expiry_invoice_at": iso_now()})
            created.append(invoice)
    return created


def notify_payment_access(tenant, payment, access):
    amount_payable = payment.get("amount_payable") or payment.get("amount") or ""
    notification_context = {
        "name": payment.get("customer_name") or payment.get("phone") or "customer",
        "package": payment.get("package_name") or "",
        "amount": payment.get("amount") or "",
        "amount_payable": amount_payable,
        "username": access.get("username") or access.get("mac_address") or "",
        "password": access.get("password") or "",
        "expires_at": access.get("expiry_date") or "",
    }
    template = (tenant or {}).get("payment_sms_template") or "Your payment is confirmed."
    message = append_payment_access_details(strip_customer_template_tokens(template), notification_context)
    results = {}
    if (tenant or {}).get("sms_on_payment") is not False:
        balance = int((tenant or {}).get("sms_balance") or 0)
        if balance <= 0:
            results["sms"] = {"sent": False, "skipped": "sms_balance_empty"}
        else:
            results["sms"] = send_sms_message(payment.get("phone"), message, tenant)
            if results["sms"].get("sent"):
                tenant_id = tenant.get("id")
                if tenant_id:
                    ref(f"tenants/{tenant_id}").update({"sms_balance": balance - 1, "sms_sent_count": int((tenant or {}).get("sms_sent_count") or 0) + 1})
    if (tenant or {}).get("whatsapp_enabled") is not False:
        whatsapp_template = (tenant or {}).get("payment_whatsapp_template") or message
        customer_name = notification_context["name"]
        results["whatsapp"] = send_whatsapp_message(
            payment.get("phone"),
            append_payment_access_details(strip_customer_template_tokens(whatsapp_template), notification_context),
            tenant,
            recipient_name=customer_name,
        )
    return results


def notify_package_expiry(tenant, customer):
    if (tenant or {}).get("whatsapp_enabled") is False or (tenant or {}).get("whatsapp_on_expiry") is False:
        return {"whatsapp": {"sent": False, "skipped": "disabled"}}
    context = {
        "name": customer.get("name") or customer.get("phone") or "customer",
        "package": customer.get("package") or "",
        "username": customer.get("username") or "",
        "expires_at": customer.get("expiry_date") or customer.get("expires_at") or "",
    }
    template = (tenant or {}).get("expiry_whatsapp_template") or "Your internet package is about to expire. Please renew to stay connected."
    message = append_expiry_details(strip_customer_template_tokens(template), context)
    return {
        "whatsapp": send_whatsapp_message(
            customer.get("phone"),
            message,
            tenant,
            recipient_name=context["name"],
        )
    }


@csrf_exempt
@api_view(["POST"])
@tenant_required
def send_expiry_notifications(request):
    data = body(request)
    hours = int(data.get("hours") or 24)
    now = timezone.now()
    due_at = now + timedelta(hours=hours)
    sent = []
    skipped = []
    invoices_created = []
    for customer in list_children(f"tenants/{request.tenant['id']}/customers"):
        expiry_raw = customer.get("expiry_date") or customer.get("expires_at")
        if not expiry_raw:
            continue
        expiry = parse_datetime(str(expiry_raw))
        if not expiry:
            continue
        if expiry.tzinfo is None:
            expiry = timezone.make_aware(expiry, timezone.get_current_timezone())
        if expiry > due_at:
            continue
        invoice_marker = f"expiry_invoice_{expiry.date().isoformat()}"
        if expiry <= now and not customer.get(invoice_marker):
            amount_due = customer.get("amount_payable")
            if amount_due in {None, ""}:
                pkg = find_child_by_field(f"tenants/{request.tenant['id']}/packages", "name", customer.get("package"))
                amount_due = (pkg or {}).get("amount_payable") or (pkg or {}).get("price") or 0
            invoice = create_customer_invoice(request.tenant, customer, amount_due, reason="subscription_expired", due_at=expiry.isoformat())
            if invoice:
                ref(f"tenants/{request.tenant['id']}/customers/{customer['id']}").update({invoice_marker: invoice["id"], "last_expiry_invoice_at": iso_now()})
                invoices_created.append(invoice)
        marker = f"expiry_whatsapp_notice_{expiry.date().isoformat()}"
        if customer.get(marker):
            skipped.append({"customer_id": customer.get("id"), "reason": "already_notified"})
            continue
        result = notify_package_expiry(request.tenant, customer)
        if result.get("whatsapp", {}).get("sent"):
            ref(f"tenants/{request.tenant['id']}/customers/{customer['id']}").update({marker: iso_now(), "last_expiry_notification_at": iso_now()})
            sent.append({"customer_id": customer.get("id"), "phone": customer.get("phone")})
        else:
            skipped.append({"customer_id": customer.get("id"), "phone": customer.get("phone"), "result": result})
    return ok({"success": True, "message": f"Sent {len(sent)} expiry WhatsApp notifications", "sent": sent, "skipped": skipped, "invoices_created": invoices_created})


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
    duration = package_duration_delta(pkg)
    duration_seconds = int(duration.total_seconds())
    expiry = package_expiry_date(utcnow(), pkg)
    router_client_mac = normalize_mac(payment.get("router_client_mac") or payment.get("router_mac"))
    router_client_ip = str(payment.get("router_client_ip") or payment.get("ip") or "").strip()
    mac_address = normalize_mac(payment.get("mac_address") or (router_client_mac if service_type == "hotspot" else "") or (customer or {}).get("mac_address"))
    username = mac_address if service_type == "tv" else (payment.get("username") or (customer or {}).get("username") or to_access_username(phone))
    password = str(payment.get("pending_access_password") or payment.get("access_password") or (customer or {}).get("password") or payment_code)
    customer_name = _payment_customer_name({**(customer or {}), **payment}, phone, username)
    if customer:
        updates = {"name": customer_name, "phone": phone, "username": username, "password": password, "package": package_for_access, "service_type": service_type, "status": "active", "expiry_date": expiry.isoformat(), "last_payment_id": payment_id, "last_payment_code": payment_code, "auto_reconnect": True, "updated_at": iso_now()}
        if mac_address:
            updates["mac_address"] = mac_address
        ref(f"tenants/{tenant_id}/customers/{customer['id']}").update(updates)
        customer_id = customer["id"]
    else:
        new_ref = ref(f"tenants/{tenant_id}/customers").push({"name": customer_name, "phone": phone, "username": username, "password": password, "package": package_for_access, "service_type": service_type, "status": "active", "expiry_date": expiry.isoformat(), "last_payment_id": payment_id, "last_payment_code": payment_code, "auto_reconnect": True, "mac_address": mac_address, "created_at": iso_now()})
        customer_id = new_ref.key
    if service_type == "tv" and not mac_address:
        raise ValueError("TV MAC address is required for activation")
    sql_customer = None
    try:
        sql_customer = _upsert_sql_customer(
            tenant_id,
            {
                "name": customer_name,
                "phone": phone,
                "username": username,
                "password": password,
                "radius_secret": password,
                "package": package_for_access,
                "service_type": service_type,
                "status": "active",
                "expiry_date": expiry.isoformat(),
                "auto_reconnect": True,
                "last_payment_id": payment_id,
                "last_payment_code": payment_code,
                "mac_address": mac_address,
                "router_client_mac": router_client_mac,
                "router_client_ip": router_client_ip,
            },
        )
    except Exception as exc:
        logger.warning("Paid access SQL customer sync failed tenant=%s payment=%s username=%s error=%s", tenant_id, payment_id, username, exc, exc_info=True)
    if tenant.get("radius_enabled") and service_type in {"hotspot", "pppoe"}:
        try:
            from billing_api.radius_provisioning import sync_radius_customer, upsert_pg_customer

            tenant_obj = Tenant.objects.get(pk=tenant_id)
            pg_customer = sql_customer or upsert_pg_customer(tenant_obj, {"name": customer_name, "phone": phone, "username": username, "password": password, "package": package_for_access, "service_type": service_type, "status": "active", "expiry_date": expiry.isoformat(), "last_payment_id": payment_id, "last_payment_code": payment_code})
            sync_radius_customer(tenant_obj, pg_customer or {"username": username, "password": password})
            logger.info(
                "Paid access prepared for RADIUS tenant=%s payment=%s username=%s service=%s package=%s expires_at=%s",
                tenant_id,
                payment_id,
                username,
                service_type,
                package_for_access,
                expiry.isoformat(),
            )
        except Exception as exc:
            logger.warning("Paid access RADIUS sync failed tenant=%s payment=%s username=%s error=%s", tenant_id, payment_id, username, exc, exc_info=True)
            ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"radius_status": "failed", "radius_error": str(exc)})
    router_access_status = "active"
    router_access_error = None
    access_payload = {
        "username": username,
        "password": password,
        "package_name": package_for_access,
        "package": package_for_access,
        "service_type": service_type,
        "mac_address": mac_address,
        "router_client_mac": router_client_mac,
        "router_client_ip": router_client_ip,
        "duration_seconds": duration_seconds,
        "expires_at": expiry.isoformat(),
        "status": "active",
        "speed": (pkg or {}).get("speed"),
    }
    if has_mikrotik_credentials(tenant):
        try:
            if service_type == "hotspot" and pkg:
                create_hotspot_profile(tenant, pkg["name"], pkg.get("speed"), duration_seconds)
            if service_type == "pppoe" and pkg:
                create_ppp_profile(tenant, pkg["name"], pkg.get("speed"), duration_seconds)
            upsert_customer_access(tenant, access_payload)
            set_customer_enabled(tenant, username, service_type, True)
            router_access_status = "active"
        except Exception as exc:
            if _router_is_agent_linked(tenant):
                _queue_router_command_for_tenant(tenant_id, {"type": "sync_secrets", "script": _customer_secret_script(access_payload)})
                router_access_status = "queued"
                router_access_error = str(exc)
            else:
                raise
    elif _router_is_agent_linked(tenant):
        _queue_router_command_for_tenant(tenant_id, {"type": "sync_secrets", "script": _customer_secret_script(access_payload)})
        router_access_status = "queued"
    elif tenant.get("radius_enabled") and service_type in {"hotspot", "pppoe"}:
        router_access_status = "radius_ready"
        router_access_error = None
        logger.info(
            "Paid access activation prepared for RADIUS tenant=%s payment=%s username=%s; no direct router channel is available for local user sync",
            tenant_id,
            payment_id,
            username,
        )
    else:
        router_access_status = "pending"
        router_access_error = "No linked MikroTik router"
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"customer_id": customer_id, "access_username": username, "access_password": password, "access_mac_address": mac_address, "access_expires_at": expiry.isoformat(), "access_status": router_access_status, "access_error": router_access_error, "auto_reconnect": True})
    access = {"username": username, "password": password, "mac_address": mac_address, "expiry_date": expiry.isoformat()}
    try:
        notify_result = notify_payment_access(tenant, {**payment, "phone": phone, "package_name": package_for_access}, access)
        if notify_result:
            sent_channels = [name for name, result in notify_result.items() if result.get("sent")]
            skipped = "; ".join(f"{name}: {result.get('skipped')}" for name, result in notify_result.items() if result.get("skipped"))
            ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"notification_status": "sent" if sent_channels else "skipped", "notification_channels": sent_channels, "notification_detail": skipped})
    except Exception as exc:
        ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"whatsapp_status": "failed", "whatsapp_detail": str(exc)})
    return access


def complete_daraja_payment(tenant_id, payment_id, callback_metadata, amount, receipt, paid_at, phone):
    payment = ref(f"tenants/{tenant_id}/payments/{payment_id}").get()
    if not payment:
        logger.warning("Daraja callback payment not found tenant=%s payment=%s receipt=%s", tenant_id, payment_id, receipt)
        return False
    if payment.get("status") == "success":
        if not payment.get("access_username"):
            tenant_data = ref(f"tenants/{tenant_id}").get() or {}
            activate_paid_access({"id": tenant_id, **tenant_data}, payment_id, {**payment, **callback_metadata}, phone or payment.get("phone"), receipt or payment.get("payment_code") or payment_id)
        return True

    expected_amount = round(float(payment.get("amount") or 0), 2)
    paid_amount = round(float(amount or 0), 2)
    if expected_amount and paid_amount != expected_amount:
        logger.warning(
            "Daraja callback amount mismatch tenant=%s payment=%s expected=%s paid=%s receipt=%s",
            tenant_id,
            payment_id,
            expected_amount,
            paid_amount,
            receipt,
        )
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
        "collection_account": payment.get("collection_account") or "platform_daraja",
        "tenant_settlement_status": payment.get("tenant_settlement_status") or "queued",
    }
    tenant_data = ref(f"tenants/{tenant_id}").get() or {}
    mark_customer_invoice_paid(tenant_id, payment.get("customer_id"), {"id": payment_id, **payment, **update})
    collection_account = str(update.get("collection_account") or "").strip().lower()
    platform_collected = collection_account != "tenant_daraja"
    payout = tenant_payout_details(tenant_data)
    settlement_amount = paid_amount
    try:
        platform_fee_percent = float(os.getenv("PLATFORM_FEE_PERCENTAGE", "0") or 0)
    except ValueError:
        platform_fee_percent = 0
    try:
        platform_fee_fixed = float(os.getenv("PLATFORM_FEE_FIXED", "0") or 0)
    except ValueError:
        platform_fee_fixed = 0
    platform_fee = round((settlement_amount * platform_fee_percent / 100) + platform_fee_fixed, 2) if platform_collected else 0
    tenant_net_amount = max(0, round(settlement_amount - platform_fee, 2)) if platform_collected else 0
    update.update({
        "settlement_gross_amount": settlement_amount,
        "settlement_fee_amount": platform_fee,
        "tenant_net_amount": tenant_net_amount,
        "tenant_payout": payout,
        "tenant_settlement_status": ("queued" if payout.get("payout_phone") else "missing_payout_details") if platform_collected else "not_required",
        "tenant_settlement_queued_at": iso_now() if platform_collected else "",
    })
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update(update)
    try:
        activate_paid_access({"id": tenant_id, **tenant_data}, payment_id, {**payment, **callback_metadata, **update}, phone or payment.get("phone"), receipt)
    except Exception as exc:
        logger.exception("Daraja payment activation failed tenant=%s payment=%s receipt=%s", tenant_id, payment_id, receipt)
        ref(f"tenants/{tenant_id}/payments/{payment_id}").update({"access_status": "activation_failed", "access_error": str(exc)})
        raise
    current_balance = float(tenant_data.get("settlement_pending_amount") or 0)
    tenant_balance_update = {
        "settlement_pending_amount": round(current_balance + tenant_net_amount, 2) if platform_collected else current_balance,
        "settlement_status": update["tenant_settlement_status"],
        "settlement_updated_at": iso_now(),
    }
    if platform_collected and payout.get("payout_phone") and tenant_net_amount > 0:
        try:
            b2c = initiate_daraja_b2c(
                platform_daraja_config({"id": tenant_id, **tenant_data}),
                payment_id,
                tenant_net_amount,
                payout.get("payout_phone"),
                remarks=f"{tenant_data.get('business_name') or tenant_id} settlement",
            )
            update.update({
                "tenant_settlement_status": "requested",
                "tenant_settlement_requested_at": iso_now(),
                "tenant_settlement_conversation_id": b2c.get("ConversationID"),
                "tenant_settlement_originator_conversation_id": b2c.get("OriginatorConversationID"),
                "tenant_settlement_response_code": b2c.get("ResponseCode"),
                "tenant_settlement_response_description": b2c.get("ResponseDescription"),
            })
            tenant_balance_update.update({
                "settlement_pending_amount": max(0, round(current_balance, 2)),
                "settlement_last_requested_amount": tenant_net_amount,
                "settlement_last_requested_at": update["tenant_settlement_requested_at"],
                "settlement_status": "requested",
            })
        except PaymentProviderError as exc:
            update.update({
                "tenant_settlement_status": "request_failed",
                "tenant_settlement_error": exc.detail,
                "tenant_settlement_failed_at": iso_now(),
            })
            tenant_balance_update["settlement_status"] = "request_failed"
        except Exception as exc:
            update.update({
                "tenant_settlement_status": "request_failed",
                "tenant_settlement_error": str(exc),
                "tenant_settlement_failed_at": iso_now(),
            })
            tenant_balance_update["settlement_status"] = "request_failed"
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update(update)
    ref(f"tenants/{tenant_id}").update(tenant_balance_update)
    logger.info("Daraja payment completed tenant=%s payment=%s receipt=%s phone=%s amount=%s", tenant_id, payment_id, receipt, phone or payment.get("phone"), paid_amount)
    return True


@csrf_exempt
@api_view(["POST"])
def daraja_callback(request, tenant_id, payment_id, token):
    # Daraja has no request-signing mechanism — the per-payment token
    # embedded in the callback URL at STK push time is what stops this
    # endpoint from being used to spoof completion of an arbitrary payment.
    if not verify_daraja_callback_token(tenant_id, payment_id, token):
        logger.warning("Daraja callback rejected invalid token tenant=%s payment=%s", tenant_id, payment_id)
        return ok({"success": False, "message": "Invalid callback token"}, 401)

    event = body(request)
    stk_callback = (((event or {}).get("Body") or {}).get("stkCallback")) or {}
    result_code = stk_callback.get("ResultCode")
    result_desc = stk_callback.get("ResultDesc") or ""
    if str(payment_id).startswith("system-subscription-"):
        items = ((stk_callback.get("CallbackMetadata") or {}).get("Item")) or []
        values = {item.get("Name"): item.get("Value") for item in items if isinstance(item, dict)}
        amount = values.get("Amount")
        receipt = values.get("MpesaReceiptNumber") or ""
        phone = values.get("PhoneNumber") or ""
        subscription = TenantSubscription.objects.select_related("tenant").filter(tenant_id=tenant_id).first()
        if str(result_code) != "0":
            logger.warning(
                "Daraja system subscription callback failed tenant=%s payment=%s result_code=%s result_desc=%s",
                tenant_id,
                payment_id,
                result_code,
                result_desc,
            )
            return ok({"success": True})
        if not subscription:
            logger.warning("Daraja system subscription callback missing subscription tenant=%s payment=%s", tenant_id, payment_id)
            return ok({"success": True})
        now = timezone.now()
        current_expiry = subscription.expires_at if subscription.expires_at and subscription.expires_at > now else now
        period_end = current_expiry + timedelta(days=subscription.billing_cycle_days)
        payment = SubscriptionPayment.objects.create(
            subscription=subscription,
            amount=Decimal(str(amount or subscription.amount or 0)),
            currency=subscription.currency,
            method="mpesa_stk",
            reference=receipt,
            notes=f"System subscription STK payment. Phone: {phone}. Callback: {payment_id}",
            period_start=current_expiry,
            period_end=period_end,
            recorded_by="daraja_callback",
        )
        subscription.last_paid_at = payment.paid_at
        subscription.expires_at = period_end
        subscription.save(update_fields=["last_paid_at", "expires_at", "updated_at"])
        if subscription.tenant.status == "suspended":
            subscription.tenant.status = "active"
            subscription.tenant.save(update_fields=["status", "updated_at"])
            ref(f"tenants/{tenant_id}").update(
                {
                    "status": "active",
                    "subscription_restored_at": iso_now(),
                    "suspended_reason": "",
                    "updated_at": iso_now(),
                }
            )
        logger.info("Daraja system subscription completed tenant=%s payment=%s receipt=%s amount=%s", tenant_id, payment_id, receipt, amount)
        return ok({"success": True})
    ref(f"tenants/{tenant_id}/payments/{payment_id}").update({
        "daraja_callback_received_at": iso_now(),
        "daraja_callback_result_code": result_code,
        "daraja_callback_result_desc": result_desc,
        "daraja_callback_payload": event,
    })

    if str(result_code) != "0":
        logger.warning(
            "Daraja callback failed tenant=%s payment=%s result_code=%s result_desc=%s",
            tenant_id,
            payment_id,
            result_code,
            result_desc,
        )
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

    try:
        complete_daraja_payment(tenant_id, payment_id, {}, amount, receipt, paid_at, phone)
    except Exception:
        logger.exception("Daraja callback activation failed tenant=%s payment=%s receipt=%s", tenant_id, payment_id, receipt)
        raise
    return ok({"success": True})


def update_b2c_settlement(event, status):
    result = (event or {}).get("Result") or {}
    originator_id = result.get("OriginatorConversationID") or ""
    conversation_id = result.get("ConversationID") or ""
    occasion = result.get("Occasion") or ""
    result_code = result.get("ResultCode")
    result_desc = result.get("ResultDesc") or ""
    for tenant in list_children("tenants"):
        tenant_id = tenant.get("id")
        for payment in list_children(f"tenants/{tenant_id}/payments"):
            matches_payment = occasion and str(payment.get("id")) == str(occasion)
            matches_originator = originator_id and payment.get("tenant_settlement_originator_conversation_id") == originator_id
            matches_conversation = conversation_id and payment.get("tenant_settlement_conversation_id") == conversation_id
            if not (matches_payment or matches_originator or matches_conversation):
                continue
            payment_id = payment.get("id")
            settlement_status = "paid" if status == "result" and str(result_code) == "0" else "failed"
            update = {
                "tenant_settlement_status": settlement_status,
                "tenant_settlement_result_code": result_code,
                "tenant_settlement_result_desc": result_desc,
                "tenant_settlement_result_at": iso_now(),
                "tenant_settlement_result": result,
            }
            ref(f"tenants/{tenant_id}/payments/{payment_id}").update(update)
            ref(f"tenants/{tenant_id}").update({
                "settlement_status": settlement_status,
                "settlement_last_result_at": update["tenant_settlement_result_at"],
                "settlement_last_result_desc": result_desc,
            })
            return True
    return False


@csrf_exempt
@api_view(["POST"])
def daraja_b2c_result(request):
    matched = update_b2c_settlement(body(request), "result")
    if not matched:
        logger.warning("Daraja B2C result did not match a payment")
    return ok({"success": True})


@csrf_exempt
@api_view(["POST"])
def daraja_b2c_timeout(request):
    matched = update_b2c_settlement(body(request), "timeout")
    if not matched:
        logger.warning("Daraja B2C timeout did not match a payment")
    return ok({"success": True})
