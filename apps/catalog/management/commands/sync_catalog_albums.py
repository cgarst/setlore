from django.core.management.base import BaseCommand
from apps.catalog.models import Song, Album
from apps.concerts.services.sync_worker import sync_worker
from src.album_enricher import AlbumEnricher


class Command(BaseCommand):
    help = "Populates Album catalog models and links Song.album from cached or live MusicBrainz enrichments."

    def add_arguments(self, parser):
        parser.add_argument(
            '--refresh',
            action='store_true',
            help='Re-query MusicBrainz for uncached tracks rather than loading only from cache.'
        )

    def handle(self, *args, **options):
        refresh = options.get('refresh', False)
        self.stdout.write("Gathering distinct songs from catalog...")

        distinct_tracks = list(Song.objects.values_list('artist__name', 'title').distinct())
        song_pairs = [{"artist": a, "song": s} for a, s in distinct_tracks if a and s]

        enricher = AlbumEnricher()
        if refresh:
            self.stdout.write(f"Enriching {len(song_pairs)} songs with MusicBrainz...")
            enrichments = enricher.enrich_catalog(song_pairs, refresh_unresolved=True)
        else:
            self.stdout.write(f"Loading {len(song_pairs)} songs from local album cache...")
            enrichments, uncached, _ = enricher.load_cached_catalog(song_pairs)
            if uncached:
                self.stdout.write(f"  ({len(uncached)} songs are uncached. Pass --refresh to query live MusicBrainz)")

        self.stdout.write("Persisting album catalog records and linking songs...")
        sync_worker._persist_album_enrichments_to_db(enrichments)

        album_count = Album.objects.count()
        linked_songs = Song.objects.filter(album__isnull=False).count()
        self.stdout.write(self.style.SUCCESS(
            f"Done! {album_count} Albums in database. {linked_songs}/{len(song_pairs)} Songs linked to Albums."
        ))
