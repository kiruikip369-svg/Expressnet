import base64
import hashlib
import hmac
import json
import logging
import os
import socket
import ssl
import uuid
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlparse, urlsplit, urlunsplit

import bcrypt
import firebase_admin
import jwt
import requests
from django.conf import settings
from firebase_admin import credentials, db as firebase_db

from billing_api.models import AdminAuditLog, AdminUser, Customer, InternetPackage, Payment, SiteSettings, Tenant, Ticket, Voucher


BASE_DIR = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)



class PaymentProviderError(RuntimeError):
    def __init__(self, public_message, detail=None, status_code=502):
        super().__init__(detail or public_message)
        self.public_message = public_message
        self.detail = detail or public_message
        self.status_code = status_code


def utcnow():
    return datetime.now(timezone.utc)


def iso_now():
    return utcnow().isoformat().replace("+00:00", "Z")


def require_secret(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    if len(value) < 16:
        raise RuntimeError(f"{name} must be at least 16 characters")
    return value


def firebase_backup_configured():
    if os.getenv("FIREBASE_BACKUP_ENABLED", "true").lower() in {"0", "false", "no", "off"}:
        return False
    return bool(os.getenv("FIREBASE_DATABASE_URL") and os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))


def init_firebase_backup():
    if not firebase_backup_configured():
        return False
    if firebase_admin._apps:
        return True

    if os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"):
        service_account = json.loads(os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON"))
        if service_account.get("private_key"):
            service_account["private_key"] = service_account["private_key"].replace("\\n", "\n")
    else:
        candidates = [BASE_DIR / "serviceAccount.json"]
        candidates += list(BASE_DIR.glob("*firebase-adminsdk*.json"))
        service_path = next((path for path in candidates if path.exists()), None)
        if not service_path:
            return False
        service_account = json.loads(service_path.read_text(encoding="utf-8"))

    database_url = os.getenv("FIREBASE_DATABASE_URL", "").strip().strip("\"'").rstrip(" ,/\t\r\n")
    if not database_url:
        return False

    firebase_admin.initialize_app(credentials.Certificate(service_account), {"databaseURL": database_url})
    return True


def firebase_backup_ref(path):
    if not init_firebase_backup():
        return None
    return firebase_db.reference(path)


def backup_set(path, data):
    backup = firebase_backup_ref(path)
    if backup is not None:
        backup.set(data)


def backup_update(path, data):
    backup = firebase_backup_ref(path)
    if backup is not None:
        backup.update(data)


def backup_delete(path):
    backup = firebase_backup_ref(path)
    if backup is not None:
        backup.delete()


def model_dict(instance, include_id=True, exclude=None):
    return instance.as_dict(include_id=include_id, exclude=exclude) if hasattr(instance, "as_dict") else {}


def model_update(instance, data):
    instance.apply_data(data)
    instance.save()
    return instance


class OrmRef:
    def __init__(self, path=""):
        self.parts = [part for part in str(path).strip("/").split("/") if part]
        self.key = None

    def _tenant(self, tenant_id):
        return Tenant.objects.get(pk=tenant_id)

    def _package(self, tenant_id, package_id):
        return InternetPackage.objects.get(tenant_id=tenant_id, pk=package_id)

    def _customer(self, tenant_id, customer_id):
        return Customer.objects.get(tenant_id=tenant_id, pk=customer_id)

    def _payment(self, tenant_id, payment_id):
        return Payment.objects.get(tenant_id=tenant_id, pk=payment_id)

    def _ticket(self, tenant_id, ticket_id):
        return Ticket.objects.get(tenant_id=tenant_id, pk=ticket_id)

    def _site_settings(self):
        return SiteSettings.objects.order_by("pk").first()

    def _resolve_instance(self):
        parts = self.parts
        if len(parts) == 2 and parts[0] == "tenants":
            return self._tenant(parts[1])
        if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "packages":
            return self._package(parts[1], parts[3])
        if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "customers":
            return self._customer(parts[1], parts[3])
        if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "payments":
            return self._payment(parts[1], parts[3])
        if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "tickets":
            return self._ticket(parts[1], parts[3])
        if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "vouchers":
            return Voucher.objects.get(tenant_id=parts[1], pk=parts[3])
        if len(parts) == 2 and parts[0] == "admins":
            return AdminUser.objects.get(pk=parts[1])
        if parts == ["site_settings"]:
            return self._site_settings()
        raise KeyError(f"Unsupported relational ref path: {'/'.join(parts)}")

    def get(self):
        parts = self.parts
        try:
            if parts == ["tenants"]:
                return {str(item.pk): model_dict(item, include_id=False) for item in Tenant.objects.all()}
            if len(parts) == 2 and parts[0] == "tenants":
                return model_dict(self._tenant(parts[1]), include_id=False)
            if len(parts) == 3 and parts[0] == "tenants" and parts[2] == "packages":
                return {str(item.pk): model_dict(item, include_id=False) for item in InternetPackage.objects.filter(tenant_id=parts[1])}
            if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "packages":
                return model_dict(self._package(parts[1], parts[3]), include_id=False)
            if len(parts) == 3 and parts[0] == "tenants" and parts[2] == "customers":
                return {str(item.pk): model_dict(item, include_id=False, exclude={"password"}) for item in Customer.objects.filter(tenant_id=parts[1])}
            if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "customers":
                return model_dict(self._customer(parts[1], parts[3]), include_id=False)
            if len(parts) == 3 and parts[0] == "tenants" and parts[2] == "payments":
                return {str(item.pk): model_dict(item, include_id=False) for item in Payment.objects.filter(tenant_id=parts[1])}
            if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "payments":
                return model_dict(self._payment(parts[1], parts[3]), include_id=False)
            if len(parts) == 3 and parts[0] == "tenants" and parts[2] == "tickets":
                return {str(item.pk): model_dict(item, include_id=False) for item in Ticket.objects.filter(tenant_id=parts[1])}
            if len(parts) == 3 and parts[0] == "tenants" and parts[2] == "vouchers":
                return {str(item.pk): model_dict(item, include_id=False) for item in Voucher.objects.filter(tenant_id=parts[1])}
            if len(parts) == 4 and parts[0] == "tenants" and parts[2] == "tickets":
                return model_dict(self._ticket(parts[1], parts[3]), include_id=False)
            if parts == ["admins"]:
                return {str(item.pk): model_dict(item, include_id=False) for item in AdminUser.objects.all()}
            if len(parts) == 2 and parts[0] == "admins":
                return model_dict(AdminUser.objects.get(pk=parts[1]), include_id=False)
            if parts == ["site_settings"]:
                settings = self._site_settings()
                return model_dict(settings, include_id=False) if settings else {}
            if parts == ["admin_audit_logs"]:
                return {str(item.pk): item.as_dict(include_id=False) for item in AdminAuditLog.objects.all()}
        except (Tenant.DoesNotExist, InternetPackage.DoesNotExist, Customer.DoesNotExist, Payment.DoesNotExist, Ticket.DoesNotExist, Voucher.DoesNotExist, AdminUser.DoesNotExist):
            return None
        raise KeyError(f"Unsupported relational ref path: {'/'.join(parts)}")

    def push(self, data):
        parts = self.parts
        if parts == ["tenants"]:
            instance = Tenant()
        elif len(parts) == 3 and parts[0] == "tenants" and parts[2] == "packages":
            instance = InternetPackage(tenant_id=parts[1])
        elif len(parts) == 3 and parts[0] == "tenants" and parts[2] == "customers":
            instance = Customer(tenant_id=parts[1])
        elif len(parts) == 3 and parts[0] == "tenants" and parts[2] == "payments":
            instance = Payment(tenant_id=parts[1])
        elif len(parts) == 3 and parts[0] == "tenants" and parts[2] == "tickets":
            instance = Ticket(tenant_id=parts[1])
        elif len(parts) == 3 and parts[0] == "tenants" and parts[2] == "vouchers":
            instance = Voucher(tenant_id=parts[1])
        elif parts == ["admins"]:
            instance = AdminUser()
        elif parts == ["admin_audit_logs"]:
            instance = AdminAuditLog()
        else:
            raise KeyError(f"Unsupported relational push path: {'/'.join(parts)}")

        if hasattr(instance, "apply_data"):
            instance.apply_data(dict(data or {}))
        else:
            for key, value in (data or {}).items():
                if hasattr(instance, key):
                    setattr(instance, key, value)
        instance.save()
        result = OrmPushResult(instance, self._child_backup_path(instance))
        result.backup_set()
        return result

    def update(self, data):
        parts = self.parts
        if parts == ["site_settings"]:
            instance = self._site_settings() or SiteSettings()
            model_update(instance, dict(data or {}))
            backup_update("site_settings", dict(data or {}))
            return
        instance = self._resolve_instance()
        if not instance:
            return
        model_update(instance, dict(data or {}))
        backup_update(self._backup_path(), dict(data or {}))

    def delete(self):
        instance = self._resolve_instance()
        if instance:
            instance.delete()
            backup_delete(self._backup_path())

    def _backup_path(self):
        return "/".join(self.parts)

    def _child_backup_path(self, instance):
        parts = list(self.parts)
        if parts == ["tenants"]:
            return f"tenants/{instance.pk}"
        if len(parts) == 3 and parts[0] == "tenants" and parts[2] in {"packages", "customers", "payments", "tickets"}:
            return f"{'/'.join(parts)}/{instance.pk}"
        if parts == ["admins"]:
            return f"admins/{instance.pk}"
        if parts == ["admin_audit_logs"]:
            return f"admin_audit_logs/{instance.pk}"
        return f"{'/'.join(parts)}/{instance.pk}"


class OrmPushResult:
    def __init__(self, instance, backup_path):
        self.instance = instance
        self.key = str(instance.pk)
        self.backup_path = backup_path

    def update(self, data):
        model_update(self.instance, dict(data or {}))
        backup_update(self.backup_path, dict(data or {}))

    def backup_set(self):
        if hasattr(self.instance, "as_dict"):
            backup_set(self.backup_path, self.instance.as_dict(include_id=False))
        else:
            backup_set(self.backup_path, {})


def ref(path=""):
    return OrmRef(path)


def list_children(path):
    value = ref(path).get() or {}
    if not isinstance(value, dict):
        return []
    return [{"id": key, **(item or {})} for key, item in value.items()]


def find_child_by_field(path, field, expected):
    expected = str(expected).lower().strip()
    for item in list_children(path):
        if str(item.get(field, "")).lower().strip() == expected:
            return item
    return None


def hash_password(password):
    return bcrypt.hashpw(str(password).encode(), bcrypt.gensalt(rounds=10)).decode()


def check_password(password, hashed):
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(str(password).encode(), str(hashed).encode())
    except (ValueError, TypeError):
        return False


def _get_jwt_secret(name):
    value = os.getenv(name, "")
    if not value:
        raise RuntimeError(f"{name} is not configured. Please set it in your .env file.")
    return value


def tenant_token(tenant_id, member_id=None):
    payload = {"id": tenant_id, "exp": utcnow() + timedelta(days=7)}
    if member_id:
        payload["member_id"] = member_id
    return jwt.encode(
        payload,
        _get_jwt_secret("JWT_SECRET"),
        algorithm="HS256",
    )


def admin_token(admin_id, admin_data):
    return jwt.encode(
        {
            "adminId": admin_id,
            "email": admin_data.get("email"),
            "name": admin_data.get("name"),
            "role": "admin",
            "exp": utcnow() + timedelta(hours=4),
        },
        _get_jwt_secret("ADMIN_JWT_SECRET"),
        algorithm="HS256",
    )


def decode_tenant_token(token):
    return jwt.decode(token, _get_jwt_secret("JWT_SECRET"), algorithms=["HS256"])


def decode_admin_token(token):
    return jwt.decode(token, _get_jwt_secret("ADMIN_JWT_SECRET"), algorithms=["HS256"])


def normalize_phone(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("254") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 10:
        return f"254{digits[1:]}"
    if digits.startswith("7") and len(digits) == 9:
        return f"254{digits}"
    return digits


def normalize_public_url(value):
    value = str(value or "").strip().strip("\"'").rstrip("/")
    while value.startswith("http://https://"):
        value = "https://" + value[len("http://https://") :]
    while value.startswith("https://http://"):
        value = "http://" + value[len("https://http://") :]
    if value.startswith("//"):
        value = "https:" + value
    if value and not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def get_public_base_url():
    candidates = [
        os.getenv("PUBLIC_APP_URL"),
        os.getenv("PAYSTACK_CALLBACK_BASE_URL"),
        getattr(settings, "PUBLIC_APP_URL", ""),
        getattr(settings, "PAYSTACK_CALLBACK_BASE_URL", ""),
    ]
    if not settings.DEBUG:
        candidates = [item for item in candidates if item and "localhost" not in item and "127.0.0.1" not in item]
    configured = next((item for item in candidates if item), "")
    return normalize_public_url(configured)




def write_audit_log(admin_id=None, admin_email=None, action=None, target_id=None, target_type=None, request=None, metadata=None):
    ref("admin_audit_logs").push(
        {
            "admin_id": admin_id,
            "admin_email": admin_email,
            "action": action,
            "target_id": target_id,
            "target_type": target_type,
            "ip": request.META.get("REMOTE_ADDR") if request else None,
            "user_agent": request.META.get("HTTP_USER_AGENT") if request else None,
            "metadata": metadata or {},
        }
    )
