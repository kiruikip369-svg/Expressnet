from django.core.management.base import BaseCommand

from core.services.tenant_provisioning import create_tenant


class Command(BaseCommand):
    help = "Create an ISP tenant, its PostgreSQL schema, and primary domain."

    def add_arguments(self, parser):
        parser.add_argument("--business-name", required=True)
        parser.add_argument("--email", required=True)
        parser.add_argument("--slug")
        parser.add_argument("--owner-name", default="")
        parser.add_argument("--phone", default="")
        parser.add_argument("--domain")

    def handle(self, *args, **options):
        tenant = create_tenant(
            business_name=options["business_name"],
            email=options["email"],
            slug=options.get("slug"),
            owner_name=options.get("owner_name", ""),
            phone=options.get("phone", ""),
            primary_domain=options.get("domain"),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created tenant {tenant.business_name} ({tenant.schema_name})"
            )
        )
