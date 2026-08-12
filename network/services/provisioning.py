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
    result = upsert_router_profile(tenant, ("ip", "hotspot", "user", "profile"), name, speed, session_timeout)
    if has_mikrotik_credentials(tenant):
        api = router_connect(tenant)
        try:
            for item in api.path("ppp", "profile").select():
                if item.get("name") == name and item.get("comment") == "billing-saas-package" and item.get(".id"):
                    try:
                        api.path("ppp", "profile").remove(item[".id"])
                    except Exception:
                        pass
        finally:
            api.close()
    return result


def package_service_type(package):
    raw = str(
        (package or {}).get("service_type")
        or (package or {}).get("package_type")
        or (package or {}).get("type")
        or ""
    ).strip().lower()
    if raw in {"hotspot", "voucher", "wifi"}:
        return "hotspot"
    if raw in {"pppoe", "ppoe", "ppp", "broadband"}:
        return "pppoe"

    # Legacy packages often missed service_type even though this product sells
    # Hotspot vouchers by default. Only explicit PPP/PPPoE values go to /ppp.
    duration_unit = str((package or {}).get("duration_unit") or "").strip().lower()
    try:
        duration_hours = float((package or {}).get("duration_hours") or 0)
    except (TypeError, ValueError):
        duration_hours = 0
    if duration_unit == "hours" or (duration_hours and duration_hours < 24):
        return "hotspot"
    return "hotspot"


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
        or "Expressnet-bridge"
    ).strip() or "Expressnet-bridge"


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


EXPRESSNET_PORTAL_PLACEHOLDER = "https://expressnet.app/api/captive"
LEGACY_PORTAL_PLACEHOLDER = "https://expresswifi.centipidtechnologies.com/buy/packages"
LEGACY_ROUTER_PLACEHOLDER = "71785"


def _strip_external_beacon(content):
    marker = '<script defer src="https://static.cloudflareinsights.com'
    start = content.find(marker)
    if start == -1:
        return content
    end = content.find("</script>", start)
    if end == -1:
        return content[:start]
    return content[:start] + content[end + len("</script>"):]


def expressnet_hotspot_file_html(page, portal_url):
    page_name = str(page or "").strip().lower()
    source_name = page_name
    if source_name in {"status.html", "radvert.html"}:
        source_name = "rlogin.html"
    template_path = BASE_DIR / "centipeed" / "centipid-hotspot" / source_name
    if not template_path.exists():
        return None

    content = template_path.read_text(encoding="utf-8", errors="ignore")
    content = _strip_external_beacon(content)
    target = str(portal_url or "").rstrip("/")
    content = content.replace(LEGACY_PORTAL_PLACEHOLDER, target)
    content = content.replace(EXPRESSNET_PORTAL_PLACEHOLDER, target)
    content = content.replace("CENTIPID", "EXPRESSNET")
    content = content.replace("Centipid", "Expressnet")
    content = content.replace("centipid", "expressnet")
    content = content.replace("Centipeed", "Expressnet")
    content = content.replace("centipeed", "expressnet")
    content = content.replace(f"router={LEGACY_ROUTER_PLACEHOLDER}", "router_ip=$(server-address)&link_login=$(link-login)")
    content = content.replace(f"router={LEGACY_ROUTER_PLACEHOLDER}&", "router_ip=$(server-address)&link_login=$(link-login)&")
    content = content.replace(f'"router=71785"', '"router_ip=$(server-address)&link_login=$(link-login)"')
    content = content.replace("router=71785", "router_ip=$(server-address)&link_login=$(link-login)")
    content = content.replace('name="router"', 'name="router_ip"')
    content = content.replace('value="71785"', 'value="$(server-address)"')
    content = content.replace('name="mikrotik_error"', 'name="error"')
    content = content.replace("mikrotik_error=$(error)", "error=$(error)")
    if 'name="link_login"' not in content:
        content = content.replace(
            '<input name="router_ip" type="hidden" value="$(server-address)" />',
            '<input name="router_ip" type="hidden" value="$(server-address)" />\n'
            '            <input name="link_login" type="hidden" value="$(link-login)" />',
        )
    return content

