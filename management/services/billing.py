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



from core.services.shared import *

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
    base = daraja_base_url(tenant)
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

def _clean_daraja_config(source):
    source = source or {}
    return {
        "daraja_consumer_key": str(source.get("daraja_consumer_key") or "").strip(),
        "daraja_consumer_secret": str(source.get("daraja_consumer_secret") or "").strip(),
        "daraja_shortcode": str(source.get("daraja_shortcode") or "").strip(),
        "daraja_passkey": str(source.get("daraja_passkey") or "").strip(),
        "daraja_till_number": str(source.get("daraja_till_number") or "").strip(),
        "daraja_environment": str(source.get("daraja_environment") or "production").strip(),
        "daraja_shortcode_type": str(source.get("daraja_shortcode_type") or "CustomerPayBillOnline").strip(),
    }


def _platform_only_daraja_config():
    mpesa_environment = os.getenv("MPESA_ENVIRONMENT")
    use_mpesa_aliases_first = str(mpesa_environment or "").strip().lower() == "sandbox"
    consumer_key = os.getenv("MPESA_CONSUMER_KEY") if use_mpesa_aliases_first else None
    consumer_secret = os.getenv("MPESA_CONSUMER_SECRET") if use_mpesa_aliases_first else None
    shortcode = (os.getenv("MPESA_SHORTCODE") or os.getenv("MPESA_BUSINESS_SHORTCODE")) if use_mpesa_aliases_first else None
    passkey = os.getenv("MPESA_PASSKEY") if use_mpesa_aliases_first else None
    till_number = (os.getenv("MPESA_TILL_NUMBER") or os.getenv("MPESA_BUSINESS_SHORTCODE")) if use_mpesa_aliases_first else None
    shortcode_type = os.getenv("MPESA_SHORTCODE_TYPE") if use_mpesa_aliases_first else None
    return {
        "daraja_consumer_key": consumer_key or os.getenv("DARAJA_CONSUMER_KEY") or os.getenv("MPESA_CONSUMER_KEY"),
        "daraja_consumer_secret": consumer_secret or os.getenv("DARAJA_CONSUMER_SECRET") or os.getenv("MPESA_CONSUMER_SECRET"),
        "daraja_shortcode": shortcode or os.getenv("DARAJA_SHORTCODE") or os.getenv("MPESA_SHORTCODE") or os.getenv("MPESA_BUSINESS_SHORTCODE"),
        "daraja_passkey": passkey or os.getenv("DARAJA_PASSKEY") or os.getenv("MPESA_PASSKEY"),
        "daraja_till_number": till_number or os.getenv("DARAJA_TILL_NUMBER") or os.getenv("MPESA_TILL_NUMBER") or os.getenv("MPESA_BUSINESS_SHORTCODE"),
        "daraja_environment": mpesa_environment or os.getenv("DARAJA_ENVIRONMENT") or "production",
        "daraja_shortcode_type": shortcode_type or os.getenv("DARAJA_SHORTCODE_TYPE") or os.getenv("MPESA_SHORTCODE_TYPE") or "CustomerPayBillOnline",
    }


def platform_daraja_config(tenant=None, payment_method=None):
    tenant = tenant or {}
    method = selected_daraja_method_raw(tenant, payment_method)
    tenant_config = _clean_daraja_config(tenant)
    platform_config = _clean_daraja_config(_platform_only_daraja_config())
    if daraja_is_configured(tenant_config, method):
        source = "tenant"
        config = tenant_config
    else:
        source = "platform"
        config = platform_config
    return {
        **tenant,
        **config,
        "daraja_credential_source": source,
    }


def tenant_payout_details(tenant):
    tenant = tenant or {}
    return {
        "business_number": str(tenant.get("business_number") or "").strip(),
        "bank_code": str(tenant.get("bank_code") or "").strip(),
        "bank_name": str(tenant.get("bank_name") or "").strip(),
        "bank_account_number": str(tenant.get("bank_account_number") or "").strip(),
        "payout_phone": str(tenant.get("payout_phone") or tenant.get("phone") or "").strip(),
    }


def daraja_is_configured(tenant, payment_method=None):
    tenant = tenant or {}
    common = all(str(tenant.get(field) or "").strip() for field in ("daraja_consumer_key", "daraja_consumer_secret", "daraja_passkey"))
    if payment_method == "daraja_buygoods":
        return common and all(str(tenant.get(field) or "").strip() for field in ("daraja_shortcode", "daraja_till_number"))
    return common and bool(str(tenant.get("daraja_shortcode") or "").strip())


