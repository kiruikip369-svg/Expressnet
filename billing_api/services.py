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
from urllib.parse import urlparse, urlsplit, urlunsplit

import bcrypt
import firebase_admin
import jwt
import requests
from django.conf import settings
from firebase_admin import credentials, db as firebase_db

from .models import AdminAuditLog, AdminUser, Customer, InternetPackage, Payment, SiteSettings, Tenant, Ticket, Voucher


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


def tenant_token(tenant_id):
    return jwt.encode(
        {"id": tenant_id, "exp": utcnow() + timedelta(days=7)},
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


def get_platform_paystack_secret():
    secret = os.getenv("PAYSTACK_SECRET_KEY")
    if secret and "replace_with" not in secret and not secret.strip().endswith("_secret_key"):
        return secret.strip()
    raise PaymentProviderError(
        "Payment is temporarily unavailable. Please contact support.",
        "PAYSTACK_SECRET_KEY is not configured",
        503,
    )


def get_paystack_secret(tenant=None):
    tenant_secret = (tenant or {}).get("paystack_secret_key")
    if tenant_secret and str(tenant_secret).strip() and "â€¢" not in str(tenant_secret) and "replace_with" not in str(tenant_secret):
        return str(tenant_secret).strip()
    return get_platform_paystack_secret()


def make_paystack_reference(tenant_id):
    return f"ps_{tenant_id}_{uuid.uuid4().hex[:24]}"


def paystack_amount(amount):
    return int(round(float(amount or 0) * 100))


def paystack_platform_percentage():
    try:
        return float(os.getenv("PAYSTACK_PLATFORM_PERCENTAGE", "1"))
    except ValueError:
        return 1.0


def create_paystack_subaccount(tenant, bank_code, account_number, business_number=None, percentage_charge=None):
    secret = get_platform_paystack_secret()
    payload = {
        "business_name": tenant.get("business_name") or tenant.get("email") or "Internet tenant",
        "bank_code": str(bank_code or "").strip(),
        "account_number": str(account_number or "").strip(),
        "percentage_charge": paystack_platform_percentage() if percentage_charge is None else float(percentage_charge),
        "description": f"ISP tenant settlement account for {tenant.get('business_name') or tenant.get('id')}",
        "primary_contact_name": tenant.get("owner_name") or tenant.get("business_name") or "",
        "primary_contact_email": tenant.get("email") or "",
        "primary_contact_phone": tenant.get("phone") or "",
    }
    if business_number:
        payload["metadata"] = {"business_number": business_number}

    if not payload["bank_code"] or not payload["account_number"]:
        raise PaymentProviderError("Bank code and account number are required to create a settlement account.", "Missing bank_code or account_number", 400)

    response = requests.post(
        "https://api.paystack.co/subaccount",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise PaymentProviderError(
            "Could not create the tenant settlement account. Please verify the bank details.",
            f"Paystack subaccount creation failed {response.status_code}: {response.text[:500]}",
            502 if response.status_code not in {401, 403} else 503,
        ) from exc
    data = response.json()
    if not data.get("status"):
        raise PaymentProviderError("Could not create the tenant settlement account. Please verify the bank details.", data.get("message") or "Paystack rejected subaccount creation", 502)
    return data.get("data") or {}


def initiate_paystack_payment(tenant, payment_id, amount, email=None, phone=None, description=None, metadata=None):
    secret = get_platform_paystack_secret()
    subaccount_code = str((tenant or {}).get("paystack_subaccount_code") or "").strip()
    if not subaccount_code:
        raise PaymentProviderError(
            "Payment is not ready for this business. Please contact support.",
            "Tenant has no Paystack subaccount code",
            503,
        )
    reference = make_paystack_reference(tenant.get("id"))
    base_url = get_public_base_url()
    if not base_url:
        raise RuntimeError("PUBLIC_APP_URL or PAYSTACK_CALLBACK_BASE_URL is required for Paystack checkout")

    customer_email = str(email or "").strip()
    if not customer_email:
        digits = "".join(ch for ch in str(phone or "") if ch.isdigit()) or "customer"
        customer_email = f"{digits}@example.com"

    payload = {
        "amount": paystack_amount(amount),
        "email": customer_email,
        "currency": tenant.get("paystack_currency") or os.getenv("PAYSTACK_CURRENCY", "KES"),
        "reference": reference,
        "callback_url": f"{base_url}/api/paystack/callback",
        "metadata": {
            "tenant_id": tenant.get("id"),
            "payment_id": payment_id,
            "phone": phone,
            **(metadata or {}),
        },
    }
    if description:
        payload["metadata"]["description"] = description

    payload["subaccount"] = subaccount_code
    payload["bearer"] = tenant.get("paystack_bearer") or "subaccount"

    response = requests.post(
        "https://api.paystack.co/transaction/initialize",
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        detail = response.text[:500]
        public_message = "Payment gateway rejected the request. Please contact support."
        status_code = 502
        if response.status_code in {401, 403}:
            public_message = "Payment is temporarily unavailable. Please contact support."
            status_code = 503
        raise PaymentProviderError(public_message, f"Paystack initialize failed {response.status_code}: {detail}", status_code) from exc
    data = response.json()
    if not data.get("status"):
        raise PaymentProviderError("Payment gateway rejected the request. Please contact support.", data.get("message") or "Paystack rejected the transaction")
    result = data.get("data") or {}
    result.update({"reference": reference, "customer_email": customer_email, "currency": payload["currency"]})
    return result


def initiate_daraja_stk(tenant, payment_id, amount, phone, payment_method="daraja_paybill", description=None):
    """Start a Daraja STK push for a tenant Paybill or Buy Goods account."""
    consumer_key = str(tenant.get("daraja_consumer_key") or "").strip()
    consumer_secret = str(tenant.get("daraja_consumer_secret") or "").strip()
    passkey = str(tenant.get("daraja_passkey") or "").strip()
    shortcode = str(tenant.get("daraja_shortcode") or "").strip()
    till = str(tenant.get("daraja_till_number") or "").strip()
    if not all([consumer_key, consumer_secret, passkey, shortcode]):
        raise PaymentProviderError("Daraja credentials are incomplete. Add all required M-Pesa API credentials.", "Missing Daraja credentials", 400)
    if payment_method == "daraja_buygoods":
        shortcode = till or shortcode
        transaction_type = "CustomerBuyGoodsOnline"
    else:
        transaction_type = "CustomerPayBillOnline"
    if not shortcode:
        raise PaymentProviderError("The Daraja shortcode or Buy Goods till number is required.", "Missing Daraja shortcode", 400)
    environment = str(tenant.get("daraja_environment") or "sandbox").lower()
    base = "https://api.safaricom.co.ke" if environment == "production" else "https://sandbox.safaricom.co.ke"
    token_response = requests.get(f"{base}/oauth/v1/generate?grant_type=client_credentials", auth=(consumer_key, consumer_secret), timeout=30)
    token_response.raise_for_status()
    token = token_response.json().get("access_token")
    if not token:
        raise PaymentProviderError("Daraja did not return an access token.", "Missing Daraja access token", 502)
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
    callback = f"{get_public_base_url()}/api/daraja/callback"
    payload = {"BusinessShortCode": shortcode, "Password": password, "Timestamp": timestamp, "TransactionType": transaction_type, "Amount": max(1, int(round(float(amount or 0)))), "PartyA": normalize_phone(phone), "PartyB": shortcode, "PhoneNumber": normalize_phone(phone), "CallBackURL": callback, "AccountReference": str(payment_id), "TransactionDesc": description or "Internet package"}
    response = requests.post(f"{base}/mpesa/stkpush/v1/processrequest", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    if str(result.get("ResponseCode")) != "0":
        raise PaymentProviderError(result.get("ResponseDescription") or "Daraja rejected the payment request.", json.dumps(result), 502)
    return result


def verify_paystack_transaction(tenant, reference):
    secret = get_platform_paystack_secret()
    response = requests.get(
        f"https://api.paystack.co/transaction/verify/{reference}",
        headers={"Authorization": f"Bearer {secret}"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack verification failed")
    return data.get("data") or {}


def verify_paystack_signature(raw_body, signature, secret):
    if not signature or not secret:
        return False
    digest = hmac.new(str(secret).encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, str(signature))


# ---------------------------------------------------------------------------
# Daraja (Safaricom M-Pesa) — for tenants who have applied for / been
# approved for their own Daraja API access, rather than the shared
# RoamTech-based SMS/notification path used elsewhere.
#
# A tenant is considered "Daraja-enabled" once they have all four
# credentials configured (consumer key/secret, shortcode, passkey) AND have
# explicitly selected mpesa as their payment_provider — mirroring how a
# Paystack subaccount_code gates that provider. There's no separate
# approval queue in this codebase yet; this is the natural equivalent.
# ---------------------------------------------------------------------------

def daraja_is_configured(tenant, payment_method=None):
    tenant = tenant or {}
    common = all(str(tenant.get(field) or "").strip() for field in ("daraja_consumer_key", "daraja_consumer_secret", "daraja_passkey"))
    if payment_method == "daraja_buygoods":
        return common and bool(str(tenant.get("daraja_till_number") or tenant.get("daraja_shortcode") or "").strip())
    return common and bool(str(tenant.get("daraja_shortcode") or "").strip())


def tenant_uses_daraja(tenant):
    tenant = tenant or {}
    methods = tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else []
    # Daraja is opt-in per tenant payment method. Paystack remains the
    # platform default, regardless of credentials stored on the tenant.
    selected = next((str(item).strip().lower() for item in methods if str(item).strip().lower() in {"daraja_paybill", "daraja_buygoods"}), "")
    return bool(selected) and daraja_is_configured(tenant, selected)


def daraja_base_url(tenant):
    environment = str((tenant or {}).get("daraja_environment") or "production").strip().lower()
    return "https://api.safaricom.co.ke" if environment == "production" else "https://sandbox.safaricom.co.ke"


def get_daraja_credentials(tenant, payment_method="daraja_paybill"):
    tenant = tenant or {}
    consumer_key = str(tenant.get("daraja_consumer_key") or "").strip()
    consumer_secret = str(tenant.get("daraja_consumer_secret") or "").strip()
    shortcode = str(tenant.get("daraja_shortcode") or "").strip()
    passkey = str(tenant.get("daraja_passkey") or "").strip()
    till_number = str(tenant.get("daraja_till_number") or "").strip()
    shortcode_type = str(tenant.get("daraja_shortcode_type") or "CustomerBuyGoodsOnline").strip()
    business_number = till_number if payment_method == "daraja_buygoods" else shortcode
    if not all([consumer_key, consumer_secret, business_number, passkey]):
        logger.warning(
            "Daraja credentials incomplete for tenant=%s method=%s missing=%s",
            tenant.get("id"),
            payment_method,
            [
                name
                for name, value in {
                    "consumer_key": consumer_key,
                    "consumer_secret": consumer_secret,
                    "business_number": business_number,
                    "passkey": passkey,
                }.items()
                if not value
            ],
        )
        raise PaymentProviderError(
            "M-Pesa is not set up for this business yet. Please contact support.",
            "Tenant is missing one or more Daraja credentials",
            503,
        )
    return {
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "shortcode": shortcode,
        "passkey": passkey,
        "till_number": till_number,
        "shortcode_type": shortcode_type,
    }


def get_daraja_access_token(tenant, payment_method="daraja_paybill"):
    creds = get_daraja_credentials(tenant, payment_method)
    response = requests.get(
        f"{daraja_base_url(tenant)}/oauth/v1/generate?grant_type=client_credentials",
        auth=(creds["consumer_key"], creds["consumer_secret"]),
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.warning(
            "Daraja OAuth failed for tenant=%s method=%s status=%s response=%s",
            (tenant or {}).get("id"),
            payment_method,
            response.status_code,
            response.text[:500],
        )
        raise PaymentProviderError(
            "M-Pesa is temporarily unavailable. Please try again shortly.",
            f"Daraja OAuth failed {response.status_code}: {response.text[:500]}",
            503,
        ) from exc
    try:
        token = response.json().get("access_token")
    except ValueError as exc:
        logger.warning(
            "Daraja OAuth returned invalid JSON for tenant=%s method=%s status=%s response=%s",
            (tenant or {}).get("id"),
            payment_method,
            response.status_code,
            response.text[:500],
        )
        raise PaymentProviderError(
            "M-Pesa is temporarily unavailable. Please try again shortly.",
            f"Daraja OAuth returned invalid JSON: {response.text[:500]}",
            503,
        ) from exc
    if not token:
        logger.warning("Daraja OAuth returned no access token for tenant=%s method=%s", (tenant or {}).get("id"), payment_method)
        raise PaymentProviderError("M-Pesa is temporarily unavailable. Please try again shortly.", "Daraja OAuth returned no access_token", 503)
    return token


def daraja_timestamp_and_password(shortcode, passkey):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
    return timestamp, password


def make_daraja_callback_token(tenant_id, payment_id):
    """Daraja has no request-signing mechanism like Paystack's webhook
    signature, so the callback URL itself carries an unguessable,
    per-payment token (HMAC over tenant_id+payment_id with SECRET_KEY) —
    anyone hitting the callback endpoint without the right token can't
    complete an arbitrary payment."""
    digest = hmac.new(settings.SECRET_KEY.encode(), f"{tenant_id}:{payment_id}".encode(), hashlib.sha256).hexdigest()
    return digest[:32]


def verify_daraja_callback_token(tenant_id, payment_id, token):
    expected = make_daraja_callback_token(tenant_id, payment_id)
    return hmac.compare_digest(expected, str(token or ""))


def daraja_phone_format(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("7") and len(digits) == 9:
        digits = "254" + digits
    return digits


def initiate_daraja_payment(tenant, payment_id, amount, phone, description=None, metadata=None, payment_method="daraja_paybill"):
    creds = get_daraja_credentials(tenant, payment_method)
    tenant_id = (tenant or {}).get("id")
    formatted_phone = daraja_phone_format(phone)
    if not formatted_phone or not formatted_phone.startswith("254") or len(formatted_phone) != 12:
        logger.info("Daraja STK rejected invalid phone for tenant=%s payment=%s method=%s phone=%s", tenant_id, payment_id, payment_method, phone)
        raise PaymentProviderError("Enter a valid M-Pesa phone number (07XXXXXXXX or 2547XXXXXXXX).", "Invalid phone for Daraja STK push", 400)

    base_url = get_public_base_url()
    if not base_url:
        logger.error("Daraja callback base URL missing for tenant=%s payment=%s", tenant_id, payment_id)
        raise RuntimeError("PUBLIC_APP_URL or PAYSTACK_CALLBACK_BASE_URL is required for the Daraja callback URL")

    token = get_daraja_access_token(tenant, payment_method)
    business_shortcode = creds["till_number"] if payment_method == "daraja_buygoods" and creds["till_number"] else creds["shortcode"]
    timestamp, password = daraja_timestamp_and_password(business_shortcode, creds["passkey"])
    callback_token = make_daraja_callback_token(tenant_id, payment_id)

    party_b = creds["till_number"] if payment_method == "daraja_buygoods" and creds["till_number"] else business_shortcode
    payload = {
        "BusinessShortCode": business_shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerBuyGoodsOnline" if payment_method == "daraja_buygoods" else "CustomerPayBillOnline",
        "Amount": max(1, int(round(float(amount or 0)))),
        "PartyA": formatted_phone,
        "PartyB": party_b,
        "PhoneNumber": formatted_phone,
        "CallBackURL": f"{base_url}/api/daraja/callback/{tenant_id}/{payment_id}/{callback_token}",
        "AccountReference": str((tenant or {}).get("business_name") or tenant_id)[:12],
        "TransactionDesc": (description or "Internet package")[:13],
    }

    response = requests.post(
        f"{daraja_base_url(tenant)}/mpesa/stkpush/v1/processrequest",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.warning(
            "Daraja STK push failed for tenant=%s payment=%s method=%s status=%s response=%s",
            tenant_id,
            payment_id,
            payment_method,
            response.status_code,
            response.text[:500],
        )
        raise PaymentProviderError(
            "Could not start the M-Pesa payment. Please try again.",
            f"Daraja STK push failed {response.status_code}: {response.text[:500]}",
            502,
        ) from exc
    try:
        data = response.json()
    except ValueError as exc:
        logger.warning(
            "Daraja STK returned invalid JSON for tenant=%s payment=%s method=%s status=%s response=%s",
            tenant_id,
            payment_id,
            payment_method,
            response.status_code,
            response.text[:500],
        )
        raise PaymentProviderError(
            "Could not start the M-Pesa payment. Please try again.",
            f"Daraja returned invalid JSON: {response.text[:500]}",
            502,
        ) from exc
    if str(data.get("ResponseCode") or "0") not in {"0", "00"} and not data.get("CheckoutRequestID"):
        logger.warning("Daraja rejected STK push for tenant=%s payment=%s method=%s response=%s", tenant_id, payment_id, payment_method, data)
        raise PaymentProviderError(
            str(data.get("ResponseDescription") or data.get("errorMessage") or "Daraja rejected the payment request."),
            f"Daraja rejected STK push: {data}",
            502,
        )
    if str(data.get("ResponseCode")) != "0":
        logger.warning("Daraja STK non-success response for tenant=%s payment=%s method=%s response=%s", tenant_id, payment_id, payment_method, data)
        raise PaymentProviderError(
            "Could not start the M-Pesa payment. Please try again.",
            data.get("ResponseDescription") or data.get("errorMessage") or "Daraja rejected the STK push request",
            502,
        )
    logger.info(
        "Daraja STK push started for tenant=%s payment=%s method=%s checkout=%s merchant=%s",
        tenant_id,
        payment_id,
        payment_method,
        data.get("CheckoutRequestID"),
        data.get("MerchantRequestID"),
    )
    return {
        "checkout_request_id": data.get("CheckoutRequestID"),
        "merchant_request_id": data.get("MerchantRequestID"),
        "phone": formatted_phone,
        "customer_message": data.get("CustomerMessage"),
    }


def has_mikrotik_credentials(tenant):
    return bool(tenant.get("mikrotik_host") and tenant.get("mikrotik_user") and tenant.get("mikrotik_pass"))


def normalize_rate_limit(speed):
    value = str(speed or "").strip()
    if not value:
        return None
    if "/" in value:
        return "".join(value.split())
    amount = "".join(ch for ch in value if ch.isdigit() or ch == ".")
    unit = "".join(ch for ch in value if ch.isalpha()).lower() or "m"
    router_unit = "G" if unit.startswith("g") else "K" if unit.startswith("k") else "M"
    return f"{amount}{router_unit}/{amount}{router_unit}" if amount else value.replace(" ", "")


def routeros_duration(value):
    """Format seconds/timedelta as a RouterOS duration such as 6h or 30d."""
    if value in {None, ""}:
        return None
    try:
        total_seconds = int(value.total_seconds()) if hasattr(value, "total_seconds") else int(float(value))
    except (TypeError, ValueError):
        return None
    total_seconds = max(1, total_seconds)
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return "".join(parts)


class RouterOSAPIError(RuntimeError):
    pass


class RouterOSPath:
    def __init__(self, api, path):
        self.api = api
        self.path = path

    @property
    def command_base(self):
        return "/" + "/".join(self.path)

    def select(self):
        return self.api.command(f"{self.command_base}/print")

    def add(self, **fields):
        rows = self.api.command(f"{self.command_base}/add", fields)
        return (rows[0] or {}).get("ret") if rows else None

    def update(self, **fields):
        item_id = fields.pop(".id", None) or fields.pop("id", None)
        if not item_id:
            raise RouterOSAPIError("RouterOS item id is required")
        return self.api.command(f"{self.command_base}/set", {".id": item_id, **fields})

    def remove(self, item_id):
        return self.api.command(f"{self.command_base}/remove", {".id": item_id})


class RouterOSAPI:
    def __init__(self, host, username, password, port=8728, timeout=4, secure=False, verify_ssl=False,
                 connect_timeout=None, read_timeout=None):
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port or (8729 if secure else 8728))
        self.connect_timeout = connect_timeout if connect_timeout is not None else min(timeout, 5)
        self.read_timeout = read_timeout if read_timeout is not None else max(timeout, 20)
        self.timeout = self.read_timeout
        self.secure = secure
        self.verify_ssl = verify_ssl
        self.sock = None

    def connect(self):
        raw = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        if self.secure:
            context = ssl.create_default_context()
            if not self.verify_ssl:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            self.sock = context.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw
        self.sock.settimeout(self.read_timeout)
        self.login()
        return self

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def path(self, *path):
        return RouterOSPath(self, tuple(path))

    def _write_word(self, word):
        data = str(word).encode("utf-8")
        length = len(data)
        if length < 0x80:
            prefix = bytes([length])
        elif length < 0x4000:
            prefix = bytes([(length >> 8) | 0x80, length & 0xFF])
        elif length < 0x200000:
            prefix = bytes([(length >> 16) | 0xC0, (length >> 8) & 0xFF, length & 0xFF])
        elif length < 0x10000000:
            prefix = bytes([(length >> 24) | 0xE0, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        else:
            prefix = bytes([0xF0, (length >> 24) & 0xFF, (length >> 16) & 0xFF, (length >> 8) & 0xFF, length & 0xFF])
        self.sock.sendall(prefix + data)

    def _read_length(self):
        first = self.sock.recv(1)
        if not first:
            raise RouterOSAPIError("RouterOS connection closed")
        value = first[0]
        if (value & 0x80) == 0:
            return value
        if (value & 0xC0) == 0x80:
            return ((value & ~0xC0) << 8) + self.sock.recv(1)[0]
        if (value & 0xE0) == 0xC0:
            chunk = self.sock.recv(2)
            return ((value & ~0xE0) << 16) + (chunk[0] << 8) + chunk[1]
        if (value & 0xF0) == 0xE0:
            chunk = self.sock.recv(3)
            return ((value & ~0xF0) << 24) + (chunk[0] << 16) + (chunk[1] << 8) + chunk[2]
        chunk = self.sock.recv(4)
        return (chunk[0] << 24) + (chunk[1] << 16) + (chunk[2] << 8) + chunk[3]

    def _read_word(self):
        length = self._read_length()
        if length == 0:
            return ""
        data = b""
        while len(data) < length:
            part = self.sock.recv(length - len(data))
            if not part:
                raise RouterOSAPIError("RouterOS connection closed while reading")
            data += part
        return data.decode("utf-8", errors="replace")

    def _write_sentence(self, words):
        for word in words:
            self._write_word(word)
        self._write_word("")

    def _read_sentence(self):
        words = []
        while True:
            word = self._read_word()
            if word == "":
                return words
            words.append(word)

    def _read_response(self):
        rows = []
        while True:
            sentence = self._read_sentence()
            if not sentence:
                continue
            tag = sentence[0]
            attrs = {}
            for word in sentence[1:]:
                if not word.startswith("="):
                    continue
                try:
                    _, key, value = word.split("=", 2)
                except ValueError:
                    continue
                attrs[key] = value
            if tag == "!re":
                rows.append(attrs)
            elif tag == "!done":
                return rows
            elif tag in {"!trap", "!fatal"}:
                raise RouterOSAPIError(attrs.get("message") or f"RouterOS API returned {tag}")

    def login(self):
        self.command("/login", {"name": self.username, "password": self.password})

    def command(self, command, attrs=None):
        words = [command]
        for key, value in (attrs or {}).items():
            if value is None:
                continue
            words.append(f"={key}={value}")
        self._write_sentence(words)
        return self._read_response()


def router_connect(tenant):
    return RouterOSAPI(
        host=tenant.get("mikrotik_host"),
        username=tenant.get("mikrotik_user"),
        password=tenant.get("mikrotik_pass"),
        port=int(tenant.get("mikrotik_port") or 8728),
        connect_timeout=int(tenant.get("mikrotik_connect_timeout") or 5),
        read_timeout=int(tenant.get("mikrotik_timeout") or 20),
        secure=int(tenant.get("mikrotik_port") or 8728) == 8729,
    ).connect()


def router_items(tenant, *path):
    if not has_mikrotik_credentials(tenant):
        return []
    api = router_connect(tenant)
    try:
        return list(api.path(*path).select())
    finally:
        api.close()


def router_first(tenant, *path):
    items = router_items(tenant, *path)
    return items[0] if items else {}


def router_update_item(tenant, path, item_id, fields):
    api = router_connect(tenant)
    try:
        return api.path(*path).update(**{".id": item_id, **fields})
    finally:
        api.close()


def router_add_item(tenant, path, fields):
    api = router_connect(tenant)
    try:
        return api.path(*path).add(**fields)
    finally:
        api.close()


def find_router_item_by_fields(api, path, fields):
    for item in api.path(*path).select():
        if all(str(item.get(key) or "") == str(value or "") for key, value in fields.items()):
            return item
    return None


def find_router_item(api, path, name):
    for item in api.path(*path).select():
        if item.get("name") == name:
            return item
    return None


def upsert_router_profile(tenant, path, name, speed, session_timeout=None):
    if not has_mikrotik_credentials(tenant):
        return None
    api = router_connect(tenant)
    try:
        router_path = api.path(*path)
        existing = find_router_item(api, path, name)
        fields = {"name": name}
        rate_limit = normalize_rate_limit(speed)
        if rate_limit:
            fields["rate-limit"] = rate_limit
        timeout = routeros_duration(session_timeout)
        if timeout and path in {("ppp", "profile"), ("ip", "hotspot", "user", "profile")}:
            fields["session-timeout"] = timeout
        if existing and existing.get(".id"):
            router_path.update(**{".id": existing[".id"], **fields})
            return existing[".id"]
        return router_path.add(**fields)
    finally:
        api.close()


def create_ppp_profile(tenant, name, speed, session_timeout=None):
    return upsert_router_profile(tenant, ("ppp", "profile"), name, speed, session_timeout)


def create_hotspot_profile(tenant, name, speed, session_timeout=None):
    return upsert_router_profile(tenant, ("ip", "hotspot", "user", "profile"), name, speed, session_timeout)


def package_service_type(package):
    service_type = str((package or {}).get("service_type") or "").strip().lower()
    return service_type if service_type in {"hotspot", "pppoe"} else "hotspot"


def captive_portal_url(tenant, base_url=None):
    """Build the URL the router should use to fetch hotspot files / redirect users to.

    base_url, when provided, should be the LIVE request host (e.g. from
    public_base_url(request).rstrip("/")) — this always wins, since it's the only
    value guaranteed to track a rotating local ngrok tunnel or the current
    production domain. Env vars / cached tenant fields are only used as a
    fallback for code paths with no request in scope (e.g. background tasks).
    """
    tenant_id = (tenant or {}).get("id")
    if base_url:
        base = normalize_public_url(base_url)
    else:
        configured = (
            os.getenv("CAPTIVE_PORTAL_PUBLIC_URL")
            or (tenant or {}).get("captive_portal_public_url")
            or os.getenv("SAAS_PORTAL_HOST")
            or ""
        )
        base = normalize_public_url(configured) or get_public_base_url()
    path = urlparse(base).path.rstrip("/")
    if "{tenant_id}" in base:
        return base.replace("{tenant_id}", str(tenant_id))
    if path.endswith(("/api/captive", "/portal")):
        return f"{base}/{tenant_id}"
    if f"/api/captive/{tenant_id}" in path or f"/portal/{tenant_id}" in path:
        return base
    return f"{base}/api/captive/{tenant_id}"


def captive_portal_host(tenant, base_url=None):
    return urlparse(captive_portal_url(tenant, base_url)).netloc.split("@")[-1].split(":")[0]


def mikrotik_managed_bridge_name(tenant=None):
    return str(
        os.getenv("MIKROTIK_BRIDGE_NAME")
        or (tenant or {}).get("mikrotik_bridge_name")
        or "billing-bridge"
    ).strip() or "billing-bridge"


def upsert_router_item(api, path, match_fields, fields):
    router_path = api.path(*path)
    existing = find_router_item_by_fields(api, path, match_fields)
    if existing and existing.get(".id"):
        router_path.update(**{".id": existing[".id"], **fields})
        return existing[".id"]
    return router_path.add(**fields)


def hotspot_portal_target(portal_url, extra_param):
    separator = "&" if "?" in str(portal_url or "") else "?"
    host = urlparse(str(portal_url or "")).netloc.lower()
    ngrok_param = "ngrok-skip-browser-warning=true&" if host.endswith("ngrok-free.dev") else ""
    return f"{portal_url}{separator}{ngrok_param}{extra_param}"


def hotspot_login_redirect_html(portal_url):
    target = hotspot_portal_target(portal_url, "ip=$(ip)&mac=$(mac)&router_ip=$(server-address)&error=$(error)")
    return (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<meta http-equiv='refresh' content='0; url={target}'>"
        "<title>Internet Access</title>"
        "</head><body>"
        f"<script>window.location.replace('{target}');</script>"
        f"<a href='{target}'>Open packages</a>"
        "</body></html>"
    )


def hotspot_error_redirect_html(portal_url):
    target = hotspot_portal_target(portal_url, "ip=$(ip)&mac=$(mac)&mikrotik_error=$(error)")
    return (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<meta http-equiv='refresh' content='0; url={target}'>"
        "<title>Internet Access</title>"
        "</head><body>"
        f"<script>window.location.replace('{target}');</script>"
        f"<a href='{target}'>Open packages</a>"
        "</body></html>"
    )


def hotspot_alogin_redirect_html(portal_url):
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Authorized</title></head><body>"
        "<div style='padding:20px; font-family:sans-serif; text-align:center;'>"
        "<h3>Access Granted</h3>"
        "<p>Please wait while your connection initializes...</p>"
        "</div>"
        "<script>"
        "var dest = '$(link-orig)';"
        f"window.location.replace(dest ? dest : '{portal_url}');"
        "</script>"
        f"<noscript><a href='{portal_url}'>Continue</a></noscript>"
        "</body></html>"
    )


def hotspot_redirect_html(portal_url=None):
    if portal_url:
        target = hotspot_portal_target(portal_url, "ip=$(ip)&mac=$(mac)&error=$(error)")
        return (
            "<!doctype html><html><head>"
            "<meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<meta http-equiv='refresh' content='0; url={target}'>"
            "<title>Internet Packages</title>"
            "</head><body>"
            f"<script>window.location.replace('{target}');</script>"
            f"<a href='{target}'>Open packages</a>"
            "</body></html>"
        )
    return (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<meta http-equiv='refresh' content='1; url=$(link-orig)'>"
        "<title>Redirecting...</title>"
        "</head><body>"
        "<p style='font-family:Arial,sans-serif;text-align:center;padding:20px'>Connecting...</p>"
        "<script>setTimeout(function(){window.location.href='$(link-orig)';},300);</script>"
        "</body></html>"
    )


def routeros_hotspot_file_script(files, log_prefix="Billing SaaS"):
    parts = []
    for index, (name, contents) in enumerate(files.items()):
        var_name = f"billingHotspotFile{index}"
        escaped_contents = _rsc_escape(contents)
        for target_name in (name, f"flash/{name}"):
            parts.append(
                f':local {var_name} "{escaped_contents}"; '
                f':local {var_name}Id [/file find name="{target_name}"]; '
                f':if ([:len ${var_name}Id] > 0) do={{ '
                f':do {{ /file set ${var_name}Id contents=${var_name} }} '
                f'on-error={{ :log warning "{log_prefix}: failed to update {target_name}" }} '
                f'}} else={{ '
                f':do {{ /file add name="{target_name}" contents=${var_name} }} '
                f'on-error={{ :log warning "{log_prefix}: failed to write {target_name}" }} '
                f'}};'
            )
    return " ".join(parts)


def routeros_hotspot_fetch_script(portal_url, log_prefix="Billing SaaS"):
    parsed = urlsplit(str(portal_url or ""))
    template_path = parsed.path.rstrip("/") + "/hotspot-file"
    portal_base = urlunsplit((parsed.scheme, parsed.netloc, template_path, parsed.query, ""))
    skip_warning = "ngrok-skip-browser-warning=true" if parsed.netloc.lower().endswith("ngrok-free.dev") else ""
    pages = ["login.html", "alogin.html", "redirect.html", "error.html", "status.html", "rlogin.html", "radvert.html"]
    parts = []
    for page in pages:
        src_url = f"{portal_base}/{page}" if not parsed.query else urlunsplit((parsed.scheme, parsed.netloc, f"{template_path}/{page}", parsed.query, ""))
        if skip_warning:
            src_url = f"{src_url}{'&' if '?' in src_url else '?'}{skip_warning}"
        src = _rsc_escape(src_url)
        for target_name in (f"hotspot/{page}", f"flash/hotspot/{page}"):
            dst = _rsc_escape(target_name)
            # Retry-once with 3s delay — a flaky first connection silently
            # leaves the captive portal broken until the next full re-sync.
            parts.append(
                f':do {{ /tool fetch url="{src}" dst-path="{dst}" }} '
                f'on-error={{ '
                f'  :log warning "{log_prefix}: first fetch failed for {dst}, retrying in 3s"; '
                f'  :delay 3s; '
                f'  :do {{ /tool fetch url="{src}" dst-path="{dst}" }} '
                f'  on-error={{ :log warning "{log_prefix}: retry also failed for {dst}" }} '
                f'}};'
            )
    return " ".join(parts)


def ensure_hotspot_login_redirect(api, portal_url):
    fallback_redirect_html = hotspot_redirect_html(portal_url)
    files_to_push = {
        "hotspot/login.html": hotspot_login_redirect_html(portal_url),
        "hotspot/alogin.html": hotspot_alogin_redirect_html(portal_url),
        "hotspot/redirect.html": fallback_redirect_html,
        "hotspot/error.html": hotspot_error_redirect_html(portal_url),
        "hotspot/status.html": fallback_redirect_html,
        "hotspot/rlogin.html": fallback_redirect_html,
        "hotspot/radvert.html": fallback_redirect_html,
    }
    existing_files = list(api.path("file").select())
    pushed = {}
    for name, contents in files_to_push.items():
        for target_name in (name, f"flash/{name}"):
            existing = next((item for item in existing_files if item.get("name") == target_name), None)
            try:
                if existing and existing.get(".id"):
                    api.path("file").update(**{".id": existing[".id"], "contents": contents})
                    pushed[target_name] = "updated"
                else:
                    api.path("file").add(**{"name": target_name, "contents": contents})
                    pushed[target_name] = "created"
            except Exception:
                pushed[target_name] = "skipped"
    return pushed


def walled_garden_hosts(tenant, portal_host=None):
    """Return the list of dst-host entries for the hotspot walled garden.

    Derives the list from the tenant's configured payment provider when
    possible (stored in tenant.extra["payment_provider"]), and always
    includes the captive portal host plus Cloudflare challenges domain
    (many gateways use Turnstile on their checkout pages).

    Supported payment_provider values:
      - "paystack"   (default when Paystack keys are configured)
      - "mpesa"      (M-Pesa STK push — no external hosts needed)
      - "pesapal"
      - "flutterwave"
      - "paypal"
      - "monnify"
      - "payfast"
    Falls back to a broad East-African default set when the provider is
    unrecognised or not set.
    """
    if not portal_host:
        portal_host = captive_portal_host(tenant)

    provider = ""
    if isinstance(tenant, dict):
        provider = str(tenant.get("payment_provider") or tenant.get("extra", {}).get("payment_provider") or "").strip().lower()
    else:
        provider = str(getattr(tenant, "extra", None) and (getattr(tenant, "extra") or {}).get("payment_provider") or "").strip().lower()

    # If no explicit provider, infer from configured keys
    if not provider:
        has_paystack = False
        if isinstance(tenant, dict):
            has_paystack = bool(tenant.get("paystack_secret_key"))
        else:
            has_paystack = bool(getattr(tenant, "paystack_secret_key", ""))
        if has_paystack:
            provider = "paystack"

    # Always include these
    hosts = [portal_host] if portal_host else []
    # Some hosted portals redirect through a subdomain/CDN hostname during
    # TLS or platform routing. Keep the configured host and its subdomains
    # reachable before Hotspot authentication.
    if portal_host and not portal_host.startswith("*."):
        hosts.append(f"*.{portal_host}")
    hosts.append("challenges.cloudflare.com")

    gateway_hosts = {
        "paystack": [
            "checkout.paystack.com",
            "api.paystack.co",
            "*.paystack.co",
            "*.paystack.com",
        ],
        "pesapal": [
            "*.pesapal.com",
        ],
        "flutterwave": [
            "checkout.flutterwave.com",
            "api.flutterwave.com",
            "*.flutterwave.com",
        ],
        "paypal": [
            "*.paypal.com",
        ],
        "monnify": [
            "api.monnify.com",
            "*.monnify.com",
        ],
        "payfast": [
            "*.payfast.co.za",
            "www.payfast.co.za",
        ],
        "mpesa": [
            # M-Pesa STK push is mobile-initiated — no browser-based
            # checkout hosts to whitelist.
        ],
    }

    if provider in gateway_hosts:
        hosts.extend(gateway_hosts[provider])
    else:
        # Broad default: include all common East-African gateways so no
        # tenant is blocked regardless of which provider they pick later.
        for gw_list in gateway_hosts.values():
            hosts.extend(gw_list)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def ensure_hotspot_captive_portal(tenant, base_url=None):
    if not has_mikrotik_credentials(tenant):
        return None

    portal_url = captive_portal_url(tenant, base_url)
    portal_host = captive_portal_host(tenant, base_url)
    profile_name = "billing-saas-captive"
    api = router_connect(tenant)
    try:
        upsert_router_item(
            api,
            ("ip", "hotspot", "profile"),
            {"name": profile_name},
            {
                "name": profile_name,
                "login-by": "http-pap,http-chap",
                "use-radius": "no",
                "radius-accounting": "no",
                "html-directory": "hotspot",
                "comment": f"billing-saas captive portal: {portal_url}",
            },
        )
        for host in walled_garden_hosts(tenant, portal_host):
            if not host:
                continue
            upsert_router_item(
                api,
                ("ip", "hotspot", "walled-garden"),
                {"dst-host": host, "comment": "billing-saas captive portal access"},
                {
                    "action": "allow",
                    "dst-host": host,
                    "comment": "billing-saas captive portal access",
                    "disabled": "no",
                },
            )
        login_page = None
        try:
            login_page = ensure_hotspot_login_redirect(api, portal_url)
        except Exception:
            login_page = None
        # A profile alone is not enough: the active Hotspot server must use
        # it or RouterOS will keep serving its default login/no-internet page.
        try:
            hotspot_servers = api.path("ip", "hotspot").select()
            for server in hotspot_servers:
                if server.get(".id"):
                    api.path("ip", "hotspot").update(**{".id": server[".id"], "profile": profile_name, "disabled": "no"})
        except Exception:
            pass
        return {"profile": profile_name, "portal_url": portal_url, "portal_host": portal_host, "login_page": login_page}
    finally:
        api.close()


def router_interface_status(tenant):
    if not has_mikrotik_credentials(tenant):
        return {}

    api = router_connect(tenant)
    try:
        def items(*path):
            return list(api.path(*path).select())

        resource = (items("system", "resource") or [{}])[0]
        routerboard = (items("system", "routerboard") or [{}])[0]
        interfaces = items("interface")
        pppoe_servers = items("interface", "pppoe-server", "server")
        hotspot_servers = items("ip", "hotspot")
        ppp_profiles = items("ppp", "profile")
        hotspot_profiles = items("ip", "hotspot", "user", "profile")
    finally:
        api.close()

    return {
        "device": {
            "board_name": resource.get("board-name") or routerboard.get("model"),
            "version": resource.get("version"),
            "uptime": resource.get("uptime"),
            "cpu_load": resource.get("cpu-load"),
            "free_memory": resource.get("free-memory"),
            "total_memory": resource.get("total-memory"),
            "architecture": resource.get("architecture-name"),
        },
        "interfaces": [
            {
                "id": item.get(".id"),
                "name": item.get("name"),
                "type": item.get("type"),
                "running": item.get("running") in {True, "true", "yes"},
                "disabled": item.get("disabled") in {True, "true", "yes"},
                "mac_address": item.get("mac-address"),
                "comment": item.get("comment", ""),
                "mtu": item.get("mtu"),
            }
            for item in interfaces
        ],
        "pppoe_servers": [
            {
                "id": item.get(".id"),
                "name": item.get("service-name") or item.get("name"),
                "interface": item.get("interface"),
                "default_profile": item.get("default-profile"),
                "disabled": item.get("disabled") in {True, "true", "yes"},
            }
            for item in pppoe_servers
        ],
        "hotspot_servers": [
            {
                "id": item.get(".id"),
                "name": item.get("name"),
                "interface": item.get("interface"),
                "profile": item.get("profile"),
                "disabled": item.get("disabled") in {True, "true", "yes"},
            }
            for item in hotspot_servers
        ],
        "profiles": {
            "pppoe": [{"name": item.get("name"), "rate_limit": item.get("rate-limit")} for item in ppp_profiles],
            "hotspot": [{"name": item.get("name"), "rate_limit": item.get("rate-limit")} for item in hotspot_profiles],
        },
    }


def _remove_port_from_any_bridge(api, interface_name):
    """Finds if an interface is inside any bridge port configuration and completely removes it."""
    for port in api.path("interface", "bridge", "port").select():
        if port.get("interface") == interface_name:
            try:
                api.path("interface", "bridge", "port").remove(port[".id"])
            except Exception:
                pass


def _clear_wireless_password_for_hotspot(api, interface_name):
    """Make assigned legacy WLAN interfaces open so Hotspot owns authentication."""
    wireless_rows = []
    try:
        wireless_rows = list(api.path("interface", "wireless").select())
    except Exception:
        return None

    wireless = next((item for item in wireless_rows if item.get("name") == interface_name), None)
    if not wireless or not wireless.get(".id"):
        return None

    profile_name = "billing-saas-open"
    try:
        profiles = list(api.path("interface", "wireless", "security-profiles").select())
        existing = next((item for item in profiles if item.get("name") == profile_name), None)
        fields = {
            "name": profile_name,
            "mode": "none",
            "authentication-types": "",
            "wpa-pre-shared-key": "",
            "wpa2-pre-shared-key": "",
            "supplicant-identity": "billing-saas",
        }
        if existing and existing.get(".id"):
            api.path("interface", "wireless", "security-profiles").update(**{".id": existing[".id"], **fields})
        else:
            api.path("interface", "wireless", "security-profiles").add(**fields)
    except Exception:
        pass

    try:
        api.path("interface", "wireless").update(**{".id": wireless[".id"], "security-profile": profile_name, "disabled": "no"})
        return profile_name
    except Exception:
        return None


def configure_router_port(tenant, interface_name, service_type, profile_name="default", base_url=None):
    service_type = str(service_type or "").lower().strip()
    if service_type not in {"pppoe", "hotspot"}:
        raise ValueError("Port service must be either pppoe or hotspot")

    api = router_connect(tenant)
    try:
        interfaces = list(api.path("interface").select())
        interface = next((item for item in interfaces if item.get("name") == interface_name), None)
        if not interface or not interface.get(".id"):
            raise ValueError("Router interface not found")

        # 1. Remove the interface from any existing bridge
        _remove_port_from_any_bridge(api, interface_name)

        # 2. Shift the interface into the billing-saas managed bridge
        managed_bridge = mikrotik_managed_bridge_name(tenant)
        existing_bridges = list(api.path("interface", "bridge").select())
        if not any(b.get("name") == managed_bridge for b in existing_bridges):
            api.path("interface", "bridge").add(name=managed_bridge, comment="billing-saas managed bridge")

        # Add the target interface to our managed bridge
        api.path("interface", "bridge", "port").add(bridge=managed_bridge, interface=interface_name)
        bind_interface = managed_bridge

        wireless_security_profile = None
        if service_type == "hotspot":
            wireless_security_profile = _clear_wireless_password_for_hotspot(api, interface_name)

        bridge_note = f"Interface '{interface_name}' successfully moved into billing-saas managed bridge '{managed_bridge}'."

        if service_type == "pppoe":
            api.path("interface").update(**{".id": interface[".id"], "comment": f"billing-saas:{service_type}:profile={profile_name or 'default'}"})
            servers = list(api.path("interface", "pppoe-server", "server").select())
            existing = next((item for item in servers if item.get("interface") == bind_interface), None)
            fields = {
                "service-name": f"billing-{interface_name}",
                "interface": bind_interface,
                "default-profile": profile_name or "default",
                "one-session-per-host": "yes",
                "disabled": "no",
            }
            if existing and existing.get(".id"):
                api.path("interface", "pppoe-server", "server").update(**{".id": existing[".id"], **fields})
                return {"updated": True, "service_type": service_type, "interface": interface_name, "bound_interface": bind_interface, "note": bridge_note}
            api.path("interface", "pppoe-server", "server").add(**fields)
            return {"created": True, "service_type": service_type, "interface": interface_name, "bound_interface": bind_interface, "note": bridge_note}

        captive = ensure_hotspot_captive_portal(tenant, base_url) or {}
        hotspot_profile = captive.get("profile") or "billing-saas-captive"
        api.path("interface").update(**{".id": interface[".id"], "comment": f"billing-saas:hotspot:portal={captive.get('portal_url') or ''}".strip()})
        
        servers = list(api.path("ip", "hotspot").select())
        existing = next((item for item in servers if item.get("interface") == bind_interface), None)
        fields = {
            "name": f"billing-hotspot-{interface_name}",
            "interface": bind_interface,
            "profile": hotspot_profile,
            "disabled": "no",
            "comment": f"billing-saas captive portal: {captive.get('portal_url') or ''}".strip(),
        }
        if existing and existing.get(".id"):
            api.path("ip", "hotspot").update(**{".id": existing[".id"], **fields})
            return {"updated": True, "service_type": service_type, "interface": interface_name, "bound_interface": bind_interface, "profile": hotspot_profile, "portal_url": captive.get("portal_url"), "wireless_security_profile": wireless_security_profile, "note": bridge_note}
        api.path("ip", "hotspot").add(**fields)
        return {"created": True, "service_type": service_type, "interface": interface_name, "bound_interface": bind_interface, "profile": hotspot_profile, "portal_url": captive.get("portal_url"), "wireless_security_profile": wireless_security_profile, "note": bridge_note}
    finally:
        api.close()


def _rsc_escape(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")


def _build_port_command_script(interface_name, service_type, profile_name, portal_url, bridge_name=None, tenant=None):
    bridge_name = bridge_name or mikrotik_managed_bridge_name()
    portal_comment = portal_url or ""
    portal_host = urlparse(portal_url or "").netloc.split("@")[-1].split(":")[0]

    # --- Hotspot file writes with LOGGED errors and verification ---
    hotspot_file_writes = ""
    if portal_url:
        hotspot_file_writes = routeros_hotspot_fetch_script(portal_url)

    # --- Default PPPoE / Hotspot secret cleanup ---
    # Remove any pre-existing /ppp secret entries that are NOT managed by us
    ppp_secret_cleanup = (
        f':foreach s in=[/ppp secret find] do={{ '
        f'  :if ([/ppp secret get $s comment] != "billing-saas-managed") do={{ '
        f'    :do {{ /ppp secret remove $s }} on-error={{}} '
        f'  }} '
        f'}}; '
    )

    hotspot_setup = ""
    if portal_url:
        hotspot_setup = (
            f':do {{ /ip hotspot profile add name="billing-saas-captive" login-by=http-pap,http-chap use-radius=no radius-accounting=no html-directory=hotspot comment="billing-saas captive portal: {portal_comment}" }} '
            f'on-error={{ /ip hotspot profile set [find name="billing-saas-captive"] login-by=http-pap,http-chap use-radius=no radius-accounting=no html-directory=hotspot comment="billing-saas captive portal: {portal_comment}" }}; '
            + "".join(
                f':do {{ /ip hotspot walled-garden add action=allow dst-host="{_rsc_escape(h)}" comment="billing-saas captive portal access" }} on-error={{ :log warning "Billing SaaS: walled-garden add failed" }}; '
                for h in walled_garden_hosts(tenant, portal_host)
            ) + ' '
            f':local billingPortalIp ""; '
            f':do {{ :set billingPortalIp [:resolve "{portal_host}"] }} on-error={{ :log warning "Billing SaaS portal DNS resolve failed" }}; '
            f':if ([:len $billingPortalIp] > 0) do={{ '
            f':do {{ /ip hotspot walled-garden ip add action=accept dst-address=$billingPortalIp protocol=tcp dst-port=80 comment="billing-saas captive portal access" }} on-error={{ :log warning "Billing SaaS: walled-garden ip add failed" }}; '
            f':do {{ /ip hotspot walled-garden ip add action=accept dst-address=$billingPortalIp protocol=tcp dst-port=443 comment="billing-saas captive portal access" }} on-error={{ :log warning "Billing SaaS: walled-garden ip add failed" }}; '
            f'}}; '
            f'{hotspot_file_writes}'
        )

    # --- Default PPPoE server creation (at provisioning time, not lazy per-port) ---
    pppoe_server_block = (
        f'  :local billingSvc [/interface pppoe-server server find interface="{bridge_name}"]; '
        f'  :if ([:len $billingSvc] > 0) do={{ /interface pppoe-server server set $billingSvc service-name="billing-{interface_name}" default-profile="{profile_name}" one-session-per-host=yes disabled=no }} else={{ /interface pppoe-server server add service-name="billing-{interface_name}" interface="{bridge_name}" default-profile="{profile_name}" one-session-per-host=yes disabled=no }}; '
    )

    # --- Hotspot server creation ---
    hotspot_server_block = (
        f'  {hotspot_setup}'
        f'  :do {{ /interface wireless security-profiles add name="billing-saas-open" mode=none authentication-types="" wpa-pre-shared-key="" wpa2-pre-shared-key="" supplicant-identity="billing-saas" }} on-error={{ /interface wireless security-profiles set [find name="billing-saas-open"] mode=none authentication-types="" wpa-pre-shared-key="" wpa2-pre-shared-key="" supplicant-identity="billing-saas" }}; '
        f'  :do {{ /interface wireless set [find name="{interface_name}"] security-profile="billing-saas-open" disabled=no }} on-error={{}}; '
        f'  :local billingHs [/ip hotspot find interface="{bridge_name}"]; '
        f'  :if ([:len $billingHs] > 0) do={{ /ip hotspot set $billingHs name="billing-hotspot-{interface_name}" profile="billing-saas-captive" disabled=no comment="billing-saas captive portal: {portal_comment}" }} else={{ /ip hotspot add name="billing-hotspot-{interface_name}" interface="{bridge_name}" profile="billing-saas-captive" disabled=no comment="billing-saas captive portal: {portal_comment}" }}; '
    )

    # --- Default secret cleanup (remove factory defaults so RADIUS is the only auth path) ---
    cleanup_block = ppp_secret_cleanup

    return (
        f'/interface bridge port remove [find interface="{interface_name}"]; '
        f':if ([:len [/interface bridge find name="{bridge_name}"]] = 0) do={{ /interface bridge add name="{bridge_name}" comment="billing-saas managed bridge" }}; '
        f'/interface bridge port add bridge="{bridge_name}" interface="{interface_name}"; '
        f':do {{ /interface set [find name="{interface_name}"] comment="billing-saas:{service_type}:portal={portal_comment}" }} on-error={{ :log warning "Billing SaaS: failed to set interface comment" }}; '
        f'{cleanup_block}'
        f':if ("{service_type}" = "pppoe") do={{ '
        f'  {pppoe_server_block}'
        f'}} else={{ '
        f'  {hotspot_server_block}'
        f'}}; '
    )


def upsert_customer_access(tenant, customer, disabled=False):
    service_type = customer.get("service_type") or "pppoe"
    # When RADIUS is enabled, skip the RouterOS API call entirely.
    # The router will ask the RADIUS server at login time, so there is
    # nothing to push. The radius_secret is managed by sync_radius_customer.
    tenant_radius_enabled = tenant.get("radius_enabled") if isinstance(tenant, dict) else getattr(tenant, "radius_enabled", False)
    if tenant_radius_enabled and service_type != "pppoe":
        return {"skipped": True, "reason": "RADIUS enabled — auth handled by RADIUS server, no RouterOS API call needed"}

    if not has_mikrotik_credentials(tenant):
        return None
    api = router_connect(tenant)
    try:
        if service_type == "tv":
            mac_address = str(customer.get("mac_address") or customer.get("username") or "").strip().upper()
            if not mac_address:
                return None
            path = ("ip", "hotspot", "ip-binding")
            router_path = api.path(*path)
            existing = find_router_item_by_fields(api, path, {"mac-address": mac_address})
            fields = {
                "mac-address": mac_address,
                "type": "bypassed",
                "comment": f"billing-saas tv access: {customer.get('package_name') or customer.get('package') or ''}".strip(),
                "disabled": "yes" if disabled else "no",
            }
            if existing and existing.get(".id"):
                router_path.update(**{".id": existing[".id"], **fields})
                return existing[".id"]
            return router_path.add(**fields)
        path = ("ppp", "secret") if service_type == "pppoe" else ("ip", "hotspot", "user")
        router_path = api.path(*path)
        existing = find_router_item(api, path, customer.get("username"))
        
        # Explicit password stripping or validation logic per specifications
        fields = {
            "name": customer.get("username"),
            "password": customer.get("password"),
            "profile": customer.get("package_name") or customer.get("package"),
            "disabled": "yes" if disabled else "no",
            "comment": f"billing-saas access expires: {customer.get('expires_at') or customer.get('expiry_date') or ''}".strip(),
        }
        if service_type == "pppoe":
            fields["password"] = customer.get("password")  # Keep for PPPoE authentication
            fields["service"] = "pppoe"
        else:
            limit_uptime = routeros_duration(customer.get("duration_seconds") or customer.get("limit_seconds"))
            if limit_uptime:
                fields["limit-uptime"] = limit_uptime
        if existing and existing.get(".id"):
            router_path.update(**{".id": existing[".id"], **fields})
            if service_type == "hotspot" and not disabled and (customer.get("duration_seconds") or customer.get("limit_seconds")):
                try:
                    api.command("/ip/hotspot/user/reset-counters", {"numbers": existing[".id"]})
                except Exception:
                    pass
            return existing[".id"]
        item_id = router_path.add(**fields)
        if service_type == "hotspot" and item_id and not disabled and (customer.get("duration_seconds") or customer.get("limit_seconds")):
            try:
                api.command("/ip/hotspot/user/reset-counters", {"numbers": item_id})
            except Exception:
                pass
        return item_id
    finally:
        api.close()


def set_customer_enabled(tenant, username, service_type="hotspot", enabled=True):
    # When RADIUS is enabled, use CoA Disconnect instead of RouterOS API
    tenant_id = tenant.get("id") if isinstance(tenant, dict) else str(tenant.id)
    tenant_radius_enabled = tenant.get("radius_enabled") if isinstance(tenant, dict) else tenant.radius_enabled

    if tenant_radius_enabled and not enabled:
        try:
            from .models import Tenant as TenantModel, Customer as CustomerModel
            from .radius_coa import radius_disconnect_customer

            tenant_obj = TenantModel.objects.get(pk=tenant_id) if isinstance(tenant, dict) else tenant
            result = radius_disconnect_customer(tenant_obj, username)
            # Also update Postgres Customer.status so the RADIUS server
            # rejects future Access-Requests for this user.
            try:
                CustomerModel.objects.filter(
                    tenant=tenant_obj, username=username
                ).update(status="inactive")
            except Exception:
                pass
            if result.get("success"):
                return result
            # If CoA failed, fall through to the direct API path
        except Exception:
            pass  # Fall through to direct API path

    if tenant_radius_enabled and enabled:
        try:
            from .models import Customer as CustomerModel
            tenant_obj = TenantModel.objects.get(pk=tenant_id) if isinstance(tenant, dict) else tenant
            CustomerModel.objects.filter(
                tenant=tenant_obj, username=username
            ).update(status="active")
        except Exception:
            pass

    if not has_mikrotik_credentials(tenant):
        return None
    api = router_connect(tenant)
    try:
        if service_type == "tv":
            path = ("ip", "hotspot", "ip-binding")
            existing = find_router_item_by_fields(api, path, {"mac-address": str(username or "").strip().upper()})
            if not existing or not existing.get(".id"):
                return None
            return api.path(*path).update(**{".id": existing[".id"], "disabled": "no" if enabled else "yes"})
        path = ("ppp", "secret") if service_type == "pppoe" else ("ip", "hotspot", "user")
        existing = find_router_item(api, path, username)
        if not existing or not existing.get(".id"):
            return None
        result = api.path(*path).update(**{".id": existing[".id"], "disabled": "no" if enabled else "yes"})

        active_path = ("ppp", "active") if service_type == "pppoe" else ("ip", "hotspot", "active")
        match_field = "name" if service_type == "pppoe" else "user"
        active = find_router_item_by_fields(api, active_path, {match_field: username})
        if not enabled:
            if active and active.get(".id"):
                try:
                    api.path(*active_path).remove(active[".id"])
                except Exception:
                    pass
        elif active and active.get(".id") and service_type == "hotspot":
            try:
                api.path(*active_path).remove(active[".id"])
            except Exception:
                pass

        return result
    finally:
        api.close()


def delete_router_customer(tenant, username, service_type="pppoe"):
    if not has_mikrotik_credentials(tenant) or not username:
        return None
    api = router_connect(tenant)
    try:
        if service_type == "tv":
            path = ("ip", "hotspot", "ip-binding")
            existing = find_router_item_by_fields(api, path, {"mac-address": str(username or "").strip().upper()})
            if not existing or not existing.get(".id"):
                return None
            return api.path(*path).remove(existing[".id"])
        path = ("ppp", "secret") if service_type == "pppoe" else ("ip", "hotspot", "user")
        existing = find_router_item(api, path, username)
        if not existing or not existing.get(".id"):
            return None
        return api.path(*path).remove(existing[".id"])
    finally:
        api.close()


def whatsapp_enabled(tenant=None):
    if tenant and tenant.get("whatsapp_enabled") is False:
        return False
    return os.getenv("WHATSAPP_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def send_whatsapp_message(phone, message, tenant=None):
    if not whatsapp_enabled(tenant):
        return {"sent": False, "skipped": "disabled"}
    token = os.getenv("WHATSAPP_API_TOKEN") or os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    api_url = os.getenv("WHATSAPP_API_URL", "").strip()
    if not api_url and phone_number_id:
        version = os.getenv("WHATSAPP_API_VERSION", "v20.0")
        api_url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    if not token or not api_url:
        return {"sent": False, "skipped": "missing_credentials"}

    recipient = normalize_phone(phone)
    if not recipient:
        return {"sent": False, "skipped": "missing_phone"}

    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": str(message or "")},
    }
    response = requests.post(
        api_url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    return {"sent": True, "response": response.json() if response.content else {}}


def send_sms_message(phone, message, tenant=None):
    """Send through the shared SMS gateway; a tenant sender ID is optional."""
    if tenant and tenant.get("sms_enabled") is False:
        return {"sent": False, "skipped": "disabled"}
    api_url = (os.getenv("SMS_API_URL") or os.getenv("ROAMTECH_API_URL") or "").strip()
    token = os.getenv("SMS_API_TOKEN") or os.getenv("ROAMTECH_API_TOKEN") or os.getenv("ROAMTECH_TOKEN")
    recipient = normalize_phone(phone)
    if not recipient:
        return {"sent": False, "skipped": "missing_phone"}
    if not api_url or not token:
        return {"sent": False, "skipped": "missing_credentials"}
    sender = str((tenant or {}).get("roamtech_sender_id") or os.getenv("SMS_DEFAULT_SENDER_ID") or os.getenv("ROAMTECH_DEFAULT_SENDER_ID") or "EXPRESS WIFI").strip()
    response = requests.post(api_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json={"to": recipient, "message": str(message or ""), "from": sender, "sender_id": sender}, timeout=20)
    response.raise_for_status()
    return {"sent": True, "response": response.json() if response.content else {}}


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
