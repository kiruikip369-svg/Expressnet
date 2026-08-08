from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Compatibility alias for Django's makemigrations command."

    def add_arguments(self, parser):
        parser.add_argument("args", nargs="*")
        parser.add_argument("--dry-run", action="store_true", dest="dry_run")
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--merge", action="store_true")
        parser.add_argument("--empty", action="store_true")
        parser.add_argument("--noinput", "--no-input", action="store_false", dest="interactive")
        parser.add_argument("--name", "-n", dest="name")
    def handle(self, *args, **options):
        self.stdout.write("Running `makemigrations`.")
        call_command(
            "makemigrations",
            *options.pop("args", []),
            **{key: value for key, value in options.items() if value not in (None, False)},
        )
