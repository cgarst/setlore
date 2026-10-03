import re
from typing import Optional, Dict, Any
from django.db import transaction
from apps.catalog.models import Artist, Album, Song, MusicianTenure, ApiCache
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from src.id_utils import generate_offline_artist_id, is_offline_id
from src.csv_parser import normalize_artist_name
from src.musician_enricher import MusicianEnricher

UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


def is_valid_uuid(val: Optional[str]) -> bool:
    return bool(val and UUID_RE.match(str(val).strip()))


def merge_artists(source_artist: Artist, target_artist: Artist) -> Artist:
    """
    Merges all child records (ConcertArtist, Album, Song, MusicianTenure)
    from source_artist into target_artist, cleanly resolving duplicate unique
    constraints, and deletes source_artist.
    """
    if source_artist.id == target_artist.id:
        return target_artist

    with transaction.atomic():
        # 1. Merge ConcertArtists
        for ca_src in list(ConcertArtist.objects.filter(artist=source_artist)):
            ca_target = ConcertArtist.objects.filter(concert=ca_src.concert, artist=target_artist).first()
            if ca_target:
                # Re-parent all ConcertSong performances
                for cs in list(ca_src.songs.all()):
                    cs.concert_artist = ca_target
                    cs.save(update_fields=['concert_artist'])

                # Preserve metadata flags
                if ca_src.setlistfm_id and not ca_target.setlistfm_id:
                    ca_target.setlistfm_id = ca_src.setlistfm_id
                    ca_target.setlist_url = ca_src.setlist_url or ca_target.setlist_url
                if ca_src.is_favorite:
                    ca_target.is_favorite = True
                ca_target.has_setlist = ca_target.has_setlist or ca_src.has_setlist
                ca_target.save()
                ca_src.delete()
            else:
                ca_src.artist = target_artist
                ca_src.save(update_fields=['artist'])

        # 2. Merge Albums
        for alb_src in list(Album.objects.filter(artist=source_artist)):
            alb_target = Album.objects.filter(artist=target_artist, clean_title=alb_src.clean_title).first()
            if alb_target:
                Song.objects.filter(album=alb_src).update(album=alb_target)
                alb_src.delete()
            else:
                alb_src.artist = target_artist
                alb_src.save(update_fields=['artist'])

        # 3. Merge Songs
        for song_src in list(Song.objects.filter(artist=source_artist)):
            song_target = Song.objects.filter(artist=target_artist, clean_title=song_src.clean_title).first()
            if song_target:
                ConcertSong.objects.filter(song=song_src).update(song=song_target)
                song_src.delete()
            else:
                song_src.artist = target_artist
                song_src.save(update_fields=['artist'])

        # 4. Merge MusicianTenures
        for mt_src in list(MusicianTenure.objects.filter(artist=source_artist)):
            mt_target = MusicianTenure.objects.filter(
                artist=target_artist,
                musician_name=mt_src.musician_name,
                start_year=mt_src.start_year,
                end_year=mt_src.end_year
            ).first()
            if mt_target:
                mt_src.delete()
            else:
                mt_src.artist = target_artist
                mt_src.save(update_fields=['artist'])

        # 5. Delete source artist
        source_artist.delete()

    return target_artist