def hotspot_login_redirect_html(portal_url):
    target = hotspot_portal_target(portal_url, "ip=$(ip)&mac=$(mac)&router_ip=$(server-address)&link_login=$(link-login)&error=$(error)")
    return hotspot_portal_landing_html(target, "Internet Access")


def hotspot_error_redirect_html(portal_url):
    target = hotspot_portal_target(portal_url, "ip=$(ip)&mac=$(mac)&router_ip=$(server-address)&link_login=$(link-login)&error=$(error)")
    return hotspot_portal_landing_html(target, "Internet Access")


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
        return hotspot_portal_landing_html(target, "Internet Packages")
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


def hotspot_portal_landing_html(target, title):
    parsed = urlsplit(str(target or ""))
    action = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    fields = parse_qsl(parsed.query, keep_blank_values=True)
    target_json = json.dumps(str(target or ""))
    action_json = json.dumps(action)
    escaped_target = str(target or "").replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_action = action.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    hidden_fields = "".join(
        f"<input type='hidden' name='{str(name).replace('&', '&amp;').replace(chr(34), '&quot;').replace('<', '&lt;').replace('>', '&gt;')}' value='{str(value).replace('&', '&amp;').replace(chr(34), '&quot;').replace('<', '&lt;').replace('>', '&gt;')}'>"
        for name, value in fields
    )
    return (
        "<!doctype html><html><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<meta http-equiv='refresh' content='0; url={escaped_target}'>"
        f"<title>{title}</title>"
        "<script>"
        f"window.__portalTarget={target_json};"
        "try{window.top.location.replace(window.__portalTarget);}catch(e){}"
        "try{window.location.replace(window.__portalTarget);}catch(e){}"
        "</script>"
        "<style>body{margin:0;background:#f8fafc;font-family:Arial,sans-serif;color:#0f172a}"
        ".wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}"
        ".card{max-width:360px;width:100%;background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:18px;text-align:center}"
        "a,button{display:block;width:100%;box-sizing:border-box;border:0;border-radius:8px;background:#f97316;color:#fff;padding:12px 14px;font-weight:700;text-decoration:none;cursor:pointer}"
        "p{font-size:14px;color:#475569}</style>"
        "</head><body>"
        "<div class='wrap'><div class='card'><h2>Internet Access</h2><p>Opening your internet packages...</p>"
        f"<form id='portal-form' method='get' action='{escaped_action}'>{hidden_fields}<button id='open' type='submit'>Open packages</button></form>"
        f"<p><a target='_self' rel='noreferrer' href='{escaped_target}'>Open in browser</a></p></div></div>"
        "<script>"
        f"var target=window.__portalTarget||{target_json};"
        f"var action={action_json};"
        "var form=document.getElementById('portal-form');"
        "function go(){"
        "try{if(form){form.submit();return;}}catch(e){}"
        "try{window.location.replace(target);return;}catch(e){}"
        "try{window.location.href=target;return;}catch(e){}"
        "try{window.top.location.href=target;return;}catch(e){}"
        "}"
        "setTimeout(go,100);setTimeout(go,900);setTimeout(go,2500);"
        "</script>"
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
        for target_name in (
            f"Expressnet-hotspot/{page}",
            f"flash/Expressnet-hotspot/{page}",
            f"hotspot/{page}",
            f"flash/hotspot/{page}",
        ):
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
        "Expressnet-hotspot/login.html": expressnet_hotspot_file_html("login.html", portal_url) or hotspot_login_redirect_html(portal_url),
        "Expressnet-hotspot/alogin.html": expressnet_hotspot_file_html("alogin.html", portal_url) or hotspot_alogin_redirect_html(portal_url),
        "Expressnet-hotspot/redirect.html": expressnet_hotspot_file_html("redirect.html", portal_url) or fallback_redirect_html,
        "Expressnet-hotspot/error.html": expressnet_hotspot_file_html("error.html", portal_url) or hotspot_error_redirect_html(portal_url),
        "Expressnet-hotspot/status.html": expressnet_hotspot_file_html("status.html", portal_url) or fallback_redirect_html,
        "Expressnet-hotspot/rlogin.html": expressnet_hotspot_file_html("rlogin.html", portal_url) or fallback_redirect_html,
        "Expressnet-hotspot/radvert.html": expressnet_hotspot_file_html("radvert.html", portal_url) or fallback_redirect_html,
        "hotspot/login.html": expressnet_hotspot_file_html("login.html", portal_url) or hotspot_login_redirect_html(portal_url),
        "hotspot/alogin.html": expressnet_hotspot_file_html("alogin.html", portal_url) or hotspot_alogin_redirect_html(portal_url),
        "hotspot/redirect.html": expressnet_hotspot_file_html("redirect.html", portal_url) or fallback_redirect_html,
        "hotspot/error.html": expressnet_hotspot_file_html("error.html", portal_url) or hotspot_error_redirect_html(portal_url),
        "hotspot/status.html": expressnet_hotspot_file_html("status.html", portal_url) or fallback_redirect_html,
        "hotspot/rlogin.html": expressnet_hotspot_file_html("rlogin.html", portal_url) or fallback_redirect_html,
        "hotspot/radvert.html": expressnet_hotspot_file_html("radvert.html", portal_url) or fallback_redirect_html,
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
        parts = portal_host.split(".")
        if len(parts) > 2:
            hosts.append(f"*.{'.'.join(parts[-2:])}")
        if portal_host.endswith(".up.railway.app"):
            hosts.extend(["up.railway.app", "*.up.railway.app", "railway.app", "*.railway.app"])
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
    profile_name = "Expressnet-profile"
    api = router_connect(tenant)
    try:
        try:
            api.command("/ip/dns/set", {"allow-remote-requests": "yes"})
        except Exception:
            pass
        upsert_router_item(
            api,
            ("ip", "hotspot", "profile"),
            {"name": profile_name},
            {
                "name": profile_name,
                "hotspot-address": "172.31.0.1",
                "dns-name": "hot.spot",
                "login-by": "cookie,http-pap,trial,mac-cookie",
                "use-radius": "yes",
                "radius-accounting": "yes",
                "html-directory": "Expressnet-hotspot",
                "comment": f"Expressnet captive portal: {portal_url}",
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

        def command_rows(command, attrs=None):
            try:
                return list(api.command(command, attrs or {}))
            except Exception:
                return []

        def first_numeric(row, *keys):
            for key in keys:
                value = row.get(key)
                if value not in {None, ""}:
                    try:
                        return int(float(value))
                    except (TypeError, ValueError):
                        return value
            return None

        resource = (items("system", "resource") or [{}])[0]
        routerboard = (items("system", "routerboard") or [{}])[0]
        interfaces = items("interface")
        pppoe_servers = items("interface", "pppoe-server", "server")
        hotspot_servers = items("ip", "hotspot")
        ppp_profiles = items("ppp", "profile")
        hotspot_profiles = items("ip", "hotspot", "user", "profile")
        active_hotspot = items("ip", "hotspot", "active")
        active_ppp = items("ppp", "active")

        traffic_by_name = {}
        for interface in interfaces:
            name = interface.get("name")
            if not name:
                continue
            rows = command_rows("/interface/monitor-traffic", {"interface": name, "once": ""})
            row = rows[0] if rows else {}
            traffic_by_name[name] = {
                "rx_bps": first_numeric(row, "rx-bits-per-second"),
                "tx_bps": first_numeric(row, "tx-bits-per-second"),
                "rx_packet_rate": first_numeric(row, "rx-packets-per-second"),
                "tx_packet_rate": first_numeric(row, "tx-packets-per-second"),
            }

        wireless_by_interface = {}
        for row in command_rows("/interface/wireless/registration-table/print"):
            interface_name = row.get("interface")
            if not interface_name:
                continue
            signal = first_numeric(row, "signal-strength")
            current = wireless_by_interface.get(interface_name)
            if current is None or (isinstance(signal, int) and signal > int(current.get("signal_strength") or -999)):
                wireless_by_interface[interface_name] = {
                    "client_mac": row.get("mac-address"),
                    "signal_strength": signal,
                    "tx_rate": row.get("tx-rate"),
                    "rx_rate": row.get("rx-rate"),
                    "uptime": row.get("uptime"),
                }

        total_rx_bps = sum(int(v.get("rx_bps") or 0) for v in traffic_by_name.values() if isinstance(v.get("rx_bps"), int))
        total_tx_bps = sum(int(v.get("tx_bps") or 0) for v in traffic_by_name.values() if isinstance(v.get("tx_bps"), int))
    finally:
        api.close()

    def session_bytes(row):
        total = 0
        for key in ("bytes-in", "bytes-out", "acct-input-octets", "acct-output-octets"):
            try:
                total += int(float(row.get(key) or 0))
            except (TypeError, ValueError):
                pass
        return total

    session_rows = [
        {
            "service_type": "hotspot",
            "username": item.get("user") or item.get("name") or item.get("mac-address"),
            "address": item.get("address"),
            "mac_address": item.get("mac-address"),
            "uptime": item.get("uptime"),
            "data_used": session_bytes(item),
            "server": item.get("server"),
        }
        for item in active_hotspot
    ] + [
        {
            "service_type": "pppoe",
            "username": item.get("name") or item.get("user"),
            "address": item.get("address") or item.get("caller-id"),
            "mac_address": item.get("caller-id"),
            "uptime": item.get("uptime"),
            "data_used": session_bytes(item),
            "server": item.get("service") or item.get("interface"),
        }
        for item in active_ppp
    ]
    session_rows = sorted(session_rows, key=lambda item: item["data_used"], reverse=True)

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
        "traffic": {
            "rx_bps": total_rx_bps,
            "tx_bps": total_tx_bps,
            "sampled_at": datetime.now(timezone.utc).isoformat(),
        },
        "active_sessions": {
            "hotspot": len(active_hotspot),
            "pppoe": len(active_ppp),
            "total": len(active_hotspot) + len(active_ppp),
            "items": session_rows[:20],
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
                "traffic": traffic_by_name.get(item.get("name")) or {},
                "wireless": wireless_by_interface.get(item.get("name")) or {},
                "signal_strength": (wireless_by_interface.get(item.get("name")) or {}).get("signal_strength"),
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

        # 2. Shift the interface into the Expressnet managed bridge
        managed_bridge = mikrotik_managed_bridge_name(tenant)
        existing_bridges = list(api.path("interface", "bridge").select())
        if not any(b.get("name") == managed_bridge for b in existing_bridges):
            api.path("interface", "bridge").add(name=managed_bridge, comment="Created by Expressnet")
        upsert_router_item(
            api,
            ("ip", "pool"),
            {"name": "Expressnet-pool"},
            {"name": "Expressnet-pool", "ranges": "172.31.0.2-172.31.255.254", "comment": "IP Pool created by Expressnet"},
        )
        upsert_router_item(
            api,
            ("ip", "address"),
            {"interface": managed_bridge, "comment": "Added by Expressnet"},
            {"address": "172.31.0.1/16", "interface": managed_bridge, "comment": "Added by Expressnet"},
        )
        upsert_router_item(
            api,
            ("ip", "dhcp-server"),
            {"name": "Expressnet-dhcp"},
            {"name": "Expressnet-dhcp", "interface": managed_bridge, "address-pool": "Expressnet-pool", "lease-time": "4h", "disabled": "no"},
        )
        upsert_router_item(
            api,
            ("ip", "dhcp-server", "network"),
            {"address": "172.31.0.0/16"},
            {"address": "172.31.0.0/16", "gateway": "172.31.0.1", "dns-server": "8.8.8.8,8.8.4.4"},
        )

        # Add the target interface to our managed bridge
        api.path("interface", "bridge", "port").add(bridge=managed_bridge, interface=interface_name)
        bind_interface = managed_bridge

        wireless_security_profile = None
        if service_type == "hotspot":
            wireless_security_profile = _clear_wireless_password_for_hotspot(api, interface_name)

        bridge_note = f"Interface '{interface_name}' successfully moved into Expressnet managed bridge '{managed_bridge}'."

        if service_type == "pppoe":
            api.path("interface").update(**{".id": interface[".id"], "comment": f"billing-saas:{service_type}:profile={profile_name or 'default'}"})
            servers = list(api.path("interface", "pppoe-server", "server").select())
            existing = next((item for item in servers if item.get("interface") == bind_interface), None)
            fields = {
                "service-name": "Expressnet-pppoe",
                "interface": bind_interface,
                "default-profile": profile_name or "INTERNET",
                "authentication": "pap",
                "one-session-per-host": "yes",
                "disabled": "no",
            }
            if existing and existing.get(".id"):
                api.path("interface", "pppoe-server", "server").update(**{".id": existing[".id"], **fields})
                return {"updated": True, "service_type": service_type, "interface": interface_name, "bound_interface": bind_interface, "note": bridge_note}
            api.path("interface", "pppoe-server", "server").add(**fields)
            return {"created": True, "service_type": service_type, "interface": interface_name, "bound_interface": bind_interface, "note": bridge_note}

        captive = ensure_hotspot_captive_portal(tenant, base_url) or {}
        hotspot_profile = captive.get("profile") or "Expressnet-profile"
        api.path("interface").update(**{".id": interface[".id"], "comment": f"billing-saas:hotspot:portal={captive.get('portal_url') or ''}".strip()})
        
        servers = list(api.path("ip", "hotspot").select())
        existing = next((item for item in servers if item.get("interface") == bind_interface), None)
        fields = {
            "name": "Expressnet-hotspot",
            "interface": bind_interface,
            "address-pool": "Expressnet-pool",
            "profile": hotspot_profile,
            "disabled": "no",
            "comment": f"Expressnet captive portal: {captive.get('portal_url') or ''}".strip(),
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

    hotspot_setup = ""
    if portal_url:
        hotspot_setup = (
            f':do {{ /ip dns set allow-remote-requests=yes }} on-error={{ :log warning "Billing SaaS: failed to enable DNS for hotspot clients" }}; '
            f':do {{ /interface list member add list=LAN interface="{_rsc_escape(bridge_name)}" comment="billing-saas captive LAN" }} on-error={{ /interface list member set [find interface="{_rsc_escape(bridge_name)}"] list=LAN comment="billing-saas captive LAN" }}; '
            f':do {{ /ip firewall filter remove [find comment="billing-saas allow hotspot dns"] }} on-error={{}}; '
            f':do {{ /ip firewall filter remove [find comment="billing-saas allow hotspot dhcp"] }} on-error={{}}; '
            f':do {{ /ip firewall filter remove [find comment="billing-saas allow hotspot web-proxy"] }} on-error={{}}; '
            f':do {{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=udp dst-port=53 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot dns" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=udp dst-port=53 comment="billing-saas allow hotspot dns" }}; '
            f':do {{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=tcp dst-port=53 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot dns" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=tcp dst-port=53 comment="billing-saas allow hotspot dns" }}; '
            f':do {{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=udp dst-port=67,68 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot dhcp" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=udp dst-port=67,68 comment="billing-saas allow hotspot dhcp" }}; '
            f':do {{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=tcp dst-port=64872-64875 place-before=[find comment="defconf: drop all not coming from LAN"] comment="billing-saas allow hotspot web-proxy" }} on-error={{ /ip firewall filter add chain=input action=accept in-interface="{_rsc_escape(bridge_name)}" protocol=tcp dst-port=64872-64875 comment="billing-saas allow hotspot web-proxy" }}; '
            f':do {{ /ip hotspot profile add name="Expressnet-profile" hotspot-address=172.31.0.1 dns-name=hot.spot login-by=cookie,http-pap,trial,mac-cookie use-radius=yes html-directory=Expressnet-hotspot radius-interim-update=10m comment="Expressnet captive portal: {portal_comment}" }} '
            f'on-error={{ /ip hotspot profile set [find name="Expressnet-profile"] hotspot-address=172.31.0.1 dns-name=hot.spot login-by=cookie,http-pap,trial,mac-cookie use-radius=yes html-directory=Expressnet-hotspot radius-interim-update=10m comment="Expressnet captive portal: {portal_comment}" }}; '
            + "".join(
                f':do {{ /ip hotspot walled-garden add action=allow dst-host="{_rsc_escape(h)}" comment="billing-saas captive portal access" }} on-error={{ :log warning "Billing SaaS: walled-garden add failed" }}; '
                for h in walled_garden_hosts(tenant, portal_host)
            ) + ' '
            f':local billingPortalIp ""; '
            f':do {{ :set billingPortalIp [:resolve "{portal_host}"] }} on-error={{ :log warning "Billing SaaS portal DNS resolve failed" }}; '
            f':if ([:len $billingPortalIp] > 0) do={{ '
            f':do {{ /ip dns static remove [find name="{portal_host}" comment="billing-saas captive portal dns"] }} on-error={{}}; '
            f':do {{ /ip dns static add name="{portal_host}" address=$billingPortalIp comment="billing-saas captive portal dns" }} on-error={{}}; '
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
        f'  :if ([:len $billingHs] > 0) do={{ /ip hotspot set $billingHs name="Expressnet-hotspot" address-pool=Expressnet-pool profile="Expressnet-profile" disabled=no }} else={{ /ip hotspot add name="Expressnet-hotspot" interface="{bridge_name}" address-pool=Expressnet-pool profile="Expressnet-profile" disabled=no }}; '
    )

    cleanup_block = ':log info "Billing SaaS: preserving existing PPP secrets and Hotspot users"; '

    return (
        f'/interface bridge port remove [find interface="{interface_name}"]; '
        f':if ([:len [/interface bridge find name="{bridge_name}"]] = 0) do={{ /interface bridge add name="{bridge_name}" comment="Created by Expressnet" }}; '
        f':do {{ /ip pool add name=Expressnet-pool ranges=172.31.0.2-172.31.255.254 comment="IP Pool created by Expressnet" }} on-error={{ /ip pool set [find name=Expressnet-pool] ranges=172.31.0.2-172.31.255.254 comment="IP Pool created by Expressnet" }}; '
        f':do {{ /ip address add address=172.31.0.1/16 interface="{bridge_name}" comment="Added by Expressnet" }} on-error={{ /ip address set [find interface="{bridge_name}" comment="Added by Expressnet"] address=172.31.0.1/16 interface="{bridge_name}" }}; '
        f':do {{ /ip dhcp-server add name=Expressnet-dhcp interface="{bridge_name}" address-pool=Expressnet-pool lease-time=4h disabled=no }} on-error={{ /ip dhcp-server set [find name=Expressnet-dhcp] interface="{bridge_name}" address-pool=Expressnet-pool lease-time=4h disabled=no }}; '
        f':do {{ /ip dhcp-server network add address=172.31.0.0/16 gateway=172.31.0.1 dns-server=8.8.8.8,8.8.4.4 }} on-error={{ /ip dhcp-server network set [find address=172.31.0.0/16] gateway=172.31.0.1 dns-server=8.8.8.8,8.8.4.4 }}; '
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
    service_type = customer.get("service_type") or "hotspot"
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
            from billing_api.models import Tenant as TenantModel, Customer as CustomerModel
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
            from billing_api.models import Tenant as TenantModel, Customer as CustomerModel
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

