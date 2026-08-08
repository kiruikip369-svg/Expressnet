from django.conf import settings
from django.db import transaction
from django.utils.text import slugify

from core.models import Domain, Tenant


def schema_name_for_slug(slug):
    normalized = slugify(slug).replace("-", "_")
    return f"isp_{normalized}"


@transaction.atomic
def create_tenant(*, business_name, email, slug=None, owner_name="", phone="", primary_domain=None):
    tenant_slug = slugify(slug or business_name)
    if not tenant_slug:
        raise ValueError("A tenant slug or business name is required.")

    tenant = Tenant.objects.create(
        schema_name=schema_name_for_slug(tenant_slug),
        slug=tenant_slug,
        business_name=business_name,
        owner_name=owner_name,
        email=email,
        phone=phone,
    )

    base_domain = getattr(settings, "TENANT_BASE_DOMAIN", "expressnet.app")
    Domain.objects.create(
        tenant=tenant,
        domain=primary_domain or f"{tenant_slug}.{base_domain}",
        is_primary=True,
    )

    return tenant