def rename_or_merge_artist(source_identifier: str, new_name: str, user=None) -> Dict[str, Any]:
    """
    Renames an artist or merges it with an existing artist in the catalog.
    - If new_name matches an existing Artist, merges source into target.
    - If new_name is new, attempts MusicBrainz resolution.
      - If MBID resolved: rekeys artist to canonical MBID.
      - If unresolved: assigns deterministic offline ID and queues for sync resolution.
    - Invalidates dashboard cache bundles.
    """
    new_name = str(new_name or '').strip()
    if not new_name:
        raise ValueError("New artist name cannot be empty.")

    source_artist = (
        Artist.objects.filter(id=source_identifier).first()
        or Artist.objects.filter(name__iexact=source_identifier).first()
        or Artist.objects.filter(normalized_name=normalize_artist_name(source_identifier).lower()).first()
    )

    if not source_artist:
        raise ValueError(f"Artist '{source_identifier}' not found in database.")

    if source_artist.name == new_name:
        return {
            'success': True,
            'action': 'unchanged',
            'artist_name': source_artist.name,
            'target_id': source_artist.id,
            'mbid': source_artist.mbid,
            'is_resolved': not source_artist.is_custom_offline,
            'message': f"Artist name is already '{source_artist.name}'."
        }

    norm_new_name = normalize_artist_name(new_name).lower()
    existing_target = (
        Artist.objects.filter(normalized_name=norm_new_name).exclude(id=source_artist.id).first()
        or Artist.objects.filter(name__iexact=new_name).exclude(id=source_artist.id).first()
    )

    if existing_target:
        merged_art = merge_artists(source_artist, existing_target)
        ApiCache.objects.filter(cache_key__startswith="user_dashboard_bundle_").delete()
        return {
            'success': True,
            'action': 'merged',
            'artist_name': merged_art.name,
            'target_id': merged_art.id,
            'mbid': merged_art.mbid,
            'is_resolved': not merged_art.is_custom_offline,
            'message': f"Merged '{source_artist.name}' into existing artist '{merged_art.name}'."
        }

    # If target does not exist, query MusicBrainz for canonical MBID resolution
    musician_enricher = MusicianEnricher()
    resolved_mbid = musician_enricher.search_artist_mbid(new_name)

    if resolved_mbid and is_valid_uuid(resolved_mbid):
        existing_by_mbid = Artist.objects.filter(id=resolved_mbid).exclude(id=source_artist.id).first()
        if existing_by_mbid:
            merged_art = merge_artists(source_artist, existing_by_mbid)
            ApiCache.objects.filter(cache_key__startswith="user_dashboard_bundle_").delete()
            return {
                'success': True,
                'action': 'merged',
                'artist_name': merged_art.name,
                'target_id': merged_art.id,
                'mbid': merged_art.mbid,
                'is_resolved': True,
                'message': f"Merged '{source_artist.name}' into '{merged_art.name}' (MusicBrainz MBID: {resolved_mbid})."
            }
        else:
            with transaction.atomic():
                new_art = Artist.objects.create(
                    id=resolved_mbid,
                    name=new_name,
                    normalized_name=norm_new_name,
                    is_custom_offline=False
                )
                merge_artists(source_artist, new_art)
                try:
                    musician_enricher.enrich_artist(new_art.name, artist_obj=new_art)
                except Exception:
                    pass

            ApiCache.objects.filter(cache_key__startswith="user_dashboard_bundle_").delete()
            return {
                'success': True,
                'action': 'renamed',
                'artist_name': new_art.name,
                'target_id': new_art.id,
                'mbid': new_art.mbid,
                'is_resolved': True,
                'message': f"Renamed to '{new_name}' and successfully resolved MusicBrainz catalog (MBID: {resolved_mbid})!"
            }

    # Fallback to deterministic offline ID if not found on MusicBrainz
    target_offline_id = generate_offline_artist_id(new_name)
    with transaction.atomic():
        if source_artist.id == target_offline_id:
            source_artist.name = new_name
            source_artist.normalized_name = norm_new_name
            source_artist.is_custom_offline = True
            source_artist.save(update_fields=['name', 'normalized_name', 'is_custom_offline'])
            new_art = source_artist
        else:
            new_art = Artist.objects.create(
                id=target_offline_id,
                name=new_name,
                normalized_name=norm_new_name,
                is_custom_offline=True
            )
            merge_artists(source_artist, new_art)

    ApiCache.objects.filter(cache_key__startswith="user_dashboard_bundle_").delete()
    return {
        'success': True,
        'action': 'renamed',
        'artist_name': new_art.name,
        'target_id': new_art.id,
        'mbid': None,
        'is_resolved': False,
        'message': f"Renamed to '{new_name}'. Queued for MusicBrainz catalog resolution at next sync."
    }
