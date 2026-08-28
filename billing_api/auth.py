from functools import wraps

from rest_framework.response import Response
from django.utils import timezone

from .models import Tenant, TenantSubscription
from .services import decode_admin_token, decode_tenant_token, ref

PAGE_RULES = [
    ("staff_tasks", ("staff/tasks",)),
    ("staff_reports", ("staff/reports",)),
    ("staff_requisitions", ("staff/requisitions",)),
    ("dashboard", ("dashboard/",)),
    ("customers", ("customers",)),
    ("packages", ("packages",)),
    ("payments", ("payments",)),
    ("invoices", ("invoices",)),
    ("vouchers", ("vouchers",)),
    ("expenses", ("reports/expenses",)),
    ("reports", ("reports/",)),
    ("messages", ("settings/notifications", "settings/test-sms", "settings/test-whatsapp", "settings/expiry-notifications")),
    ("emails", ()),
    ("mikrotik", ("router/", "settings/mikrotik")),
    ("equipment", ()),
    ("requisitions", ("requisitions",)),
    ("settings", ("settings/", "team/")),
    ("tickets", ("tickets",)),
]

DEFAULT_MEMBER_PAGES = {"staff_tasks", "staff_reports", "staff_requisitions"}
SUBSCRIPTION_PAYMENT_PREFIXES = ("subscription/status",)


def _normalized_api_path(request):
    path = request.path.strip("/").lower()
    parts = path.split("/")
    if parts and parts[0] == "api":
        parts = parts[1:]
    if parts and parts[0] == "v1":
        parts = parts[1:]
    return "/".join(parts)


def _request_page_key(request):
    path = _normalized_api_path(request)
    for page_key, prefixes in PAGE_RULES:
        if any(path.startswith(prefix) for prefix in prefixes):
            return page_key
    return None


def _request_action(request):
    method = request.method.upper()
    if method == "GET":
        return "view"
    if method == "POST":
        return "create"
    if method in {"PATCH", "PUT"}:
        return "edit"
    if method == "DELETE":
        return "delete"
    return "view"


def _member_allows(member, page_key, action):
    if page_key in DEFAULT_MEMBER_PAGES:
        return True
    permissions = member.get("permissions") if isinstance(member, dict) else {}
    page = permissions.get(page_key) if isinstance(permissions, dict) else {}
    return bool(page.get("access") and page.get(action, action == "view"))


def bearer_token(request):
    header = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    parts = header.split()
    return parts[1] if len(parts) == 2 and parts[0].lower() == "bearer" else None


def tenant_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        token = bearer_token(request)
        if not token:
            return Response({"message": "No token provided"}, status=401)
        try:
            decoded = decode_tenant_token(token)
            tenant = Tenant.objects.filter(pk=decoded["id"]).first()
            if not tenant:
                return Response({"message": "Tenant not found"}, status=401)
            normalized_path = _normalized_api_path(request)
            subscription = TenantSubscription.objects.filter(tenant=tenant).first()
            if tenant.status == "active" and subscription and subscription.expires_at and subscription.expires_at < timezone.now():
                tenant.status = "suspended"
                tenant.save(update_fields=["status", "updated_at"])
                ref(f"tenants/{tenant.pk}").update(
                    {
                        "status": "suspended",
                        "suspended_reason": "expired_subscription",
                        "subscription_expired_at": subscription.expires_at.isoformat(),
                    }
                )
            if tenant.status != "active":
                if tenant.status == "suspended" and any(normalized_path.startswith(prefix) for prefix in SUBSCRIPTION_PAYMENT_PREFIXES):
                    pass
                elif tenant.status == "suspended":
                    return Response(
                        {
                            "message": "Your subscription has expired. Please pay to continue using the billing system.",
                            "code": "SUBSCRIPTION_PAYMENT_REQUIRED",
                            "redirect": "/expenses?paySystem=1",
                        },
                        status=402,
                    )
                else:
                    return Response({"message": "Your account is pending admin activation."}, status=403)
            request.tenant = tenant.as_dict(include_id=True)
            request.tenant["password"] = tenant.password
            request.tenant_member = None
            member_id = decoded.get("member_id")
            if member_id:
                members = request.tenant.get("team_members") or {}
                member = members.get(member_id) if isinstance(members, dict) else None
                if not member or member.get("status", "active") != "active":
                    return Response({"message": "User account inactive"}, status=403)
                page_key = _request_page_key(request)
                action = _request_action(request)
                if page_key and not _member_allows(member, page_key, action):
                    return Response({"message": "You are not allowed to access this page or perform this action."}, status=403)
                request.tenant_member = {"id": member_id, **member}
                request.tenant["member_id"] = member_id
                request.tenant["role"] = member.get("role") or ""
                request.tenant["permissions"] = member.get("permissions") or {}
                request.tenant["is_admin"] = False
            else:
                request.tenant["role"] = "tenant_admin"
                request.tenant["is_admin"] = True
        except Exception:
            return Response({"message": "Invalid token"}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        token = bearer_token(request)
        if not token:
            return Response({"error": "Admin token required"}, status=401)
        try:
            decoded = decode_admin_token(token)
            if decoded.get("role") != "admin":
                return Response({"error": "Insufficient privileges"}, status=403)
            admin_data = ref(f"admins/{decoded['adminId']}").get()
            if not admin_data or not admin_data.get("is_active"):
                return Response({"error": "Admin account inactive"}, status=403)
            request.admin = {
                "adminId": decoded["adminId"],
                "email": decoded.get("email"),
                "name": decoded.get("name"),
                "role": decoded.get("role"),
            }
        except Exception as exc:
            if exc.__class__.__name__ == "ExpiredSignatureError":
                return Response({"error": "Admin session expired"}, status=401)
            return Response({"error": "Invalid admin token"}, status=403)
        return view(request, *args, **kwargs)

    return wrapped
