from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Tenant(TenantMixin):
    """
    Public-schema tenant record for an ISP.

    Platform users live in the shared schema. ISP operators should remain
    tenant-scoped when the auth tables are moved into TENANT_APPS; if shared
    cross-tenant identities are needed later, use django-tenant-users rather
    than hand-rolling that identity layer.
    """

    auto_create_schema = True
    auto_drop_schema = False

    business_name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=80, unique=True)
    owner_name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(max_length=50, blank=True, default="pending_setup")

    provision_token_expires_at = models.DateTimeField(null=True, blank=True)
    logo_url = models.CharField(max_length=500, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name or self.email


class Domain(DomainMixin):
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["domain"], name="unique_tenant_domain"),
        ]

