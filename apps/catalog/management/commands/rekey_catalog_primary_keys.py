import re
from django.core.management.base import BaseCommand
from django.db import transaction
from apps.catalog.models import Artist, Album, Song, Venue, MusicianTenure
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from src.id_utils import (
    generate_offline_artist_id,
    generate_offline_album_id,
    generate_offline_song_id,
    generate_offline_venue_id,
    is_offline_id
)
from src.album_enricher import AlbumEnricher
from src.musician_enricher import MusicianEnricher

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def is_valid_uuid(val: str) -> bool:
    return bool(val and UUID_RE.match(str(val).strip()))


class Command(BaseCommand):
    help = "Re-keys Artist, Venue, Album, and Song primary keys to canonical upstream IDs (MBID/Setlist.fm) or deterministic offline IDs."

    def add_arguments(self, parser):
        parser.add_argument('--artists-only', action='store_true', help='Only re-key artists')
        parser.add_argument('--venues-only', action='store_true', help='Only re-key venues')
        parser.add_argument('--albums-only', action='store_true', help='Only re-key albums')
        parser.add_argument('--songs-only', action='store_true', help='Only re-key songs')

    def handle(self, *args, **options):
        artists_only = options.get('artists_only')
        venues_only = options.get('venues_only')
        albums_only = options.get('albums_only')
        songs_only = options.get('songs_only')

        run_all = not (artists_only or venues_only or albums_only or songs_only)

        if run_all or artists_only:
            self.rekey_artists()

        if run_all or venues_only:
            self.rekey_venues()

        if run_all or albums_only:
            self.rekey_albums()

        if run_all or songs_only:
            self.rekey_songs()

        self.stdout.write(self.style.SUCCESS("\nDone! All catalog primary keys verified and re-keyed."))

    def rekey_artists(self):
        self.stdout.write("\n=== 1. Re-keying Artists to MusicBrainz MBIDs ===")
        musician_enricher = MusicianEnricher()
        artists = list(Artist.objects.all())
        updated_count = 0

        for art in artists:
            mbid_val = getattr(art, 'mbid', None)
            if not is_valid_uuid(mbid_val):
                resolved_mbid = musician_enricher.search_artist_mbid(art.name)
                if resolved_mbid and is_valid_uuid(resolved_mbid):
                    mbid_val = resolved_mbid

            if is_valid_uuid(mbid_val):
                target_id = mbid_val.strip()
                is_offline = False
            else:
                target_id = generate_offline_artist_id(art.name)
                is_offline = True

            if art.id == target_id:
                if art.is_custom_offline != is_offline:
                    art.is_custom_offline = is_offline
                    art.save(update_fields=['is_custom_offline'])
                continue

            with transaction.atomic():
                existing = Artist.objects.filter(id=target_id).first()
                old_id = art.id
                if not existing:
                    name_ref = art.name
                    norm_ref = art.normalized_name
                    # Create placeholder to hold relationships
                    new_art = Artist.objects.create(
                        id=target_id,
                        name=f"__tmp__{target_id}",
                        normalized_name=f"__tmp__{target_id}",
                        is_custom_offline=is_offline
                    )
                    # Re-point child FKs
                    Album.objects.filter(artist_id=old_id).update(artist=new_art)
                    Song.objects.filter(artist_id=old_id).update(artist=new_art)
                    ConcertArtist.objects.filter(artist_id=old_id).update(artist=new_art)
                    MusicianTenure.objects.filter(artist_id=old_id).update(artist=new_art)
                    # Delete old artist now that it has 0 children
                    art.delete()
                    # Restore original canonical name
                    new_art.name = name_ref
                    new_art.normalized_name = norm_ref
                    new_art.save(update_fields=['name', 'normalized_name'])
                else:
                    new_art = existing
                    Album.objects.filter(artist_id=old_id).update(artist=new_art)
                    Song.objects.filter(artist_id=old_id).update(artist=new_art)
                    ConcertArtist.objects.filter(artist_id=old_id).update(artist=new_art)
                    MusicianTenure.objects.filter(artist_id=old_id).update(artist=new_art)
                    if art.id != existing.id:
                        art.delete()

            updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Artists complete! Re-keyed {updated_count}/{len(artists)} artists."
        ))

    def rekey_venues(self):
        self.stdout.write("\n=== 2. Re-keying Venues to Setlist.fm IDs ===")
        import json, glob
        from src.config import SETLIST_CACHE_DIR
        v_map = {}
        for f in glob.glob(str(SETLIST_CACHE_DIR / '*.json')):
            try:
                data = json.load(open(f, encoding="utf-8"))
                items = data if isinstance(data, list) else data.get('setlist', [])
                for sl in items:
                    if isinstance(sl, dict) and sl.get('venue'):
                        v_entry = sl['venue']
                        if v_entry.get('name') and v_entry.get('id'):
                            v_map[v_entry['name'].lower().strip()] = v_entry['id']
            except Exception:
                pass

        venues = list(Venue.objects.all())
        updated_count = 0

        for v in venues:
            sl_id = (getattr(v, 'setlistfm_id', '') or '').strip() or v_map.get(v.name.lower().strip())
            if sl_id and not sl_id.isdigit():
                target_id = sl_id
                is_offline = False
            elif sl_id and sl_id.isdigit() and len(sl_id) > 6:
                target_id = sl_id
                is_offline = False
            elif is_offline_id(v.id) and not sl_id:
                target_id = v.id
                is_offline = True
            else:
                target_id = generate_offline_venue_id(v.name, v.city, v.state, v.country)
                is_offline = True

            if v.id == target_id:
                if v.is_custom_offline != is_offline:
                    v.is_custom_offline = is_offline
                    v.save(update_fields=['is_custom_offline'])
                continue

            with transaction.atomic():
                existing = Venue.objects.filter(id=target_id).first()
                old_id = v.id
                if not existing:
                    name_ref = v.name
                    v_dict = {
                        'city': v.city,
                        'state': v.state,
                        'country': v.country,
                        'latitude': v.latitude,
                        'longitude': v.longitude,
                        'geocode_source': v.geocode_source,
                        'is_custom_offline': is_offline
                    }
                    new_v = Venue.objects.create(
                        id=target_id,
                        name=f"__tmp__{target_id}",
                        **v_dict
                    )
                    Concert.objects.filter(venue_id=old_id).update(venue=new_v)
                    v.delete()
                    new_v.name = name_ref
                    new_v.save(update_fields=['name'])
                else:
                    new_v = existing
                    Concert.objects.filter(venue_id=old_id).update(venue=new_v)
                    if v.id != existing.id:
                        v.delete()

            updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Venues complete! Re-keyed {updated_count}/{len(venues)} venues."
        ))

    def rekey_albums(self):
        self.stdout.write("\n=== 3. Re-keying Albums to Release Group MBIDs ===")
        enricher = AlbumEnricher()
        albums = list(Album.objects.all().select_related('artist'))
        updated_count = 0

        for alb in albums:
            if is_valid_uuid(alb.id):
                if alb.is_custom_offline:
                    alb.is_custom_offline = False
                    alb.save(update_fields=['is_custom_offline'])
                continue

            art_name = alb.artist.name
            alb_title = alb.clean_title or alb.title
            rg_mbid = enricher.get_release_group_mbid(art_name, alb_title)

            if rg_mbid and is_valid_uuid(rg_mbid):
                target_id = rg_mbid
                is_offline = False
            else:
                target_id = generate_offline_album_id(alb.artist_id, alb.clean_title)
                is_offline = True

            if alb.id == target_id:
                if alb.is_custom_offline != is_offline:
                    alb.is_custom_offline = is_offline
                    alb.save(update_fields=['is_custom_offline'])
                continue

            with transaction.atomic():
                existing = Album.objects.filter(id=target_id).first()
                old_id = alb.id
                if not existing:
                    clean_ref = alb.clean_title
                    new_alb = Album.objects.create(
                        id=target_id,
                        artist=alb.artist,
                        title=alb.title,
                        clean_title=f"__tmp__{target_id}",
                        release_year=alb.release_year,
                        album_type=alb.album_type,
                        is_custom_offline=is_offline
                    )
                    Song.objects.filter(album_id=old_id).update(album=new_alb)
                    alb.delete()
                    new_alb.clean_title = clean_ref
                    new_alb.save(update_fields=['clean_title'])
                else:
                    new_alb = existing
                    if alb.release_year and not new_alb.release_year:
                        new_alb.release_year = alb.release_year
                        new_alb.save(update_fields=['release_year'])
                    Song.objects.filter(album_id=old_id).update(album=new_alb)
                    if alb.id != existing.id:
                        alb.delete()

            updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Albums complete! Re-keyed {updated_count}/{len(albums)} albums."
        ))

    def rekey_songs(self):
        self.stdout.write("\n=== 4. Re-keying Songs to Recording MBIDs ===")
        songs = list(Song.objects.all().select_related('artist', 'album'))
        updated_count = 0

        for song in songs:
            song_mbid = getattr(song, 'mbid', None)
            if is_valid_uuid(song_mbid):
                target_id = song_mbid
                is_offline = False
            elif is_valid_uuid(song.id):
                target_id = song.id
                is_offline = False
            else:
                target_id = generate_offline_song_id(song.artist_id, song.clean_title)
                is_offline = True

            if song.id == target_id:
                if song.is_custom_offline != is_offline:
                    song.is_custom_offline = is_offline
                    song.save(update_fields=['is_custom_offline'])
                continue

            with transaction.atomic():
                existing = Song.objects.filter(id=target_id).first()
                old_id = song.id
                if not existing:
                    clean_ref = song.clean_title
                    new_s = Song.objects.create(
                        id=target_id,
                        artist=song.artist,
                        album=song.album,
                        title=song.title,
                        clean_title=f"__tmp__{target_id}",
                        release_year=song.release_year,
                        is_cover=song.is_cover,
                        original_artist=song.original_artist,
                        is_custom_offline=is_offline
                    )
                    ConcertSong.objects.filter(song_id=old_id).update(song=new_s)
                    song.delete()
                    new_s.clean_title = clean_ref
                    new_s.save(update_fields=['clean_title'])
                else:
                    new_s = existing
                    if song.album and not new_s.album:
                        new_s.album = song.album
                        new_s.save(update_fields=['album'])
                    if song.release_year and not new_s.release_year:
                        new_s.release_year = song.release_year
                        new_s.save(update_fields=['release_year'])
                    ConcertSong.objects.filter(song_id=old_id).update(song=new_s)
                    if song.id != existing.id:
                        song.delete()

            updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Songs complete! Re-keyed {updated_count}/{len(songs)} songs."
        ))