def tenant_uses_daraja(tenant):
    return daraja_is_configured(platform_daraja_config(tenant), selected_daraja_method(tenant))


def selected_daraja_method_raw(tenant, requested_method=None):
    tenant = tenant or {}
    requested = str(requested_method or "").strip().lower()
    if requested in {"paybill", "mpesa_paybill"}:
        return "daraja_paybill"
    if requested in {"buygoods", "buy_goods", "mpesa_buygoods"}:
        return "daraja_buygoods"
    if requested in {"daraja_paybill", "daraja_buygoods"}:
        return requested
    methods = tenant.get("payment_methods") if isinstance(tenant.get("payment_methods"), list) else []
    selected = ""
    for item in methods:
        value = str(item).strip().lower()
        if value in {"paybill", "mpesa_paybill"}:
            selected = "daraja_paybill"
            break
        if value in {"buygoods", "buy_goods", "mpesa_buygoods"}:
            selected = "daraja_buygoods"
            break
        if value in {"daraja_paybill", "daraja_buygoods"}:
            selected = value
            break
    if selected:
        return selected
    shortcode_type = str(tenant.get("daraja_shortcode_type") or "").strip().lower()
    if shortcode_type == "customerbuygoodsonline":
        return "daraja_buygoods"
    if shortcode_type == "customerpaybillonline":
        return "daraja_paybill"
    default_method = str(os.getenv("DARAJA_DEFAULT_METHOD") or "").strip().lower()
    if default_method in {"daraja_paybill", "daraja_buygoods"}:
        return default_method
    return "daraja_paybill"


def selected_daraja_method(tenant, requested_method=None):
    return selected_daraja_method_raw(tenant, requested_method)


def daraja_environment_name(tenant):
    environment = str((tenant or {}).get("daraja_environment") or "production").strip().lower()
    return "sandbox" if environment in {"sandbox", "test", "testing"} else "production"


def daraja_base_url(tenant):
    return "https://api.safaricom.co.ke" if daraja_environment_name(tenant) == "production" else "https://sandbox.safaricom.co.ke"


