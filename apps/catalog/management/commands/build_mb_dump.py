from django.core.management.base import BaseCommand
from src.musicbrainz_dump import MusicBrainzDumpManager

class Command(BaseCommand):
    help = "Builds the local MusicBrainz SQLite dump index from downloaded archives."

    def add_arguments(self, parser):
        parser.add_argument(
            '--components',
            nargs='+',
            default=['release.tar.xz', 'artist.tar.xz'],
            help='Components to index (default: release.tar.xz artist.tar.xz)'
        )

    def handle(self, *args, **options):
        components = options['components']
        self.stdout.write(self.style.NOTICE(f"Starting MusicBrainz dump build for components: {components}"))
        manager = MusicBrainzDumpManager.get_instance()
        manager.download_and_build(components=components, background=False)
        self.stdout.write(self.style.SUCCESS("MusicBrainz dump build completed successfully!"))
