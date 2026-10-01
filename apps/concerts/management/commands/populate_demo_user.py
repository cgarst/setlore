from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from apps.concerts.services.demo_cache import populate_user_data_from_cache, DEFAULT_CACHE_PATH


class Command(BaseCommand):
    help = (
        "Quickly populates/seeds demo user data and catalog items into a database from an offline cache. "
        "Works cleanly in blank databases, existing databases with other users, or production mirrors "
        "without colliding on primary keys or IDs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "-c", "--cache",
            dest="cache_path",
            default=str(DEFAULT_CACHE_PATH),
            help=f"Path to demo user cache JSON/gzip file (default: {DEFAULT_CACHE_PATH})."
        )
        parser.add_argument(
            "-u", "--user", "--username",
            dest="username",
            default=None,
            help="Target username to populate into (defaults to username in cache, e.g. 'demouser')."
        )
        parser.add_argument(
            "--database",
            default="default",
            help="Target database alias to populate into (default: 'default')."
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Overwrite existing concerts for the target user if they already exist."
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Grant superuser / staff permissions to the populated user."
        )
        parser.add_argument(
            "--password",
            default="demopassword123",
            help="Password for the user if newly created (default: demopassword123)."
        )

    def handle(self, *args, **options):
        cache_path = options["cache_path"]
        username = options["username"]
        database = options["database"]
        overwrite = options["overwrite"]
        superuser = options["superuser"]
        password = options["password"]

        self.stdout.write(self.style.MIGRATE_HEADING("=== Populating Demo User from Offline Cache ==="))
        try:
            res = populate_user_data_from_cache(
                cache_input=cache_path,
                target_username=username,
                database=database,
                overwrite=overwrite,
                create_superuser=superuser,
                default_password=password
            )
            self.stdout.write(self.style.SUCCESS(
                f"Successfully populated user '{res['username']}' into database '{database}':\n"
                f"  - Concerts Created: {res['concerts_created']}\n"
                f"  - Concerts Skipped: {res['concerts_skipped']}\n"
                f"  - Venues Created: {res['venues_created']}\n"
                f"  - Artists Created: {res['artists_created']}\n"
                f"  - Songs Created: {res['songs_created']}\n"
                f"  - Albums Created: {res['albums_created']}\n"
                f"  - Tenures Created: {res['tenures_created']}"
            ))
        except Exception as e:
            raise CommandError(f"Failed to populate demo user: {e}")
