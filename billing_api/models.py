import secrets as crypto_secrets

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils.dateparse import parse_datetime
from django.utils import timezone


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superusers must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superusers must have is_superuser=True")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        TENANT = "tenant", "Tenant"

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=50, choices=Role.choices, default=Role.TENANT)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email


class ExtraFieldsModel(models.Model):
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True

    def apply_data(self, data):
        concrete_fields = {field.name: field for field in self._meta.fields}
        for key, value in data.items():
            if key == "id":
                continue
            if key in concrete_fields:
                field = concrete_fields[key]
                if isinstance(field, models.DateTimeField) and isinstance(value, str):
                    parsed = parse_datetime(value)
                    if parsed is None:
                        continue
                    value = parsed
                setattr(self, key, value)
            else:
                self.extra[key] = value

    def as_dict(self, include_id=True, exclude=None):
        exclude = set(exclude or [])
        data = {}
        for field in self._meta.fields:
            if field.name in {"extra"} or field.name in exclude:
                continue
            if field.name == "id":
                if include_id:
                    data["id"] = str(self.pk)
                continue
            value = getattr(self, field.name)
            if field.is_relation:
                value = str(getattr(self, f"{field.name}_id")) if getattr(self, f"{field.name}_id") else None
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            data[field.name] = value
        data.update(self.extra or {})
        for key in exclude:
            data.pop(key, None)
        return data


class Tenant(ExtraFieldsModel):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="tenants",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    business_name = models.CharField(max_length=255, blank=True, default="")
    owner_name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=50, blank=True, default="")
    password = models.CharField(max_length=255)
    status = models.CharField(max_length=50, blank=True, default="pending_setup")

    mikrotik_host = models.CharField(max_length=255, blank=True, default="")
    mikrotik_user = models.CharField(max_length=255, blank=True, default="")
    mikrotik_pass = models.CharField(max_length=255, blank=True, default="")
    mikrotik_port = models.IntegerField(default=8728)

    provision_token_expires_at = models.DateTimeField(null=True, blank=True)
    logo_url = models.CharField(max_length=500, blank=True, default="")

    notification_provider = models.CharField(max_length=50, blank=True, default="roamtech")
    sms_enabled = models.BooleanField(default=True)
    whatsapp_enabled = models.BooleanField(default=False)
    roamtech_sender_id = models.CharField(max_length=80, blank=True, default="")
    payment_sms_template = models.TextField(blank=True, default="")
    payment_whatsapp_template = models.TextField(blank=True, default="")

    radius_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.business_name or self.email

    def as_dict(self, include_id=True, exclude=None):
        return super().as_dict(include_id, set(exclude or []) | {"password"})


class InternetPackage(ExtraFieldsModel):
    tenant = models.ForeignKey(Tenant, related_name="packages", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    speed = models.CharField(max_length=80, blank=True, default="")
    duration_days = models.IntegerField(default=1)
    price = models.FloatField(default=0)
    is_active = models.BooleanField(default=True)
    ppp_profile_status = models.CharField(max_length=50, blank=True, default="")
    ppp_profile_synced_at = models.CharField(max_length=80, blank=True, default="")
    ppp_profile_error = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="unique_package_name_per_tenant"),
        ]

    def as_dict(self, include_id=True, exclude=None):
        data = super().as_dict(include_id, exclude)
        data.pop("tenant", None)
        return data


class Voucher(ExtraFieldsModel):
    tenant = models.ForeignKey(Tenant, related_name="vouchers", on_delete=models.CASCADE)
    code = models.CharField(max_length=80, unique=True)
    username = models.CharField(max_length=80, blank=True, default="")
    password = models.CharField(max_length=80, blank=True, default="")
    package = models.CharField(max_length=255, blank=True, default="")
    package_id = models.CharField(max_length=80, blank=True, default="")
    price = models.FloatField(default=0)
    status = models.CharField(max_length=40, default="active")
    service_type = models.CharField(max_length=30, default="hotspot")
    router_status = models.CharField(max_length=40, default="pending")
    router_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.router_error is None:
            self.router_error = ""
        super().save(*args, **kwargs)

    def as_dict(self, include_id=True, exclude=None):
        data = super().as_dict(include_id, exclude)
        data.pop("tenant", None)
        return data


