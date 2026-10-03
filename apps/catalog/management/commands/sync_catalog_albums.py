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
        parser.add_argument(
            '--resolve-mbids',
            action='store_true',
            help='Query MusicBrainz to resolve Release Group MBIDs for albums currently with offline IDs.'
        )

    def handle(self, *args, **options):
        refresh = options.get('refresh', False)
        resolve_mbids = options.get('resolve_mbids', False)
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

        if resolve_mbids:
            self.stdout.write("Resolving MusicBrainz Release Group MBIDs for offline albums...")
            offline_albums = list(Album.objects.filter(id__startswith='offline:album').select_related('artist'))
            total_off = len(offline_albums)
            resolved_count = 0
            for idx, alb in enumerate(offline_albums, start=1):
                art_name = alb.artist.name
                alb_title = alb.clean_title or alb.title
                rg_mbid = enricher.get_release_group_mbid(art_name, alb_title)
                if rg_mbid:
                    existing_mbid_alb = Album.objects.filter(id=rg_mbid).first()
                    if not existing_mbid_alb:
                        saved_songs = list(Song.objects.filter(album=alb))
                        artist_ref = alb.artist
                        title_ref = alb.title
                        clean_ref = alb.clean_title
                        year_ref = alb.release_year
                        type_ref = alb.album_type
                        alb.delete()
                        new_alb = Album.objects.create(
                            id=rg_mbid,
                            artist=artist_ref,
                            title=title_ref,
                            clean_title=clean_ref,
                            release_year=year_ref,
                            album_type=type_ref,
                            mbid=rg_mbid,
                            is_custom_offline=False
                        )
                        for s in saved_songs:
                            s.album = new_alb
                            s.save(update_fields=['album'])
                    else:
                        new_alb = existing_mbid_alb
                        if alb.release_year and not new_alb.release_year:
                            new_alb.release_year = alb.release_year
                            new_alb.save(update_fields=['release_year'])
                        Song.objects.filter(album=alb).update(album=new_alb)
                        if alb.id != new_alb.id:
                            alb.delete()
                    resolved_count += 1
                if idx % 10 == 0 or idx == total_off:
                    self.stdout.write(f"  [{idx}/{total_off}] Resolved {resolved_count} album MBIDs...")

        album_count = Album.objects.count()
        mbid_count = Album.objects.exclude(id__startswith='offline:album').count()
        linked_songs = Song.objects.filter(album__isnull=False).count()
        self.stdout.write(self.style.SUCCESS(
            f"Done! {album_count} Albums in database ({mbid_count} with MusicBrainz MBIDs). {linked_songs}/{len(song_pairs)} Songs linked to Albums."
        ))
