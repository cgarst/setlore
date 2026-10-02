import json
import gzip
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Union

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction

from apps.core.models import UserProfile
from apps.catalog.models import Artist, Album, Song, Venue, MusicianTenure, ApiCache
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from src.csv_parser import normalize_artist_name

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path(settings.BASE_DIR) / "data" / "cache" / "demo_user_cache.json"


def export_user_data_to_cache(
    username: str = "demouser",
    output_path: Optional[Union[str, Path]] = None,
    database: str = "default"
) -> Dict[str, Any]:
    """
    Exports a user's entire concert collection and associated catalog data
    into a portable, abstract JSON snapshot that can be seeded into any database
    without ID collisions.
    """
    user = User.objects.using(database).filter(username__iexact=username.strip()).first()
    if not user:
        raise ValueError(f"User '{username}' was not found in database '{database}'.")

    profile = getattr(user, 'profile', None)
    if not profile:
        profile, _ = UserProfile.objects.using(database).get_or_create(user=user)

    user_info = {
        "username": user.username,
        "email": user.email,
        "is_staff": user.is_staff,
        "is_superuser": user.is_superuser,
        "theme": profile.theme,
        "time_format": profile.time_format,
        "is_public": profile.is_public,
        "setlistfm_username": profile.setlistfm_username,
        "setlistfm_api_key": profile.setlistfm_api_key,
        "ignored_artists": profile.ignored_artists or [],
    }

    # Fetch all user concerts with related data
    concerts_qs = (
        Concert.objects.using(database)
        .filter(user=user)
        .select_related('venue')
        .prefetch_related('artists__artist', 'artists__songs__song__album')
        .order_by('date', 'year', 'id')
    )

    venues_dict = {}
    artists_dict = {}
    albums_dict = {}
    songs_dict = {}
    tenures_list = []
    concerts_list = []

    for c in concerts_qs:
        v = c.venue
        v_data = None
        if v:
            v_key = v.name.lower().strip()
            v_data = {
                "name": v.name,
                "city": v.city or "",
                "state": v.state or "",
                "country": v.country or "United States",
                "latitude": v.latitude,
                "longitude": v.longitude,
                "geocode_source": v.geocode_source or "unresolved"
            }
            venues_dict[v_key] = v_data

        ca_entries = []
        for ca in c.artists.all():
            art = ca.artist
            art_name = art.name if art else c.primary_artist
            if art:
                art_key = art.normalized_name or normalize_artist_name(art.name)
                if art_key not in artists_dict:
                    artists_dict[art_key] = {
                        "name": art.name,
                        "normalized_name": art.normalized_name,
                        "mbid": art.mbid,
                    }
                    # Include tenures for this artist
                    for mt in art.members.all():
                        tenures_list.append({
                            "artist_normalized": art_key,
                            "artist_name": art.name,
                            "musician_name": mt.musician_name,
                            "role": mt.role,
                            "instrument": mt.instrument,
                            "start_year": mt.start_year,
                            "end_year": mt.end_year
                        })

            songs_entries = []
            for cs in ca.songs.all():
                s_obj = cs.song
                alb_data = None
                if s_obj and s_obj.album:
                    alb = s_obj.album
                    alb_data = {
                        "artist_name": alb.artist.name if alb.artist else art_name,
                        "title": alb.title,
                        "clean_title": alb.clean_title,
                        "release_year": alb.release_year,
                        "album_type": alb.album_type
                    }
                    alb_key = (alb_data["artist_name"].lower(), alb.clean_title)
                    albums_dict[str(alb_key)] = alb_data

                songs_entries.append({
                    "raw_song_name": cs.raw_song_name,
                    "title": s_obj.title if s_obj else cs.raw_song_name,
                    "clean_title": s_obj.clean_title if s_obj else cs.raw_song_name.lower().strip(),
                    "set_name": cs.set_name,
                    "is_encore": cs.is_encore,
                    "encore_number": cs.encore_number,
                    "track_num": cs.track_num,
                    "total_tracks": cs.total_tracks,
                    "pct_position": cs.pct_position,
                    "slot": cs.slot,
                    "slot_category": cs.slot_category,
                    "is_cover": cs.is_cover,
                    "original_artist": cs.original_artist,
                    "info": cs.info,
                    "album": alb_data
                })

            ca_entries.append({
                "artist_name": art_name,
                "billing_order": ca.billing_order,
                "setlistfm_id": ca.setlistfm_id or "",
                "setlist_url": ca.setlist_url or "",
                "has_setlist": ca.has_setlist,
                "is_favorite": ca.is_favorite,
                "songs": songs_entries
            })

        concerts_list.append({
            "date": c.date.strftime("%Y-%m-%d") if c.date else None,
            "raw_date": c.raw_date,
            "year": c.year,
            "venue_name": c.raw_venue or (v.name if v else ""),
            "venue": v_data,
            "primary_artist": c.primary_artist,
            "raw_artists": c.raw_artists,
            "seen_before": c.seen_before,
            "notes": c.notes,
            "source": c.source,
            "is_custom_offline": c.is_custom_offline,
            "is_favorite": c.is_favorite,
            "artists": ca_entries
        })

    payload = {
        "version": "1.0",
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "source_username": user.username,
        "total_concerts": len(concerts_list),
        "total_artists": len(artists_dict),
        "total_venues": len(venues_dict),
        "user": user_info,
        "catalog": {
            "venues": list(venues_dict.values()),
            "artists": list(artists_dict.values()),
            "musician_tenures": tenures_list,
            "albums": list(albums_dict.values())
        },
        "concerts": concerts_list
    }

    target_path = Path(output_path) if output_path else DEFAULT_CACHE_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if str(target_path).endswith('.gz'):
        with gzip.open(target_path, 'wt', encoding='utf-8') as f:
            json.dump(payload, f)
    else:
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)

    logger.info("Exported demo cache to %s (%d concerts)", target_path, len(concerts_list))
    return payload


