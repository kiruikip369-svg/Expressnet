"""
Lightweight RADIUS server built on pyrad, running inside the Django process.

Handles:
  - Access-Request  (PPPoE / Hotspot auth)
  - Accounting-Request  (Start / Interim-Update / Stop)

Run via:  python manage.py runradius
"""

import logging
import os
import threading
import time
from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime
import pyrad.packet
from pyrad.server import Server, RemoteHost
from pyrad.dictionary import Dictionary

from .models import Customer, InternetPackage, RadiusNasClient, RadiusSession, Voucher

logger = logging.getLogger(__name__)

# RADIUS attribute constants used for MikroTik vendor-specific attributes
MIKROTIK_VENDOR_ID = 14988
ATTR_MIKROTIK_RATE_LIMIT = "Mikrotik-Rate-Limit"

# Standard RADIUS attribute names expected from pyrad dictionary
ATTR_USER_NAME = "User-Name"
ATTR_USER_PASSWORD = "User-Password"
ATTR_NAS_IP_ADDRESS = "NAS-IP-Address"
ATTR_CALLED_STATION_ID = "Called-Station-Id"
ATTR_ACCT_STATUS_TYPE = "Acct-Status-Type"
ATTR_ACCT_SESSION_ID = "Acct-Session-Id"
ATTR_ACCT_INPUT_OCTETS = "Acct-Input-Octets"
ATTR_ACCT_OUTPUT_OCTETS = "Acct-Output-Octets"
ATTR_ACCT_SESSION_TIME = "Acct-Session-Time"
ATTR_FRAMED_IP_ADDRESS = "Framed-IP-Address"
ATTR_SESSION_TIMEOUT = "Session-Timeout"
ATTR_IDLE_TIMEOUT = "Idle-Timeout"
ATTR_TERMINATE_CAUSE = "Acct-Terminate-Cause"
ATTR_SERVICE_TYPE = "Service-Type"
ATTR_MIKROTIK_TOTAL_LIMIT = "Mikrotik-Total-Limit"
ATTR_MIKROTIK_RECV_LIMIT = "Mikrotik-Recv-Limit"
ATTR_MIKROTIK_XMIT_LIMIT = "Mikrotik-Xmit-Limit"

ACCT_START = 1
ACCT_INTERIM_UPDATE = 3
ACCT_STOP = 2


def _load_or_create_dictionary():
    """Load the local dictionary so MikroTik VSAs are always available."""
    dict_path = os.path.join(os.path.dirname(__file__), "radius_dictionary.dict")
    if os.path.exists(dict_path):
        try:
            return Dictionary(dict_path)
        except Exception:
            logger.exception("RADIUS: failed to load local dictionary %s", dict_path)
    try:
        return Dictionary("dictionary")
    except Exception:
        logger.warning("RADIUS: falling back to empty dictionary; named attributes may fail")
        return Dictionary()


def _get_radius_host():
    """Return the IP the RADIUS server should bind to."""
    return os.getenv("RADIUS_HOST", "0.0.0.0")


def _get_radius_auth_port():
    """Return the auth port (default 1812)."""
    return int(os.getenv("RADIUS_AUTH_PORT", "1812"))


def _get_radius_acct_port():
    """Return the accounting port (default 1813)."""
    return int(os.getenv("RADIUS_ACCT_PORT", "1813"))


def _attr(pkt, name, default=""):
    value = pkt.GetAttribute(name)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value if value is not None else default


def _reject(server, pkt, reason, **metadata):
    username = metadata.pop("username", _attr(pkt, ATTR_USER_NAME, ""))
    nas_ip = metadata.pop("nas_ip", pkt.source[0] if pkt.source else "")
    logger.info("RADIUS Access-Reject: user=%s nas=%s reason=%s metadata=%s", username, nas_ip, reason, metadata)
    reply = server.CreateReplyPacket(pkt, **{"Reply-Message": str(reason)})
    reply.code = pyrad.packet.AccessReject
    return reply.SendTo(pkt.source)


def _parse_datetime(value):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _positive_int(*values):
    for value in values:
        if value in (None, ""):
            continue
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _bytes_from_value(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value) if value > 0 else None
    raw = str(value).strip().lower().replace(" ", "")
    multipliers = {
        "kb": 1000, "k": 1000,
        "mb": 1000 ** 2, "m": 1000 ** 2,
        "gb": 1000 ** 3, "g": 1000 ** 3,
        "tb": 1000 ** 4, "t": 1000 ** 4,
        "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
    }
    for suffix, multiplier in sorted(multipliers.items(), key=lambda item: len(item[0]), reverse=True):
        if raw.endswith(suffix):
            try:
                return int(float(raw[:-len(suffix)]) * multiplier)
            except ValueError:
                return None
    try:
        number = int(float(raw))
        return number if number > 0 else None
    except ValueError:
        return None


