import shutil
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.catalog.models import Album, Song, MusicianTenure, ApiCache, Artist
from src.config import MB_CACHE_DIR

class Command(BaseCommand):
    help = "Clears MusicBrainz catalog data (albums, musician tenures, song metadata, orphan records, and cache) to allow a fresh pull on next sync."

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear-cache',
            action='store_true',
            help='Also delete disk cache files in data/cache/musicbrainz/'
        )
        parser.add_argument(
            '--resync',
            action='store_true',
            help='Immediately run fresh dynamic MusicBrainz enrichment for all active songs and artists'
        )

    def handle(self, *args, **options):
        self.stdout.write("Clearing MusicBrainz catalog tables...")

        with transaction.atomic():
            # 1. Unlink album, release year, and recording MBID from all songs
            song_count = Song.objects.count()
            Song.objects.update(album=None, release_year=None, mbid=None)
            self.stdout.write(f"  - Reset album, release year, and recording MBID on {song_count} songs.")

            # 2. Delete all albums
            album_count = Album.objects.count()
            Album.objects.all().delete()
            self.stdout.write(f"  - Deleted {album_count} album records.")

            # 3. Delete musician tenures
            tenure_count = MusicianTenure.objects.count()
            MusicianTenure.objects.all().delete()
            self.stdout.write(f"  - Deleted {tenure_count} musician tenure records.")

            # 4. Reset artist MBIDs on artists
            artist_count = Artist.objects.count()
            Artist.objects.update(mbid=None)
            self.stdout.write(f"  - Reset MusicBrainz MBIDs on {artist_count} artists.")

            # 5. Purge orphan songs not linked to any user concert performances
            orphan_songs = Song.objects.filter(performances__isnull=True)
            orphan_song_count = orphan_songs.count()
            orphan_songs.delete()
            self.stdout.write(f"  - Deleted {orphan_song_count} orphan songs not linked to any concert.")

            # 6. Purge orphan artists not linked to any user concert appearances
            orphan_artists = Artist.objects.filter(concert_appearances__isnull=True)
            orphan_artist_count = orphan_artists.count()
            orphan_artists.delete()
            self.stdout.write(f"  - Deleted {orphan_artist_count} orphan artists not linked to any concert.")

            # 7. Clear API caches
            api_cache_count = ApiCache.objects.count()
            ApiCache.objects.all().delete()
            self.stdout.write(f"  - Cleared {api_cache_count} database API cache entries.")

        if options.get('clear_cache'):
            if MB_CACHE_DIR.exists():
                count = len(list(MB_CACHE_DIR.glob("*.json")))
                shutil.rmtree(MB_CACHE_DIR, ignore_errors=True)
                MB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                self.stdout.write(f"  - Cleared {count} MusicBrainz disk cache files.")

        self.stdout.write(self.style.SUCCESS("Music catalog tables successfully cleared. Ready for fresh upstream pull."))

        if options.get('resync'):
            self.stdout.write("Running fresh MusicBrainz re-sync...")
            from src.album_enricher import AlbumEnricher
            from src.musician_enricher import MusicianEnricher

            # 1. Re-resolve artist MBIDs and enrich musician tenures
            m_enricher = MusicianEnricher()
            for art_obj in Artist.objects.all():
                try:
                    m_enricher.enrich_artist(art_obj.name, artist_obj=art_obj, refresh=True)
                except Exception:
                    pass

            # 2. Enrich albums for songs
            enricher = AlbumEnricher()
            distinct_tracks = list(Song.objects.values_list('artist__name', 'title').distinct())
            self.stdout.write(f"Enriching {len(distinct_tracks)} songs with MusicBrainz...")
            enrich_results = enricher.enrich_catalog(
                [{"artist": a, "song": s} for a, s in distinct_tracks if a and s],
                refresh_unresolved=True
            )

            for art_name, s_name in distinct_tracks:
                if not art_name or not s_name:
                    continue
                key = f"{art_name}_{s_name}".lower()
                info = enrich_results.get(key)
                if not info:
                    continue
                alb_title = info.get("album")
                rel_year = info.get("release_year")
                alb_mbid = info.get("mbid")
                rec_mbid = info.get("recording_id")
                if alb_title and alb_title != "Non-Album / Singles" and alb_title.lower() != "covers":
                    art_obj = Artist.objects.filter(name__iexact=art_name).first()
                    if art_obj:
                        alb_obj, _ = Album.get_or_create_album(
                            artist=art_obj,
                            title=alb_title,
                            mbid=alb_mbid,
                            release_year=rel_year
                        )
                        Song.objects.filter(artist=art_obj, clean_title=s_name.lower().strip()).update(
                            album=alb_obj,
                            release_year=rel_year,
                            mbid=rec_mbid or None
                        )

            self.stdout.write(self.style.SUCCESS("Fresh MusicBrainz re-sync complete!"))