class Customer(ExtraFieldsModel):
    tenant = models.ForeignKey(Tenant, related_name="customers", on_delete=models.CASCADE)
    name = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    username = models.CharField(max_length=255, blank=True, default="")
    password = models.CharField(max_length=255, blank=True, default="")
    package = models.CharField(max_length=255, blank=True, default="")
    service_type = models.CharField(max_length=50, blank=True, default="pppoe")
    radius_secret = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=50, blank=True, default="inactive")
    expiry_date = models.CharField(max_length=80, blank=True, null=True)
    auto_reconnect = models.BooleanField(default=True)
    provisioning_status = models.CharField(max_length=80, blank=True, default="")
    provisioning_message = models.TextField(blank=True, null=True)
    last_payment_id = models.CharField(max_length=80, blank=True, null=True)
    last_payment_code = models.CharField(max_length=120, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "username"], name="unique_customer_username_per_tenant"),
        ]

    def as_dict(self, include_id=True, exclude=None):
        data = super().as_dict(include_id, exclude)
        data.pop("tenant", None)
        return data


class Payment(ExtraFieldsModel):
    tenant = models.ForeignKey(Tenant, related_name="payments", on_delete=models.CASCADE)
    customer = models.ForeignKey(Customer, related_name="payments", on_delete=models.SET_NULL, null=True, blank=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    package_id = models.CharField(max_length=80, blank=True, null=True)
    package_name = models.CharField(max_length=255, blank=True, null=True)
    service_type = models.CharField(max_length=50, blank=True, default="pppoe")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payment_code = models.CharField(max_length=120, blank=True, null=True)
    provider = models.CharField(max_length=50, blank=True, null=True)
    currency = models.CharField(max_length=10, blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, default="")
    status = models.CharField(max_length=50, blank=True, default="pending")
    paid_at = models.CharField(max_length=80, blank=True, null=True)
    initiated_at = models.CharField(max_length=80, blank=True, default="")
    merchant_request_id = models.CharField(max_length=255, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=255, blank=True, null=True)
    response_code = models.CharField(max_length=80, blank=True, null=True)
    response_description = models.TextField(blank=True, null=True)
    customer_message = models.TextField(blank=True, null=True)
    source = models.CharField(max_length=80, blank=True, null=True)
    access_username = models.CharField(max_length=255, blank=True, null=True)
    access_password = models.CharField(max_length=255, blank=True, null=True)
    access_expires_at = models.CharField(max_length=80, blank=True, null=True)
    access_status = models.CharField(max_length=80, blank=True, null=True)
    callback_result_code = models.CharField(max_length=80, blank=True, null=True)
    callback_result_desc = models.TextField(blank=True, null=True)

    def apply_data(self, data):
        customer_id = data.pop("customer_id", None) if "customer_id" in data else None
        super().apply_data(data)
        if customer_id:
            self.customer_id = customer_id

    def as_dict(self, include_id=True, exclude=None):
        data = super().as_dict(include_id, exclude)
        data.pop("tenant", None)
        data.pop("customer", None)
        data["customer_id"] = str(self.customer_id) if self.customer_id else None
        return data


class AdminUser(ExtraFieldsModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="admin_profile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=255, blank=True, default="")
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=50, blank=True, default="admin")
    is_active = models.BooleanField(default=True)
    last_login = models.CharField(max_length=80, blank=True, default="")
    login_count = models.IntegerField(default=0)
    created_at = models.CharField(max_length=80, blank=True, default="")


class SiteSettings(ExtraFieldsModel):
    brand_name = models.CharField(max_length=255, blank=True, default="")
    headline = models.CharField(max_length=255, blank=True, default="")
    subheadline = models.TextField(blank=True, default="")
    about = models.TextField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    location = models.CharField(max_length=255, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    cta_label = models.CharField(max_length=255, blank=True, default="")
    cta_url = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.CharField(max_length=80, blank=True, default="")
    updated_by = models.CharField(max_length=80, blank=True, default="")


class AdminAuditLog(models.Model):
    admin_id = models.CharField(max_length=80, blank=True, null=True)
    admin_email = models.EmailField(blank=True, null=True)
    action = models.CharField(max_length=120, blank=True, null=True)
    target_id = models.CharField(max_length=80, blank=True, null=True)
    target_type = models.CharField(max_length=80, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip = models.CharField(max_length=80, blank=True, null=True)
    user_agent = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    def as_dict(self, include_id=True):
        data = {
            "admin_id": self.admin_id,
            "admin_email": self.admin_email,
            "action": self.action,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "ip": self.ip,
            "user_agent": self.user_agent,
            "metadata": self.metadata or {},
        }
        if include_id:
            data["id"] = str(self.pk)
        return data


class SubscriptionPlan(models.TextChoices):
    BASIC = "basic", "Basic"
    PRO = "pro", "Pro"
    ENTERPRISE = "enterprise", "Enterprise"


class TenantSubscription(models.Model):
    tenant = models.OneToOneField(Tenant, related_name="subscription", on_delete=models.CASCADE)
    plan = models.CharField(max_length=50, choices=SubscriptionPlan.choices, default=SubscriptionPlan.BASIC)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=1500)
    currency = models.CharField(max_length=10, default="KES")
    billing_cycle_days = models.IntegerField(default=30)
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_paid_at = models.DateTimeField(null=True, blank=True)
    grace_period_days = models.IntegerField(default=3)
    auto_renew = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_expired(self):
        return bool(self.expires_at and timezone.now() > self.expires_at)

    def days_until_expiry(self):
        if not self.expires_at:
            return None
        return (self.expires_at - timezone.now()).days

    def as_dict(self, include_id=True):
        data = {
            "tenant_id": str(self.tenant_id),
            "tenant_name": self.tenant.business_name,
            "tenant_email": self.tenant.email,
            "plan": self.plan,
            "amount": float(self.amount or 0),
            "currency": self.currency,
            "billing_cycle_days": self.billing_cycle_days,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "expires_at": self.expires_at.isoformat() if self.expires_at else "",
            "last_paid_at": self.last_paid_at.isoformat() if self.last_paid_at else "",
            "grace_period_days": self.grace_period_days,
            "auto_renew": self.auto_renew,
            "notes": self.notes,
            "days_until_expiry": self.days_until_expiry(),
            "status": "expired" if self.is_expired() else "active",
        }
        if include_id:
            data["id"] = str(self.pk)
        return data


class SubscriptionPayment(models.Model):
    subscription = models.ForeignKey(TenantSubscription, related_name="payments", on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="KES")
    paid_at = models.DateTimeField(default=timezone.now)
    method = models.CharField(max_length=80, blank=True, default="manual")
    reference = models.CharField(max_length=255, blank=True, default="")
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    recorded_by = models.CharField(max_length=255, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def as_dict(self, include_id=True):
        data = {
            "subscription_id": str(self.subscription_id),
            "amount": float(self.amount or 0),
            "currency": self.currency,
            "paid_at": self.paid_at.isoformat() if self.paid_at else "",
            "method": self.method,
            "reference": self.reference,
            "period_start": self.period_start.isoformat() if self.period_start else "",
            "period_end": self.period_end.isoformat() if self.period_end else "",
            "recorded_by": self.recorded_by,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }
        if include_id:
            data["id"] = str(self.pk)
        return data


class RadiusNasClient(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="nas_clients")
    nas_ip = models.GenericIPAddressField()
    shared_secret = models.CharField(max_length=128)
    identifier = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "RADIUS NAS Client"
        verbose_name_plural = "RADIUS NAS Clients"
        unique_together = [("tenant", "nas_ip")]

    def as_dict(self, include_id=True):
        data = {
            "tenant_id": str(self.tenant_id),
            "nas_ip": self.nas_ip,
            "identifier": self.identifier,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }
        if include_id:
            data["id"] = str(self.pk)
        return data

    @classmethod
    def generate_secret(cls):
        return crypto_secrets.token_urlsafe(24)


class RadiusSession(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="radius_sessions")
    acct_session_id = models.CharField(max_length=64, db_index=True)
    nas_ip = models.GenericIPAddressField()
    framed_ip = models.GenericIPAddressField(null=True, blank=True)
    service_type = models.CharField(max_length=16, blank=True, default="")
    started_at = models.DateTimeField()
    last_interim_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    input_octets = models.BigIntegerField(default=0)
    output_octets = models.BigIntegerField(default=0)
    terminate_cause = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        verbose_name = "RADIUS Session"
        verbose_name_plural = "RADIUS Sessions"
        ordering = ["-started_at"]

    @property
    def total_octets(self):
        return self.input_octets + self.output_octets

    def as_dict(self, include_id=True):
        data = {
            "tenant_id": str(self.tenant_id),
            "customer_id": str(self.customer_id),
            "acct_session_id": self.acct_session_id,
            "nas_ip": self.nas_ip,
            "framed_ip": self.framed_ip or "",
            "service_type": self.service_type,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "last_interim_at": self.last_interim_at.isoformat() if self.last_interim_at else "",
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else "",
            "input_octets": self.input_octets,
            "output_octets": self.output_octets,
            "total_octets": self.total_octets,
            "terminate_cause": self.terminate_cause,
            "active": self.stopped_at is None,
        }
        if include_id:
            data["id"] = str(self.pk)
        return data


class Ticket(ExtraFieldsModel):
    tenant = models.ForeignKey(Tenant, related_name="tickets", on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    customer_id = models.CharField(max_length=80, blank=True, default="")
    status = models.CharField(max_length=50, blank=True, default="open")
    priority = models.CharField(max_length=50, blank=True, default="medium")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def as_dict(self, include_id=True, exclude=None):
        data = super().as_dict(include_id, exclude)
        data.pop("tenant", None)
        return data
