"""
Django management command to run the RADIUS server.

Usage:
    python manage.py runradius
    python manage.py runradius --host 0.0.0.0 --auth-port 1812 --acct-port 1813
"""

import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Start the RADIUS AAA server for MikroTik PPPoE/Hotspot authentication"

    def add_arguments(self, parser):
        parser.add_argument(
            "--host",
            default=None,
            help="IP address to bind to (default: RADIUS_HOST env var or 0.0.0.0)",
        )
        parser.add_argument(
            "--auth-port",
            type=int,
            default=None,
            help="Authentication port (default: RADIUS_AUTH_PORT env var or 1812)",
        )
        parser.add_argument(
            "--acct-port",
            type=int,
            default=None,
            help="Accounting port (default: RADIUS_ACCT_PORT env var or 1813)",
        )

    def handle(self, *args, **options):
        from billing_api.radius_server import run_radius_server

        self.stdout.write("Starting RADIUS server...")
        self.stdout.write(f"  Host:      {options['host'] or 'default (0.0.0.0)'}")
        self.stdout.write(f"  Auth port: {options['auth_port'] or 'default (1812)'}")
        self.stdout.write(f"  Acct port: {options['acct_port'] or 'default (1813)'}")
        self.stdout.write("Press Ctrl+C to stop.")

        try:
            run_radius_server(
                host=options["host"],
                auth_port=options["auth_port"],
                acct_port=options["acct_port"],
            )
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nRADIUS server stopped."))
