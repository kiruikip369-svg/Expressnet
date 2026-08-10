import json
import html
import hashlib
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
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.mail import EmailMultiAlternatives, send_mail
from django.core.paginator import Paginator
from django.db import close_old_connections, connection
from django.db.utils import OperationalError
from django.db.models import Count, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
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
    create_paystack_subaccount,
    selected_daraja_method,
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


def _normalize_permissions(value):
    return value if isinstance(value, dict) else {}


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


def send_login_verification_email(recipient_email, code):
    if not recipient_email or not code:
        return 0
    brand = getattr(settings, "EMAIL_BRAND_NAME", "Expressnet")
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
    subject = f"{code} is your {brand} sign-in code"
    text_body = (
        f"Your {brand} sign-in code is {code}.\n\n"
        "Enter this code on the login page to finish signing in. "
        "The code expires in 10 minutes.\n\n"
        "If you did not try to sign in, you can ignore this email."
    )
    html_body = f"""
<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;color:#0f172a;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f8fafc;padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:520px;background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;">
            <tr>
              <td style="padding:28px;">
                <p style="margin:0 0 8px;font-size:14px;font-weight:700;color:#2563eb;">{html.escape(brand)}</p>
                <h1 style="margin:0 0 16px;font-size:22px;line-height:1.3;color:#0f172a;">Sign in verification code</h1>
                <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#334155;">Use this code to finish signing in. It expires in 10 minutes.</p>
                <p style="margin:0 0 22px;padding:14px 18px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;text-align:center;font-size:30px;letter-spacing:8px;font-weight:700;color:#1d4ed8;">{html.escape(code)}</p>
                <p style="margin:0;font-size:13px;line-height:1.5;color:#64748b;">If you did not try to sign in, you can safely ignore this email.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    try:
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=from_email,
            to=[recipient_email],
            headers={"Reply-To": from_email} if from_email else None,
        )
        message.attach_alternative(html_body, "text/html")
        return message.send(fail_silently=True)
    except Exception:
        logger.exception("Failed to send login verification email")
        return 0


def _login_failure_key(request, email):
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()
    digest = hashlib.sha256(f"{ip}:{email}".encode("utf-8")).hexdigest()
    return f"tenant_login_failures:{digest}"


def _login_failure_response(request, email):
    limit = int(os.getenv("TENANT_LOGIN_FAILURE_LIMIT", "5"))
    window = int(os.getenv("TENANT_LOGIN_FAILURE_WINDOW", str(15 * 60)))
    key = _login_failure_key(request, email)
    failures = int(cache.get(key) or 0) + 1
    cache.set(key, failures, timeout=window)
    if failures > limit:
        return ok({"message": "Too many wrong password attempts. Please try again later."}, 429)
    return None


def _clear_login_failures(request, email):
    cache.delete(_login_failure_key(request, email))


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
        "Your Expressnetbilling account is active",
        (
            f"Hello {tenant.get('owner_name') or tenant.get('business_name')},\n\n"
            f"Your {tenant.get('business_name') or 'Expressnetbilling'} account has been activated. "
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
    service_type = package_service_type(data or {})
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
            "sms_balance": 10,
            "sms_sent_count": 0,
            "theme_color": data.get("theme_color") or "#fa8200",
            "dark_mode": False,
            "status": "active",
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
    return ok({"success": True, "message": "Business registered successfully. Your account is active.", "tenantId": tenant_ref.key, **subaccount_status})


@csrf_exempt
@api_view(["POST"])
def auth_login(request):
    data = body(request)
    if data.get("resend_challenge_id"):
        challenge_id = str(data.get("resend_challenge_id") or "").strip()
        challenge = cache.get(f"tenant_login_2fa:{challenge_id}")
        if not challenge:
            return ok({"message": "Invalid or expired verification code"}, 401)
        sent = send_login_verification_email(challenge.get("email"), challenge.get("code"))
        if not sent:
            return ok({"message": "We could not resend the verification email. Please try again."}, 502)
        return ok({"success": True, "message": "Verification code resent to your email."})

    if data.get("challenge_id"):
        challenge_id = str(data.get("challenge_id") or "").strip()
        code = "".join(ch for ch in str(data.get("code") or "") if ch.isdigit())
        challenge = cache.get(f"tenant_login_2fa:{challenge_id}")
        if not challenge or not code or code != str(challenge.get("code")):
            return ok({"message": "Invalid or expired verification code"}, 401)
        cache.delete(f"tenant_login_2fa:{challenge_id}")
        tenant_obj = Tenant.objects.filter(pk=challenge.get("tenant_id")).first()
        if not tenant_obj:
            return ok({"message": "Tenant not found"}, 401)
        tenant = tenant_obj.as_dict(include_id=True)
        member_id = challenge.get("member_id")
        try:
            token = tenant_token(tenant["id"], member_id=member_id)
        except Exception as exc:
            return ok({"message": f"Server configuration error: {exc}"}, 500)
        if member_id:
            member = (tenant.get("team_members") or {}).get(member_id) or {}
            tenant_payload = {
                "id": tenant["id"],
                "business_name": tenant.get("business_name"),
                "email": member.get("email"),
                "name": member.get("name"),
                "role": member.get("role") or "",
                "member_id": member_id,
                "permissions": _normalize_permissions(member.get("permissions")),
                "is_admin": False,
                **tenant_theme_payload(tenant),
            }
        else:
            tenant_payload = {
                "id": tenant["id"],
                "business_name": tenant.get("business_name"),
                "email": tenant.get("email"),
                "role": "tenant_admin",
                "is_admin": True,
                **tenant_theme_payload(tenant),
            }
        return ok({"success": True, "token": token, "tenant": tenant_payload})

    if not data.get("email") or not data.get("password"):
        return ok({"message": "Email and password are required"}, 400)
    email = str(data["email"]).lower().strip()
    password = str(data["password"]).strip()

    def start_two_step(tenant, recipient_email, member_id=None):
        code = f"{secrets.randbelow(1000000):06d}"
        challenge_id = secrets.token_urlsafe(24)
        cache.set(
            f"tenant_login_2fa:{challenge_id}",
            {"tenant_id": tenant["id"], "member_id": member_id, "code": code, "email": recipient_email},
            timeout=10 * 60,
        )
        sent = send_login_verification_email(recipient_email, code)
        if not sent:
            return ok({"message": "We could not send the verification email. Please try again."}, 502)
        return ok(
            {
                "success": True,
                "requires_two_step": True,
                "challenge_id": challenge_id,
                "message": "Verification code sent to your email.",
            }
        )

    tenant_obj = Tenant.objects.filter(email__iexact=email).first()
    if tenant_obj:
        if not check_password(password, tenant_obj.password):
            limited = _login_failure_response(request, email)
            if limited:
                return limited
            return ok({"message": "Wrong password"}, 401)
        _clear_login_failures(request, email)
        tenant = tenant_obj.as_dict(include_id=True)
        if tenant.get("status") != "active":
            tenant_obj.status = "active"
            tenant_obj.save(update_fields=["status"])
            tenant["status"] = "active"
        return start_two_step(tenant, tenant.get("email"))

    for tenant_obj in Tenant.objects.filter(status="active"):
        tenant = tenant_obj.as_dict(include_id=True)
        members = tenant.get("team_members") or {}
        if not isinstance(members, dict):
            continue
        for member_id, member in members.items():
            if str(member.get("email") or "").lower().strip() != email:
                continue
            if member.get("status", "active") != "active" or not check_password(password, member.get("password")):
                limited = _login_failure_response(request, email)
                if limited:
                    return limited
                return ok({"message": "Wrong password"}, 401)
            _clear_login_failures(request, email)
            return start_two_step(tenant, member.get("email"), member_id=member_id)

    limited = _login_failure_response(request, email)
    if limited:
        return limited
    return ok({"message": "Account not found"}, 404)


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
