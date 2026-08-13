"""
RADIUS user provisioning helpers.

When tenant.radius_enabled is True, creating/updating a Customer only needs
to set the radius_secret field — no RouterOS API call is needed because the
router will ask the RADIUS server at login time.
"""

import logging
import secrets

from django.utils import timezone

from .models import Customer, InternetPackage, RadiusNasClient, Tenant

logger = logging.getLogger(__name__)


def upsert_pg_customer(tenant_obj, customer_data):
    """Mirror a Firebase customer record into the Postgres Customer table."""
    username = customer_data.get("username")
    if not username:
        return None
    extra_updates = {}
    for key in ("expiry_date", "last_payment_id", "last_payment_code", "auto_reconnect", "provisioning_status", "provisioning_message"):
        if key in customer_data:
            extra_updates[key] = customer_data.get(key)
    customer, _ = Customer.objects.update_or_create(
        tenant=tenant_obj, username=username,
        defaults={
            "name": customer_data.get("name", ""),
            "phone": customer_data.get("phone", ""),
            "password": customer_data.get("password", ""),
            "package": customer_data.get("package") or customer_data.get("package_name") or "",
            "service_type": customer_data.get("service_type") or "pppoe",
            "status": customer_data.get("status") or "inactive",
            **extra_updates,
        },
    )
    return customer


def upsert_pg_package(tenant_obj, package_data):
    """Mirror a Firebase package record into the Postgres InternetPackage table."""
    name = package_data.get("name")
    if not name:
        return None
    extra = dict(package_data.get("extra") or {})
    for key in ("service_type", "package_type", "type", "duration_unit", "duration_value", "duration_hours"):
        if key in package_data and package_data.get(key) not in (None, ""):
            extra[key] = package_data.get(key)
    package, _ = InternetPackage.objects.update_or_create(
        tenant=tenant_obj, name=name,
        defaults={
            "speed": package_data.get("speed", ""),
            "duration_days": int(package_data.get("duration_days") or 1),
            "price": float(package_data.get("price") or 0),
            "is_active": package_data.get("is_active") is not False,
            "extra": extra,
        },
    )
    return package


def backfill_radius_data(tenant_obj, tenant_id):
    """Sync all Firebase customers and packages into Postgres so the RADIUS
    server (which reads Postgres) can authenticate them."""
    from .services import list_children

    backfilled_customers = 0
    for customer_data in list_children(f"tenants/{tenant_id}/customers"):
        customer = upsert_pg_customer(tenant_obj, customer_data)
        if customer:
            sync_radius_customer(tenant_obj, customer)
            backfilled_customers += 1

    backfilled_packages = 0
    for package_data in list_children(f"tenants/{tenant_id}/packages"):
        if upsert_pg_package(tenant_obj, package_data):
            backfilled_packages += 1

    logger.info("RADIUS backfill for tenant %s: %d customers, %d packages",
                tenant_id, backfilled_customers, backfilled_packages)
    return {"customers": backfilled_customers, "packages": backfilled_packages}


def sync_radius_customer(tenant_obj, customer_data):
    """
    Create or update a Customer's RADIUS secret.

    This is the RADIUS equivalent of upsert_customer_access — but since
    RADIUS means the router asks Django at login time, there is nothing
    to push to the router. We just ensure the Customer.radius_secret
    is set so the RADIUS server can authenticate the user.

    Args:
        tenant_obj: A Tenant model instance.
        customer_data: Either a Customer model instance, or a dict with at
                       least "username" and optionally "password" / "radius_secret".

    Returns:
        dict with "synced" bool and "message" string.
    """
    username = ""
    password = ""

    if isinstance(customer_data, Customer):
        username = customer_data.username
        password = customer_data.password or ""
        customer = customer_data
    elif isinstance(customer_data, dict):
        username = customer_data.get("username", "")
        password = customer_data.get("password", "")
        # Try to look up the actual model instance
        try:
            customer = Customer.objects.get(tenant=tenant_obj, username=username)
        except Customer.DoesNotExist:
            logger.warning("RADIUS provisioning: customer %s not found for tenant %s, cannot sync", username, tenant_obj.id)
            return {"synced": False, "message": f"Customer {username} not found"}
    else:
        return {"synced": False, "message": "Invalid customer_data type"}

    if not username:
        return {"synced": False, "message": "No username provided"}

    # Set the radius_secret if not already set, or if the password changed.
    update_fields = []
    if password and customer.radius_secret != password:
        customer.radius_secret = password
        update_fields.append("radius_secret")
    elif not customer.radius_secret:
        customer.radius_secret = password or secrets.token_urlsafe(16)
        update_fields.append("radius_secret")

    if update_fields:
        customer.save(update_fields=update_fields + ["updated_at"])
        logger.info("RADIUS provisioning: synced user %s for tenant %s", username, tenant_obj.id)
        return {"synced": True, "message": f"RADIUS secret set for {username}"}

    return {"synced": True, "message": f"RADIUS user {username} already provisioned"}


def ensure_nas_client(tenant_obj, nas_ip, identifier=""):
    """
    Ensure a RadiusNasClient exists for the given tenant + NAS IP.
    If one already exists, return it without changing the secret.

    Args:
        tenant_obj: A Tenant model instance.
        nas_ip: The router's WireGuard tunnel IP address.
        identifier: Router identity / board serial (optional).

    Returns:
        The RadiusNasClient instance.
    """
    nas_client, created = RadiusNasClient.objects.get_or_create(
        tenant=tenant_obj,
        nas_ip=nas_ip,
        defaults={
            "shared_secret": RadiusNasClient.generate_secret(),
            "identifier": identifier,
        },
    )
    if created:
        logger.info("Created RADIUS NAS client for tenant %s: nas_ip=%s", tenant_obj.id, nas_ip)
    if identifier and nas_client.identifier != identifier:
        nas_client.identifier = identifier
        nas_client.save(update_fields=["identifier"])
    return nas_client
