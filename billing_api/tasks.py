from celery import shared_task
from django.utils import timezone

from .models import TenantSubscription
from .services import iso_now, list_children, ref, set_customer_enabled, write_audit_log

"""

"""
@shared_task
def expire_tenant_subscriptions():
    now = timezone.now()
    expired = TenantSubscription.objects.select_related("tenant").filter(expires_at__lt=now).exclude(tenant__status="suspended")
    count = 0
    for subscription in expired:
        subscription.tenant.status = "suspended"
        subscription.tenant.save(update_fields=["status", "updated_at"])
        write_audit_log(action="AUTO_SUSPEND_EXPIRED_SUBSCRIPTION", target_id=str(subscription.tenant_id), target_type="tenant", metadata={"subscription_id": subscription.pk})
        count += 1
    return count


@shared_task
def send_subscription_reminder_sms():
    now = timezone.now()
    soon = now + timezone.timedelta(days=3)
    return TenantSubscription.objects.filter(expires_at__date=soon.date()).count()


@shared_task
def expire_customer_access():
    now = iso_now()
    count = 0
    for tenant in list_children("tenants"):
        tenant_id = tenant.get("id")
        if not tenant_id:
            continue
        for customer in list_children(f"tenants/{tenant_id}/customers"):
            expiry = str(customer.get("expiry_date") or "")
            if not expiry or expiry > now or customer.get("status") == "expired":
                continue
            service_type = customer.get("service_type") or "hotspot"
            username = customer.get("mac_address") if service_type == "tv" else customer.get("username")
            if service_type != "pppoe":
                try:
                    set_customer_enabled({"id": tenant_id, **tenant}, username, service_type, False)
                except Exception:
                    pass
            ref(f"tenants/{tenant_id}/customers/{customer['id']}").update(
                {
                    "status": "expired",
                    "auto_reconnect": False,
                    "expired_at": now,
                    "updated_at": now,
                }
            )
            count += 1
    return count
