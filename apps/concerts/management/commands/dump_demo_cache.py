from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from apps.concerts.services.demo_cache import export_user_data_to_cache, DEFAULT_CACHE_PATH


class Command(BaseCommand):
    help = (
        "Exports a user's concert history, venues, songs, albums, and setlists into a portable "
        "offline JSON cache file that can be seeded into another database without ID collisions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "-u", "--user", "--username",
            dest="username",
            default="demouser",
            help="Username of the user whose data to export (default: demouser)."
        )
        parser.add_argument(
            "-o", "--output",
            dest="output",
            default=str(DEFAULT_CACHE_PATH),
            help=f"Path to output JSON/gzip file (default: {DEFAULT_CACHE_PATH})."
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Source database alias to read from (default: default)."
        )

    def handle(self, *args, **options):
        username = options["username"]
        output_path = options["output"]
        database = options["database"]

        self.stdout.write(self.style.MIGRATE_HEADING(f"=== Exporting Demo User Cache for '{username}' ==="))
        try:
            res = export_user_data_to_cache(username=username, output_path=output_path, database=database)
            self.stdout.write(self.style.SUCCESS(
                f"Successfully exported {res['total_concerts']} concerts, {res['total_artists']} artists, "
                f"and {res['total_venues']} venues to:\n  {output_path}"
            ))
        except Exception as e:
            raise CommandError(f"Failed to export demo cache: {e}")