def get_daraja_credentials(tenant, payment_method="daraja_paybill"):
    tenant = platform_daraja_config(tenant, payment_method)
    consumer_key = str(tenant.get("daraja_consumer_key") or "").strip()
    consumer_secret = str(tenant.get("daraja_consumer_secret") or "").strip()
    shortcode = str(tenant.get("daraja_shortcode") or "").strip()
    passkey = str(tenant.get("daraja_passkey") or "").strip()
    till_number = str(tenant.get("daraja_till_number") or "").strip()
    shortcode_type = str(tenant.get("daraja_shortcode_type") or "CustomerPayBillOnline").strip()
    shortcode_type_method = "daraja_buygoods" if shortcode_type.lower() == "customerbuygoodsonline" else "daraja_paybill"
    if payment_method not in {"daraja_paybill", "daraja_buygoods"}:
        payment_method = shortcode_type_method
    business_number = shortcode
    till_required = payment_method == "daraja_buygoods"
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
    if till_required and not till_number:
        logger.warning("Daraja Buy Goods till number missing for tenant=%s", tenant.get("id"))
        raise PaymentProviderError(
            "M-Pesa Buy Goods is not set up for this business yet. Please contact support.",
            "Tenant is missing Daraja Buy Goods till number",
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
    payment_method = selected_daraja_method(tenant, payment_method)
    tenant = platform_daraja_config(tenant, payment_method)
    creds = get_daraja_credentials(tenant, payment_method)
    shortcode_type = str(creds.get("shortcode_type") or "CustomerPayBillOnline").strip()
    if payment_method not in {"daraja_paybill", "daraja_buygoods"}:
        payment_method = "daraja_buygoods" if shortcode_type.lower() == "customerbuygoodsonline" else "daraja_paybill"
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
    business_shortcode = creds["shortcode"]
    timestamp, password = daraja_timestamp_and_password(business_shortcode, creds["passkey"])
    callback_token = make_daraja_callback_token(tenant_id, payment_id)

    party_b = creds["till_number"] if payment_method == "daraja_buygoods" else business_shortcode
    transaction_type = "CustomerBuyGoodsOnline" if payment_method == "daraja_buygoods" else "CustomerPayBillOnline"
    logger.info(
        "Daraja STK config tenant=%s payment=%s environment=%s method=%s shortcode=%s transaction_type=%s callback_base_set=%s",
        tenant_id,
        payment_id,
        tenant.get("daraja_environment"),
        payment_method,
        business_shortcode,
        transaction_type,
        bool(base_url),
    )
    payload = {
        "BusinessShortCode": business_shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": transaction_type,
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


def query_daraja_stk_payment(tenant, checkout_request_id, payment_method="daraja_paybill"):
    payment_method = selected_daraja_method(tenant, payment_method)
    tenant = platform_daraja_config(tenant, payment_method)
    creds = get_daraja_credentials(tenant, payment_method)
    token = get_daraja_access_token(tenant, payment_method)
    timestamp, password = daraja_timestamp_and_password(creds["shortcode"], creds["passkey"])
    payload = {
        "BusinessShortCode": creds["shortcode"],
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }
    response = requests.post(
        f"{daraja_base_url(tenant)}/mpesa/stkpushquery/v1/query",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        logger.warning(
            "Daraja STK query failed for tenant=%s checkout=%s status=%s response=%s",
            (tenant or {}).get("id"),
            checkout_request_id,
            response.status_code,
            response.text[:500],
        )
        raise PaymentProviderError(
            "Payment verification is temporarily unavailable. Please wait a moment.",
            f"Daraja STK query failed {response.status_code}: {response.text[:500]}",
            503,
        ) from exc
    try:
        return response.json()
    except ValueError as exc:
        raise PaymentProviderError(
            "Payment verification is temporarily unavailable. Please wait a moment.",
            f"Daraja STK query returned invalid JSON: {response.text[:500]}",
            503,
        ) from exc


def initiate_daraja_b2c(tenant, payment_id, amount, phone, remarks=None):
    tenant = platform_daraja_config(tenant)
    shortcode = str(tenant.get("daraja_b2c_shortcode") or os.getenv("DARAJA_B2C_SHORTCODE") or tenant.get("daraja_shortcode") or "").strip()
    initiator = str(tenant.get("daraja_b2c_initiator_name") or os.getenv("DARAJA_B2C_INITIATOR_NAME") or "").strip()
    security_credential = str(tenant.get("daraja_b2c_security_credential") or os.getenv("DARAJA_B2C_SECURITY_CREDENTIAL") or "").strip()
    recipient = daraja_phone_format(phone)
    if not all([shortcode, initiator, security_credential]):
        raise PaymentProviderError(
            "Tenant settlement is not fully configured.",
            "Missing Daraja B2C shortcode, initiator name, or security credential",
            503,
        )
    if not recipient or not recipient.startswith("254") or len(recipient) != 12:
        raise PaymentProviderError("Tenant payout phone is invalid.", "Invalid Daraja B2C recipient phone", 400)

    base_url = get_public_base_url()
    if not base_url:
        raise RuntimeError("PUBLIC_APP_URL or PAYSTACK_CALLBACK_BASE_URL is required for Daraja B2C callbacks")

    token = get_daraja_access_token(tenant)
    payload = {
        "InitiatorName": initiator,
        "SecurityCredential": security_credential,
        "CommandID": str(os.getenv("DARAJA_B2C_COMMAND_ID") or "BusinessPayment"),
        "Amount": max(1, int(round(float(amount or 0)))),
        "PartyA": shortcode,
        "PartyB": recipient,
        "Remarks": str(remarks or f"Tenant settlement {payment_id}")[:100],
        "QueueTimeOutURL": f"{base_url}/api/daraja/b2c/timeout",
        "ResultURL": f"{base_url}/api/daraja/b2c/result",
        "Occasion": str(payment_id)[:100],
    }
    response = requests.post(
        f"{daraja_base_url(tenant)}/mpesa/b2c/v1/paymentrequest",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise PaymentProviderError(
            "Tenant settlement could not be requested.",
            f"Daraja B2C request failed {response.status_code}: {response.text[:500]}",
            502,
        ) from exc
    data = response.json()
    if str(data.get("ResponseCode")) not in {"0", "00"}:
        raise PaymentProviderError(
            "Tenant settlement could not be requested.",
            data.get("ResponseDescription") or data.get("errorMessage") or f"Daraja B2C rejected request: {data}",
            502,
        )
    return data



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