def _package_policy(package):
    if not package:
        return {}
    from .services import normalize_rate_limit

    extra = package.extra or {}
    duration_hours = _positive_int(extra.get("duration_hours"), extra.get("duration_value") if extra.get("duration_unit") == "hours" else None)
    session_timeout = _positive_int(extra.get("session_timeout"), extra.get("session_timeout_seconds"), extra.get("limit_seconds"))
    if not session_timeout:
        if duration_hours:
            session_timeout = duration_hours * 3600
        elif package.duration_days:
            session_timeout = int(timedelta(days=package.duration_days).total_seconds())
    return {
        "rate_limit": normalize_rate_limit(package.speed or "") if package.speed else None,
        "session_timeout": session_timeout,
        "idle_timeout": _positive_int(extra.get("idle_timeout"), extra.get("idle_timeout_seconds"), 300),
        "data_quota": _bytes_from_value(extra.get("data_quota") or extra.get("data_limit") or extra.get("limit_bytes") or extra.get("quota_bytes")),
    }


def _voucher_usage_bytes(voucher):
    username = voucher.username or voucher.code
    totals = RadiusSession.objects.filter(tenant=voucher.tenant, customer__username=username).values("input_octets", "output_octets")
    return sum(int(row["input_octets"] or 0) + int(row["output_octets"] or 0) for row in totals)


def _find_voucher(tenant, username, password):
    candidates = Voucher.objects.filter(tenant=tenant, service_type__iexact="hotspot")
    return candidates.filter(username__iexact=username).first() or candidates.filter(code__iexact=username).first() or candidates.filter(code__iexact=password).first()


def _password_matches(pkt, plain_password, expected_password):
    if not expected_password:
        return False
    if plain_password:
        return plain_password == expected_password
    try:
        return bool(pkt.VerifyChapPasswd(expected_password))
    except Exception:
        return False


def _validate_voucher(voucher, pkt, password):
    if not voucher:
        return False, "voucher not found", None
    if str(voucher.status or "").lower() != "active":
        return False, f"voucher is {voucher.status or 'inactive'}", None
    expected_passwords = {str(voucher.password or ""), str(voucher.code or "")}
    if not any(_password_matches(pkt, password, expected) for expected in expected_passwords if expected):
        return False, "voucher password mismatch", None
    expires_at = _parse_datetime((voucher.extra or {}).get("expires_at") or (voucher.extra or {}).get("expiry_date"))
    if expires_at and timezone.now() >= expires_at:
        voucher.status = "expired"
        voucher.save(update_fields=["status"])
        return False, "voucher expired", None
    package = None
    if voucher.package_id and str(voucher.package_id).isdigit():
        package = InternetPackage.objects.filter(tenant=voucher.tenant, pk=voucher.package_id).first()
    if not package and voucher.package:
        package = InternetPackage.objects.filter(tenant=voucher.tenant, name=voucher.package).first()
    if package and not package.is_active:
        return False, "voucher package is inactive", None
    policy = _package_policy(package)
    voucher_extra = voucher.extra or {}
    data_quota = _bytes_from_value(voucher_extra.get("data_quota") or voucher_extra.get("data_limit") or voucher_extra.get("limit_bytes"))
    if data_quota:
        policy["data_quota"] = data_quota
    used_bytes = _voucher_usage_bytes(voucher)
    if policy.get("data_quota") and used_bytes >= policy["data_quota"]:
        voucher.status = "used"
        voucher.save(update_fields=["status"])
        return False, "voucher data quota exhausted", None
    policy["data_quota_remaining"] = max(0, int(policy["data_quota"] - used_bytes)) if policy.get("data_quota") else None
    return True, "accepted", policy


def _customer_from_voucher(voucher):
    username = voucher.username or voucher.code
    password = voucher.password or voucher.code
    customer, _ = Customer.objects.update_or_create(
        tenant=voucher.tenant,
        username=username,
        defaults={
            "name": f"Voucher {voucher.code}",
            "password": password,
            "radius_secret": password,
            "package": voucher.package or "",
            "service_type": "hotspot",
            "status": "active",
        },
    )
    return customer