def populate_user_data_from_cache(
    cache_input: Optional[Union[str, Path, Dict[str, Any]]] = None,
    target_username: Optional[str] = None,
    database: str = "default",
    overwrite: bool = False,
    create_superuser: bool = False,
    default_password: str = "demopassword123"
) -> Dict[str, Any]:
    """
    Populates or refreshes demo user data and associated catalog items into the target database.
    Abstract and key-collision safe across blank databases, production mirrors, or existing users.
    """
    if isinstance(cache_input, dict):
        cache_data = cache_input
    else:
        cache_path = Path(cache_input) if cache_input else DEFAULT_CACHE_PATH
        if not cache_path.exists():
            # Try fallback to uncompressed / compressed variants
            if str(cache_path).endswith('.gz') and cache_path.with_suffix('').exists():
                cache_path = cache_path.with_suffix('')
            elif not str(cache_path).endswith('.gz') and Path(str(cache_path) + '.gz').exists():
                cache_path = Path(str(cache_path) + '.gz')
            else:
                raise FileNotFoundError(f"Demo user cache file not found at: {cache_path}")

        if str(cache_path).endswith('.gz'):
            with gzip.open(cache_path, 'rt', encoding='utf-8') as f:
                cache_data = json.load(f)
        else:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

    user_meta = cache_data.get("user", {})
    resolved_username = target_username or user_meta.get("username", "demouser")

    stats = {
        "user_created": False,
        "concerts_created": 0,
        "concerts_skipped": 0,
        "artists_created": 0,
        "venues_created": 0,
        "songs_created": 0,
        "albums_created": 0,
        "tenures_created": 0
    }

    with transaction.atomic(using=database):
        # 1. User & Profile Setup
        user, user_created = User.objects.using(database).get_or_create(
            username=resolved_username,
            defaults={
                'email': user_meta.get('email', ''),
                'is_staff': user_meta.get('is_staff', False) or create_superuser,
                'is_superuser': user_meta.get('is_superuser', False) or create_superuser,
            }
        )
        if user_created:
            user.set_password(default_password)
            user.save(using=database)
            stats["user_created"] = True

        profile, _ = UserProfile.objects.using(database).get_or_create(user=user)
        profile.theme = user_meta.get("theme", profile.theme or "Mystic Dream")
        if profile.theme == "Default":
            profile.theme = "Mystic Dream"
        profile.time_format = user_meta.get("time_format", profile.time_format or "12")
        profile.is_public = user_meta.get("is_public", profile.is_public)
        if user_meta.get("setlistfm_username"):
            profile.setlistfm_username = user_meta.get("setlistfm_username")
        if user_meta.get("setlistfm_api_key"):
            profile.setlistfm_api_key = user_meta.get("setlistfm_api_key")
        if user_meta.get("ignored_artists"):
            profile.ignored_artists = user_meta.get("ignored_artists")
        profile.save(using=database)

        # If overwrite is enabled, clear previous concerts for this user
        if overwrite:
            Concert.objects.using(database).filter(user=user).delete()

        # 2. Ingest Catalog: Venues
        venues_cache = {v.name.lower(): v for v in Venue.objects.using(database).all()}
        catalog_venues = cache_data.get("catalog", {}).get("venues", [])
        for v_entry in catalog_venues:
            v_name = (v_entry.get("name") or "").strip()
            if not v_name:
                continue
            v_low = v_name.lower()
            if v_low not in venues_cache:
                v_obj = Venue.objects.using(database).create(
                    name=v_name,
                    city=v_entry.get("city", ""),
                    state=v_entry.get("state", ""),
                    country=v_entry.get("country", "United States"),
                    latitude=v_entry.get("latitude"),
                    longitude=v_entry.get("longitude"),
                    geocode_source=v_entry.get("geocode_source", "unresolved")
                )
                venues_cache[v_low] = v_obj
                stats["venues_created"] += 1
            else:
                # Fill missing coords if present in cache
                existing_v = venues_cache[v_low]
                if (existing_v.latitude is None or existing_v.longitude is None) and (v_entry.get("latitude") is not None):
                    existing_v.latitude = v_entry.get("latitude")
                    existing_v.longitude = v_entry.get("longitude")
                    existing_v.geocode_source = v_entry.get("geocode_source", "setlistfm")
                    existing_v.save(using=database, update_fields=['latitude', 'longitude', 'geocode_source'])

        # 3. Ingest Catalog: Artists & MusicianTenures
        artists_cache = {a.normalized_name: a for a in Artist.objects.using(database).all()}
        for a_entry in cache_data.get("catalog", {}).get("artists", []):
            raw_art_name = a_entry.get("name", "").strip()
            if not raw_art_name:
                continue
            norm_k = a_entry.get("normalized_name") or normalize_artist_name(raw_art_name) or raw_art_name.lower()
            if norm_k not in artists_cache:
                art_obj, _ = Artist.get_or_create_artist(raw_art_name)
                if art_obj:
                    if a_entry.get("mbid"):
                        art_obj.mbid = a_entry.get("mbid")
                        art_obj.save(using=database, update_fields=['mbid'])
                    artists_cache[norm_k] = art_obj
                    stats["artists_created"] += 1

        for mt_entry in cache_data.get("catalog", {}).get("musician_tenures", []):
            art_norm = mt_entry.get("artist_normalized")
            art_obj = artists_cache.get(art_norm)
            if not art_obj and mt_entry.get("artist_name"):
                can_n = normalize_artist_name(mt_entry.get("artist_name")) or mt_entry.get("artist_name").lower()
                art_obj = artists_cache.get(can_n)

            if art_obj:
                m_name = mt_entry.get("musician_name", "").strip()
                s_yr = mt_entry.get("start_year", 1900)
                if m_name:
                    e_yr = mt_entry.get("end_year")
                    m_mbid = mt_entry.get("musician_mbid")
                    _, is_mt_new = MusicianTenure.objects.using(database).get_or_create(
                        artist=art_obj,
                        musician_name=m_name,
                        start_year=s_yr,
                        end_year=e_yr,
                        defaults={
                            "role": mt_entry.get("role", "Musician"),
                            "instrument": mt_entry.get("instrument", "Other"),
                            "musician_mbid": m_mbid
                        }
                    )
                    if is_mt_new:
                        stats["tenures_created"] += 1

        # 4. Ingest Catalog: Albums
        albums_cache = {}
        for alb_entry in cache_data.get("catalog", {}).get("albums", []):
            art_name = alb_entry.get("artist_name", "").strip()
            clean_t = alb_entry.get("clean_title", "").strip().lower()
            if not art_name or not clean_t:
                continue
            art_norm = normalize_artist_name(art_name) or art_name.lower()
            art_obj = artists_cache.get(art_norm)
            if not art_obj:
                art_obj, _ = Artist.get_or_create_artist(art_name)
                if art_obj:
                    artists_cache[art_norm] = art_obj

            if art_obj:
                alb_obj, is_alb_new = Album.objects.using(database).get_or_create(
                    artist=art_obj,
                    clean_title=clean_t,
                    defaults={
                        "title": alb_entry.get("title", clean_t.title()),
                        "release_year": alb_entry.get("release_year"),
                        "album_type": alb_entry.get("album_type", "album")
                    }
                )
                albums_cache[(art_obj.id, clean_t)] = alb_obj
                if is_alb_new:
                    stats["albums_created"] += 1

        # 5. Ingest Concerts & Setlists
        songs_cache = {}
        existing_user_concerts = {
            (c.date.strftime("%Y-%m-%d") if c.date else c.raw_date, (c.primary_artist or "").lower().strip())
            for c in Concert.objects.using(database).filter(user=user)
        }

        for c_entry in cache_data.get("concerts", []):
            date_s = c_entry.get("date")
            raw_date_s = c_entry.get("raw_date") or date_s or ""
            prim_art = (c_entry.get("primary_artist") or "").strip()
            date_obj = None
            if date_s:
                try:
                    date_obj = datetime.strptime(date_s, "%Y-%m-%d").date()
                except Exception:
                    pass

            c_key = (date_s if date_s else raw_date_s, prim_art.lower())
            if c_key in existing_user_concerts and not overwrite:
                stats["concerts_skipped"] += 1
                continue

            # Resolve venue
            v_obj = None
            v_data = c_entry.get("venue")
            v_name = (c_entry.get("venue_name") or (v_data.get("name") if v_data else "") or "").strip()
            if v_name:
                v_low = v_name.lower()
                v_obj = venues_cache.get(v_low)
                if not v_obj:
                    v_obj = Venue.objects.using(database).create(
                        name=v_name,
                        city=v_data.get("city", "") if v_data else "",
                        state=v_data.get("state", "") if v_data else "",
                        country=v_data.get("country", "United States") if v_data else "United States",
                        latitude=v_data.get("latitude") if v_data else None,
                        longitude=v_data.get("longitude") if v_data else None,
                        geocode_source=v_data.get("geocode_source", "unresolved") if v_data else "unresolved"
                    )
                    venues_cache[v_low] = v_obj

            concert = Concert.objects.using(database).create(
                user=user,
                date=date_obj,
                raw_date=raw_date_s,
                year=c_entry.get("year"),
                venue=v_obj,
                raw_venue=v_name,
                primary_artist=prim_art,
                raw_artists=c_entry.get("raw_artists") or prim_art,
                seen_before=c_entry.get("seen_before", ""),
                notes=c_entry.get("notes", ""),
                source=c_entry.get("source", "setlistfm"),
                is_custom_offline=c_entry.get("is_custom_offline", False),
                is_favorite=c_entry.get("is_favorite", False)
            )
            existing_user_concerts.add(c_key)
            stats["concerts_created"] += 1

            for ca_entry in c_entry.get("artists", []):
                ca_art_name = ca_entry.get("artist_name") or prim_art
                ca_norm = normalize_artist_name(ca_art_name) or ca_art_name.lower()
                art_obj = artists_cache.get(ca_norm)
                if not art_obj:
                    art_obj, _ = Artist.get_or_create_artist(ca_art_name)
                    if art_obj:
                        artists_cache[ca_norm] = art_obj

                if not art_obj:
                    continue

                ca = ConcertArtist.objects.using(database).create(
                    concert=concert,
                    artist=art_obj,
                    billing_order=ca_entry.get("billing_order", 0),
                    setlistfm_id=ca_entry.get("setlistfm_id", ""),
                    setlist_url=ca_entry.get("setlist_url", ""),
                    has_setlist=ca_entry.get("has_setlist", False),
                    is_favorite=ca_entry.get("is_favorite", False)
                )

                songs_list = ca_entry.get("songs", [])
                cs_objs = []
                for s_entry in songs_list:
                    song_title = (s_entry.get("title") or s_entry.get("raw_song_name") or "").strip()
                    clean_s_key = s_entry.get("clean_title") or song_title.lower().strip()
                    cache_k = (art_obj.id, clean_s_key)
                    if cache_k in songs_cache:
                        song_obj = songs_cache[cache_k]
                    else:
                        song_obj = Song.objects.using(database).filter(artist=art_obj, clean_title=clean_s_key).first()
                        if not song_obj:
                            alb_obj = None
                            if s_entry.get("album"):
                                alb_k = (art_obj.id, s_entry["album"].get("clean_title", "").lower().strip())
                                alb_obj = albums_cache.get(alb_k)

                            song_obj = Song.objects.using(database).create(
                                artist=art_obj,
                                clean_title=clean_s_key,
                                title=song_title,
                                album=alb_obj,
                                is_cover=s_entry.get("is_cover", False),
                                original_artist=s_entry.get("original_artist") or None
                            )
                            stats["songs_created"] += 1
                        songs_cache[cache_k] = song_obj

                    cs_objs.append(ConcertSong(
                        concert_artist=ca,
                        song=song_obj,
                        raw_song_name=s_entry.get("raw_song_name", song_title),
                        set_name=s_entry.get("set_name", "Main Set"),
                        is_encore=s_entry.get("is_encore", False),
                        encore_number=s_entry.get("encore_number"),
                        track_num=s_entry.get("track_num", 1),
                        total_tracks=s_entry.get("total_tracks", len(songs_list)),
                        pct_position=s_entry.get("pct_position", 100),
                        slot=s_entry.get("slot", "Mid-Set"),
                        slot_category=s_entry.get("slot_category", "mid"),
                        is_cover=s_entry.get("is_cover", False),
                        original_artist=s_entry.get("original_artist", ""),
                        info=s_entry.get("info", "")
                    ))

                if cs_objs:
                    ConcertSong.objects.using(database).bulk_create(cs_objs)

        # Clear bundle cache
        ApiCache.objects.using(database).filter(cache_key=f"user_dashboard_bundle_{user.id}").delete()

    return {
        "status": "success",
        "username": user.username,
        "database": database,
        **stats
    }
