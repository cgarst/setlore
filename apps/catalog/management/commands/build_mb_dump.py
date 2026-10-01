from django.core.management.base import BaseCommand
from src.musicbrainz_dump import MusicBrainzDumpManager

class Command(BaseCommand):
    help = "Builds or updates the local MusicBrainz SQLite dump index from downloaded archives or checks upstream schedule."

    def add_arguments(self, parser):
        parser.add_argument(
            '--components',
            nargs='+',
            default=['release.tar.xz', 'artist.tar.xz'],
            help='Components to index (default: release.tar.xz artist.tar.xz)'
        )
        parser.add_argument(
            '--check-schedule',
            action='store_true',
            help='Checks upstream version according to schedule and runs update if new version exists'
        )
        parser.add_argument(
            '--auto-delete-raw',
            action='store_true',
            help='Automatically delete raw .tar.xz archives after indexing'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force dump update even if local version matches upstream'
        )

    def handle(self, *args, **options):
        manager = MusicBrainzDumpManager.get_instance()
        if options.get('check_schedule'):
            self.stdout.write(self.style.NOTICE("Checking upstream MetaBrainz dump server for scheduled update..."))
            success, msg = manager.check_and_run_scheduled_update(force=options.get('force', False))
            if success:
                self.stdout.write(self.style.SUCCESS(f"Scheduled update initiated: {msg}"))
            else:
                self.stdout.write(self.style.WARNING(f"Scheduled update result: {msg}"))
            return

        components = options['components']
        auto_delete = options.get('auto_delete_raw', False)
        self.stdout.write(self.style.NOTICE(f"Starting MusicBrainz dump build for components: {components} (auto_delete_raw={auto_delete})"))
        manager.download_and_build(components=components, background=False, auto_delete_raw=auto_delete)
        self.stdout.write(self.style.SUCCESS("MusicBrainz dump build completed successfully!"))