class BillingRadiusServer(Server):
    """
    RADIUS server that authenticates MikroTik PPPoE/Hotspot users
    against the Django billing database.
    """

    def HandleAuthPacket(self, pkt):
        """Process an Access-Request from a MikroTik NAS."""
        nas_ip = pkt.source[0]
        username = str(_attr(pkt, ATTR_USER_NAME, "") or "").strip()
        password = ""
        try:
            encrypted_password = _attr(pkt, ATTR_USER_PASSWORD, "")
            password = pkt.PwDecrypt(encrypted_password) if encrypted_password else ""
        except Exception as exc:
            logger.debug("RADIUS password decrypt skipped: user=%s nas=%s error=%s", username, nas_ip, exc)

        logger.info(
            "RADIUS Access-Request: user=%s nas=%s called=%s service=%s",
            username,
            nas_ip,
            _attr(pkt, ATTR_CALLED_STATION_ID, ""),
            _attr(pkt, ATTR_SERVICE_TYPE, ""),
        )

        if not username:
            return _reject(self, pkt, "missing username", nas_ip=nas_ip)

        # 1. Look up NAS client to validate shared secret
        try:
            nas_client = RadiusNasClient.objects.select_related("tenant").get(nas_ip=nas_ip)
        except RadiusNasClient.DoesNotExist:
            return _reject(self, pkt, "unknown NAS", username=username, nas_ip=nas_ip)

        tenant = nas_client.tenant

        customer = Customer.objects.filter(tenant=tenant, username=username).first()
        source = "customer"
        policy = {}
        if customer:
            if customer.status != "active":
                return _reject(self, pkt, f"user is {customer.status or 'inactive'}", username=username, nas_ip=nas_ip)
            radius_secret = customer.radius_secret or customer.password or ""
            if not radius_secret:
                return _reject(self, pkt, "user has no RADIUS secret", username=username, nas_ip=nas_ip)
            if not _password_matches(pkt, password, radius_secret):
                return _reject(self, pkt, "password mismatch", username=username, nas_ip=nas_ip)
            package = InternetPackage.objects.filter(tenant=tenant, name=customer.package).first()
            policy = _package_policy(package)
        else:
            voucher = _find_voucher(tenant, username, password)
            accepted, reason, policy = _validate_voucher(voucher, pkt, password)
            if not accepted:
                return _reject(self, pkt, reason, username=username, nas_ip=nas_ip, source="voucher")
            customer = _customer_from_voucher(voucher)
            source = "voucher"

        reply = self.CreateReplyPacket(pkt, **{
            "Service-Type": "Framed-User",
        })

        rate_limit = policy.get("rate_limit")
        session_timeout = policy.get("session_timeout")
        idle_timeout = policy.get("idle_timeout")
        data_quota = policy.get("data_quota_remaining") or policy.get("data_quota")

        if rate_limit:
            try:
                reply.AddAttribute(ATTR_MIKROTIK_RATE_LIMIT, rate_limit)
            except Exception:
                logger.warning("Could not add Mikrotik-Rate-Limit attribute for user %s", username)

        if session_timeout and session_timeout > 0:
            reply.AddAttribute(ATTR_SESSION_TIMEOUT, str(session_timeout))
        if idle_timeout and idle_timeout > 0:
            reply.AddAttribute(ATTR_IDLE_TIMEOUT, str(idle_timeout))
        if data_quota and data_quota > 0:
            try:
                reply.AddAttribute(ATTR_MIKROTIK_TOTAL_LIMIT, str(int(data_quota)))
            except Exception:
                logger.warning("Could not add Mikrotik-Total-Limit attribute for user %s", username)

        logger.info(
            "RADIUS Access-Accept: user=%s tenant=%s nas=%s source=%s rate_limit=%s session_timeout=%s idle_timeout=%s data_quota=%s customer_id=%s",
            username,
            tenant.id,
            nas_ip,
            source,
            rate_limit,
            session_timeout,
            idle_timeout,
            data_quota,
            customer.id if customer else None,
        )
        return reply.SendTo(pkt.source)

    def HandleAcctPacket(self, pkt):
        """Process an Accounting-Request (Start / Interim-Update / Stop)."""
        nas_ip = pkt.source[0]
        username = str(_attr(pkt, ATTR_USER_NAME, "") or "").strip()
        session_id = str(_attr(pkt, ATTR_ACCT_SESSION_ID, "") or "")
        status_type = _attr(pkt, ATTR_ACCT_STATUS_TYPE, "")

        logger.debug("RADIUS Accounting: user=%s session=%s status=%s nas=%s", username, session_id, status_type, nas_ip)

        if not session_id or not username:
            # Acknowledge but skip processing
            reply = self.CreateReplyPacket(pkt)
            reply.code = pyrad.packet.AccountingResponse
            return reply.SendTo(pkt.source)

        # Look up NAS client to find the tenant
        try:
            nas_client = RadiusNasClient.objects.select_related("tenant").get(nas_ip=nas_ip)
        except RadiusNasClient.DoesNotExist:
            logger.warning("RADIUS Accounting: unknown NAS IP %s, skipping", nas_ip)
            reply = self.CreateReplyPacket(pkt)
            reply.code = pyrad.packet.AccountingResponse
            return reply.SendTo(pkt.source)

        tenant = nas_client.tenant

        try:
            customer = Customer.objects.get(tenant=tenant, username=username)
        except Customer.DoesNotExist:
            logger.warning("RADIUS Accounting: user %s not found for tenant %s", username, tenant.id)
            reply = self.CreateReplyPacket(pkt)
            reply.code = pyrad.packet.AccountingResponse
            return reply.SendTo(pkt.source)

        input_octets = int(_attr(pkt, ATTR_ACCT_INPUT_OCTETS, 0) or 0)
        output_octets = int(_attr(pkt, ATTR_ACCT_OUTPUT_OCTETS, 0) or 0)
        framed_ip = _attr(pkt, ATTR_FRAMED_IP_ADDRESS, "") or None
        terminate_cause = _attr(pkt, ATTR_TERMINATE_CAUSE, "") or ""
        service_type = customer.service_type or ""

        try:
            status_int = int(status_type) if status_type else 0
        except (ValueError, TypeError):
            status_int = 0

        if status_int == ACCT_START:
            # Create a new session record
            RadiusSession.objects.update_or_create(
                tenant=tenant,
                acct_session_id=session_id,
                defaults={
                    "customer": customer,
                    "nas_ip": nas_ip,
                    "framed_ip": framed_ip,
                    "service_type": service_type,
                    "started_at": timezone.now(),
                    "last_interim_at": timezone.now(),
                    "stopped_at": None,
                    "input_octets": input_octets,
                    "output_octets": output_octets,
                    "terminate_cause": "",
                },
            )
            logger.info("RADIUS Accounting Start: user=%s session=%s", username, session_id)

        elif status_int == ACCT_INTERIM_UPDATE:
            # Update existing session with new data usage
            RadiusSession.objects.filter(
                tenant=tenant,
                acct_session_id=session_id,
                stopped_at__isnull=True,
            ).update(
                input_octets=input_octets,
                output_octets=output_octets,
                framed_ip=framed_ip,
                last_interim_at=timezone.now(),
            )
            logger.debug("RADIUS Accounting Interim: user=%s session=%s in=%s out=%s", username, session_id, input_octets, output_octets)

        elif status_int == ACCT_STOP:
            # Close the session
            RadiusSession.objects.filter(
                tenant=tenant,
                acct_session_id=session_id,
                stopped_at__isnull=True,
            ).update(
                input_octets=input_octets,
                output_octets=output_octets,
                framed_ip=framed_ip,
                stopped_at=timezone.now(),
                terminate_cause=terminate_cause,
            )
            logger.info("RADIUS Accounting Stop: user=%s session=%s cause=%s", username, session_id, terminate_cause)

        reply = self.CreateReplyPacket(pkt)
        reply.code = pyrad.packet.AccountingResponse
        return reply.SendTo(pkt.source)


def _register_nas_clients(server):
    """Register all NAS clients from the database as allowed RADIUS clients."""
    for nas_client in RadiusNasClient.objects.select_related("tenant").all():
        try:
            server.hosts[nas_client.nas_ip] = RemoteHost(
                nas_client.nas_ip,
                nas_client.shared_secret.encode("utf-8"),
                nas_client.shared_secret.encode("utf-8"),
            )
            logger.info("RADIUS: registered NAS client %s (%s) for tenant %s",
                        nas_client.nas_ip, nas_client.identifier, nas_client.tenant_id)
        except Exception as exc:
            logger.error("RADIUS: failed to register NAS client %s: %s", nas_client.nas_ip, exc)


def _start_nas_refresh_loop(server, interval=30):
    """Background thread that periodically re-reads NAS clients from the DB
    so that newly provisioned routers are recognized without a server restart."""
    def loop():
        while True:
            time.sleep(interval)
            try:
                _register_nas_clients(server)
            except Exception:
                logger.exception("Failed to refresh RADIUS NAS client list")
    threading.Thread(target=loop, daemon=True).start()


def run_radius_server(host=None, auth_port=None, acct_port=None):
    """
    Start the RADIUS server. Blocks forever.
    Intended to be called from the management command or Celery worker.
    """
    host = host or _get_radius_host()
    auth_port = auth_port or _get_radius_auth_port()
    acct_port = acct_port or _get_radius_acct_port()

    # Ensure Django is set up when running standalone
    import django
    django.setup()

    dict_obj = _load_or_create_dictionary()

    server = BillingRadiusServer(
        addresses=[host],
        authport=auth_port,
        acctport=acct_port,
        dict=dict_obj,
    )

    _register_nas_clients(server)
    _start_nas_refresh_loop(server)

    logger.info(
        "RADIUS server starting on %s (auth=%d, acct=%d) with %d NAS clients registered",
        host, auth_port, acct_port, len(server.hosts),
    )

    try:
        server.Run()
    except KeyboardInterrupt:
        logger.info("RADIUS server shutting down")
