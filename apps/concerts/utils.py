import re
import urllib.request
import urllib.parse
import json
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

def split_outside_parentheses(s: str, delimiter: str = ',') -> List[str]:
    """Splits a string by a delimiter character only when outside brackets/parentheses."""
    tokens = []
    current = []
    depth = 0
    for char in s:
        if char in '([{':
            depth += 1
            current.append(char)
        elif char in ')]}':
            depth = max(0, depth - 1)
            current.append(char)
        elif char == delimiter and depth == 0:
            token = "".join(current).strip()
            if token:
                tokens.append(token)
            current = []
        else:
            current.append(char)
    token = "".join(current).strip()
    if token:
        tokens.append(token)
    return tokens

def parse_setlist_text(text: str) -> List[Dict[str, Any]]:
    """
    Parses a user-pasted or CSV-exported setlist string into a structured list of track dictionaries.
    Supports multiline text, comma-separated lists, set headers (e.g. 'Set 1:', 'Encore:'),
    numbered tracks ('1. Song'), and cover tags ('(Cover - Artist)', '(Artist cover)').
    """
    if not text or not text.strip():
        return []

    raw_lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    tokens = []
    for line in raw_lines:
        # Split by comma if commas exist outside parentheses
        sub_tokens = split_outside_parentheses(line, delimiter=',')
        tokens.extend(sub_tokens)

    tracks = []
    current_set_name = "Main Set"
    is_encore = False
    encore_number = None
    encore_count = 0
    main_set_count = 1

    # Regex patterns
    encore_regex = re.compile(r'^(?:[-*#=_\s]*)?encore(?:\s*(\d+))?(?:[-*#=_\s:]*)$', re.IGNORECASE)
    inline_encore_regex = re.compile(r'^(?:[-*#=_\s]*)?encore(?:\s*(\d+))?\s*:\s*(.+)$', re.IGNORECASE)
    
    set_regex = re.compile(r'^(?:[-*#=_\s]*)?set\s*(\d+)(?:[-*#=_\s:]*)$', re.IGNORECASE)
    inline_set_regex = re.compile(r'^(?:[-*#=_\s]*)?set\s*(\d+)\s*:\s*(.+)$', re.IGNORECASE)
    
    custom_set_regex = re.compile(r'^(?:[-*#=_\s]*)?(acoustic set|b-sides set|orchestral set|stage \w+)(?:[-*#=_\s:]*)$', re.IGNORECASE)
    inline_custom_set_regex = re.compile(r'^(?:[-*#=_\s]*)?(acoustic set|b-sides set|orchestral set|stage \w+)\s*:\s*(.+)$', re.IGNORECASE)

    divider_regex = re.compile(r'^[-=*_~]{3,}$')
    leading_number_regex = re.compile(r'^\s*(?:\d+[\.\)\-:]\s*|\#\d+\s*)')

    # Cover detection: (Artist cover), (Cover - Artist), (cover of Artist), (Cover)
    cover_regex = re.compile(
        r'[\(\[]\s*(?:cover\s*(?:of|-|by)?\s*([^\)\]]+)|([^\)\]]+?)\s+cover)\s*[\)\]]',
        re.IGNORECASE
    )
    generic_cover_regex = re.compile(r'[\(\[]\s*cover\s*[\)\]]', re.IGNORECASE)

    for token in tokens:
        if not token or divider_regex.match(token):
            continue

        raw_song_line = token

        # 1. Check for inline Encore header e.g. "Encore: Song Name" or "Encore 2: Song Name"
        inline_enc = inline_encore_regex.match(raw_song_line)
        if inline_enc:
            is_encore = True
            encore_count += 1
            num_str = inline_enc.group(1)
            encore_number = int(num_str) if num_str else encore_count
            current_set_name = f"Encore {encore_number}" if encore_number > 1 else "Encore"
            raw_song_line = inline_enc.group(2).strip()
        else:
            # Check for standalone Encore header
            enc_match = encore_regex.match(raw_song_line)
            if enc_match:
                is_encore = True
                encore_count += 1
                num_str = enc_match.group(1)
                encore_number = int(num_str) if num_str else encore_count
                current_set_name = f"Encore {encore_number}" if encore_number > 1 else "Encore"
                continue

        # 2. Check for inline Set header e.g. "Set 2: Song Name"
        inline_set = inline_set_regex.match(raw_song_line)
        if inline_set:
            is_encore = False
            encore_number = None
            set_num = int(inline_set.group(1))
            main_set_count = max(main_set_count, set_num)
            current_set_name = f"Set {set_num}"
            raw_song_line = inline_set.group(2).strip()
        else:
            # Check for standalone Set header
            set_match = set_regex.match(raw_song_line)
            if set_match:
                is_encore = False
                encore_number = None
                set_num = int(set_match.group(1))
                main_set_count = max(main_set_count, set_num)
                current_set_name = f"Set {set_num}"
                continue

        # 3. Check for inline Custom Set header e.g. "Acoustic Set: Song Name"
        inline_cset = inline_custom_set_regex.match(raw_song_line)
        if inline_cset:
            is_encore = False
            encore_number = None
            current_set_name = inline_cset.group(1).title()
            raw_song_line = inline_cset.group(2).strip()
        else:
            # Check for standalone Custom Set header
            custom_set_match = custom_set_regex.match(raw_song_line)
            if custom_set_match:
                is_encore = False
                encore_number = None
                current_set_name = custom_set_match.group(1).title()
                continue

        # Process as a song track
        raw_song_line = leading_number_regex.sub('', raw_song_line).strip()
        if not raw_song_line:
            continue

        is_cover = False
        original_artist = ""
        info = ""

        # Check cover annotations
        cov_m = cover_regex.search(raw_song_line)
        if cov_m:
            is_cover = True
            original_artist = (cov_m.group(1) or cov_m.group(2) or "").strip()
            raw_song_line = cover_regex.sub('', raw_song_line).strip()
        elif generic_cover_regex.search(raw_song_line):
            is_cover = True
            raw_song_line = generic_cover_regex.sub('', raw_song_line).strip()

        # Check for general info in remaining parentheses e.g. "(Acoustic)" or "(with special guest)"
        info_m = re.search(r'[\(\[](.*?)[\)\]]$', raw_song_line)
        if info_m:
            possible_info = info_m.group(1).strip()
            if any(k in possible_info.lower() for k in ["acoustic", "guest", "intro", "extended", "snippet", "jam", "instrumental", "solo", "reprise"]):
                info = possible_info
                raw_song_line = raw_song_line[:info_m.start()].strip()

        # Clean trailing punctuation
        clean_title = raw_song_line.strip(' -–—:;,')
        if not clean_title:
            continue

        tracks.append({
            "title": clean_title,
            "set_name": current_set_name,
            "is_encore": is_encore,
            "encore_number": encore_number,
            "is_cover": is_cover,
            "original_artist": original_artist,
            "info": info
        })

    return tracks

def save_setlist_for_concert_artist(ca, artist_obj, parsed_tracks: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """
    Saves parsed track dictionaries into ConcertSong rows for a ConcertArtist,
    computes track slot categories, marks ca.has_setlist = True, and returns list of (artist_name, song_title).
    """
    from apps.catalog.models import Song
    from apps.concerts.models import ConcertSong

    ca.songs.all().delete()
    if not parsed_tracks:
        ca.has_setlist = False
        ca.save()
        return []

    total_tracks = len(parsed_tracks)
    new_songs_to_enrich = []

    for idx, t in enumerate(parsed_tracks):
        track_num = idx + 1
        pct = round((track_num / total_tracks) * 100) if total_tracks > 0 else 100

        if track_num == 1:
            slot = "Opener"
            slot_category = "opener"
        elif t["is_encore"]:
            if track_num == total_tracks:
                slot = "Show Closer"
            else:
                slot = f"Encore {t['encore_number']}" if t['encore_number'] else "Encore"
            slot_category = "encore"
        elif track_num == total_tracks:
            slot = "Show Closer"
            slot_category = "closer"
        elif pct <= 35:
            slot = "Early Set"
            slot_category = "early"
        elif pct <= 70:
            slot = "Mid-Set"
            slot_category = "mid"
        else:
            slot = "Late Set"
            slot_category = "late"

        clean_title_key = t["title"].lower().strip()
        song_obj, _ = Song.objects.get_or_create(
            artist=artist_obj,
            clean_title=clean_title_key,
            defaults={
                'title': t["title"],
                'is_cover': t["is_cover"],
                'original_artist': t["original_artist"] or None
            }
        )

        ConcertSong.objects.create(
            concert_artist=ca,
            song=song_obj,
            raw_song_name=t["title"],
            set_name=t["set_name"],
            is_encore=t["is_encore"],
            encore_number=t["encore_number"],
            track_num=track_num,
            total_tracks=total_tracks,
            pct_position=pct,
            slot=slot,
            slot_category=slot_category,
            is_cover=t["is_cover"],
            original_artist=t["original_artist"] or '',
            info=t["info"] or ''
        )
        new_songs_to_enrich.append((artist_obj.name, t["title"]))

    ca.has_setlist = True
    ca.save()
    return new_songs_to_enrich

def resolve_venue_coordinates(venue_name: str, city: str = "", state: str = "", country: str = "United States") -> Tuple[Optional[float], Optional[float], str]:
    """
    Attempts to resolve latitude and longitude for a venue dynamically using
    OpenStreetMap Nominatim geocoding with persistent local disk caching.
    """
    from src.config import CACHE_DIR
    v_cache_dir = CACHE_DIR / "venues"
    v_cache_dir.mkdir(parents=True, exist_ok=True)
    
    clean_k = re.sub(r'[^a-zA-Z0-9_-]', '_', venue_name.strip().lower())
    cache_file = v_cache_dir / f"{clean_k}.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached.get("lat") is not None and cached.get("lon") is not None:
                    return cached["lat"], cached["lon"], "nominatim_cache"
        except Exception:
            pass

    # Query Nominatim with strict 2.5s timeout
    query_parts = [p.strip() for p in [venue_name, city, state, country] if p and p.strip()]
    if query_parts:
        query_str = ", ".join(query_parts)
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query_str)}&format=json&limit=1"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    if data and len(data) > 0:
                        lat = float(data[0]['lat'])
                        lon = float(data[0]['lon'])
                        try:
                            with open(cache_file, "w", encoding="utf-8") as f:
                                json.dump({"lat": lat, "lon": lon, "query": query_str}, f)
                        except Exception:
                            pass
                        return lat, lon, "nominatim"
        except Exception as e:
            logger.debug("Nominatim geocoding lookup failed for %s: %s", query_str, e)

    return None, None, "unresolved"

def build_manual_setlist_for_concert_artist(ca) -> Optional[Dict[str, Any]]:
    """
    Constructs a Setlist.fm-compatible setlist dictionary structure from ConcertSong rows
    in the database for a ConcertArtist instance.
    """
    songs = list(ca.songs.all().order_by('track_num'))
    if not songs:
        return None

    # Group songs by (is_encore, encore_number, set_name)
    grouped = []
    seen_groups = {}

    for cs in songs:
        group_key = (cs.is_encore, cs.encore_number, cs.set_name)
        if group_key not in seen_groups:
            group_obj = {
                "name": cs.set_name,
                "is_encore": cs.is_encore,
                "encore_number": cs.encore_number,
                "songs": []
            }
            seen_groups[group_key] = group_obj
            grouped.append(group_obj)

        song_dict = {
            "name": cs.raw_song_name,
            "info": cs.info or ""
        }
        if cs.is_cover:
            song_dict["cover"] = {"name": cs.original_artist or "Cover"}
        seen_groups[group_key]["songs"].append(song_dict)

    sets_list = []
    for g in grouped:
        s_data = {
            "name": g["name"],
            "song": g["songs"]
        }
        if g["is_encore"]:
            s_data["encore"] = g["encore_number"] or 1
        sets_list.append(s_data)

    return {
        "id": f"manual_{ca.concert.id}_{ca.id}",
        "url": ca.setlist_url or "",
        "artist": {"name": ca.artist.name},
        "sets": {"set": sets_list},
        "is_manual": True
    }

def parse_setlistfm_sets_to_tracks(sl: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extracts ordered track dictionaries from a Setlist.fm API setlist payload dictionary.
    """
    sets = sl.get("sets", {}).get("set", []) if isinstance(sl.get("sets"), dict) else []
    tracks = []
    encore_count = 0
    for s in sets:
        if not isinstance(s, dict):
            continue
        is_encore = bool(s.get("encore"))
        if is_encore:
            encore_count += 1
            enc_num = s.get("encore")
            encore_number = enc_num if isinstance(enc_num, int) else encore_count
            set_name = f"Encore {encore_number}" if encore_number > 1 else "Encore"
        else:
            encore_number = None
            set_name = s.get("name") or "Main Set"
        song_list = s.get("song", [])
        if not isinstance(song_list, list):
            song_list = [song_list]
        for song_obj in song_list:
            if not isinstance(song_obj, dict):
                continue
            if song_obj.get("tape"):
                continue
            name = (song_obj.get("name") or "").strip()
            if not name:
                continue
            is_cover = bool(song_obj.get("cover"))
            orig = song_obj.get("cover", {}).get("name", "") if is_cover and isinstance(song_obj.get("cover"), dict) else ""
            info = (song_obj.get("info") or "").strip()
            tracks.append({
                "title": name,
                "set_name": set_name,
                "is_encore": is_encore,
                "encore_number": encore_number,
                "is_cover": is_cover,
                "original_artist": orig,
                "info": info
            })
    return tracks

def import_setlistfm_shows_into_database(user, user_attended: List[Dict[str, Any]], ignored_artists: Optional[List[str]] = None) -> int:
    """
    Ingests attended setlists from Setlist.fm directly into the database as Concert,
    ConcertArtist, and ConcertSong records for the specified user, ensuring existing shows
    are linked and new shows are created automatically.
    """
    from datetime import datetime
    from django.db import transaction
    from apps.catalog.models import Venue, Artist, Song
    from apps.concerts.models import Concert, ConcertArtist, ConcertSong
    from src.csv_parser import normalize_artist_name
    from src.gap_analysis import is_ignored_artist

    if not user_attended:
        return 0

    ignored_list = ignored_artists or []
    venues_cache = {v.name.lower(): v for v in Venue.objects.all()}
    artists_cache = {a.normalized_name: a for a in Artist.objects.all()}
    songs_cache = {}
    imported_count = 0

    with transaction.atomic():
        for sl in user_attended:
            art_dict = sl.get("artist") or {}
            art_name = (art_dict.get("name") or "").strip()
            art_mbid = (art_dict.get("mbid") or "").strip() or None
            if not art_name or is_ignored_artist(art_name, ignored_list):
                continue

            can_art = normalize_artist_name(art_name) or art_name
            norm_art = can_art.lower()
            if norm_art in artists_cache:
                art_obj = artists_cache[norm_art]
                if art_mbid and not art_obj.mbid:
                    art_obj.mbid = art_mbid
                    try:
                        art_obj.save(update_fields=['mbid'])
                    except Exception:
                        pass
            else:
                art_obj, _ = Artist.get_or_create_artist(can_art, mbid=art_mbid)
                if art_obj:
                    artists_cache[norm_art] = art_obj

            if not art_obj:
                continue


            event_date_str = (sl.get("eventDate") or "").strip()
            d_obj = None
            if event_date_str:
                try:
                    d_obj = datetime.strptime(event_date_str, "%d-%m-%Y").date()
                except Exception:
                    pass
            raw_date = d_obj.strftime("%m-%d-%Y") if d_obj else event_date_str
            year = d_obj.year if d_obj else None

            v_dict = sl.get("venue") or {}
            v_id = (v_dict.get("id") or "").strip()
            v_name = (v_dict.get("name") or "").strip()
            city_dict = v_dict.get("city") or {}
            city_name = (city_dict.get("name") or "").strip()
            state_name = (city_dict.get("state") or city_dict.get("stateCode") or "").strip()
            country_dict = city_dict.get("country") or {}
            country_name = (country_dict.get("name") or "United States").strip()
            coords = city_dict.get("coords") or {}
            lat = coords.get("lat")
            lng = coords.get("long")

            venue_obj = None
            if v_name:
                v_low = v_name.lower()
                if v_low in venues_cache:
                    venue_obj = venues_cache[v_low]
                    if (venue_obj.latitude is None or venue_obj.longitude is None) and (lat is not None and lng is not None):
                        venue_obj.latitude = lat
                        venue_obj.longitude = lng
                        venue_obj.geocode_source = 'setlistfm'
                        venue_obj.save(update_fields=['latitude', 'longitude', 'geocode_source'])
                else:
                    venue_obj = Venue.objects.create(
                        id=v_id or None,
                        name=v_name,
                        city=city_name,
                        state=state_name,
                        country=country_name,
                        latitude=lat,
                        longitude=lng,
                        geocode_source='setlistfm' if (lat is not None and lng is not None) else 'unresolved'
                    )
                    venues_cache[v_low] = venue_obj

            sl_id = sl.get("id", "")
            sl_url = sl.get("url", "")

            # Check existing ConcertArtist
            existing_ca = None
            if sl_id:
                existing_ca = ConcertArtist.objects.filter(concert__user=user, setlistfm_id=sl_id).select_related('concert').first()
            if not existing_ca and d_obj and art_obj:
                existing_ca = ConcertArtist.objects.filter(concert__user=user, concert__date=d_obj, artist=art_obj).select_related('concert').first()

            tracks = parse_setlistfm_sets_to_tracks(sl)

            if existing_ca:
                if sl_id:
                    existing_ca.setlistfm_id = sl_id
                if sl_url:
                    existing_ca.setlist_url = sl_url
                if tracks:
                    existing_ca.has_setlist = True
                existing_ca.save(update_fields=['setlistfm_id', 'setlist_url', 'has_setlist'])
                ca = existing_ca
            else:
                concert = Concert.objects.create(
                    user=user,
                    date=d_obj,
                    raw_date=raw_date,
                    year=year,
                    venue=venue_obj,
                    raw_venue=v_name,
                    raw_artists=art_obj.name,
                    source='setlistfm',
                    is_custom_offline=False
                )
                ca = ConcertArtist.objects.create(
                    concert=concert,
                    artist=art_obj,
                    billing_order=0,
                    setlistfm_id=sl_id,
                    setlist_url=sl_url,
                    has_setlist=bool(tracks)
                )
                imported_count += 1

            if tracks and not ca.songs.exists():
                total_tracks = len(tracks)
                cs_objs = []
                for idx, t in enumerate(tracks):
                    track_num = idx + 1
                    pct = round((track_num / total_tracks) * 100) if total_tracks > 0 else 100
                    if track_num == 1:
                        slot = "Opener"
                        slot_category = "opener"
                    elif t["is_encore"]:
                        slot = "Show Closer" if track_num == total_tracks else (f"Encore {t['encore_number']}" if t['encore_number'] else "Encore")
                        slot_category = "encore"
                    elif track_num == total_tracks:
                        slot = "Show Closer"
                        slot_category = "closer"
                    elif pct <= 35:
                        slot = "Early Set"
                        slot_category = "early"
                    elif pct <= 70:
                        slot = "Mid-Set"
                        slot_category = "mid"
                    else:
                        slot = "Late Set"
                        slot_category = "late"

                    clean_title_key = t["title"].lower().strip()
                    cache_k = (art_obj.id, clean_title_key)
                    if cache_k in songs_cache:
                        song_obj = songs_cache[cache_k]
                    else:
                        song_obj = Song.objects.filter(artist=art_obj, clean_title=clean_title_key).first()
                        if not song_obj:
                            song_obj = Song.objects.create(
                                artist=art_obj,
                                clean_title=clean_title_key,
                                title=t["title"],
                                is_cover=t["is_cover"],
                                original_artist=t["original_artist"] or None
                            )
                        songs_cache[cache_k] = song_obj

                    cs_objs.append(ConcertSong(
                        concert_artist=ca,
                        song=song_obj,
                        raw_song_name=t["title"],
                        set_name=t["set_name"],
                        is_encore=t["is_encore"],
                        encore_number=t["encore_number"],
                        track_num=track_num,
                        total_tracks=total_tracks,
                        pct_position=pct,
                        slot=slot,
                        slot_category=slot_category,
                        is_cover=t["is_cover"],
                        original_artist=t["original_artist"] or '',
                        info=t["info"] or ''
                    ))
                ConcertSong.objects.bulk_create(cs_objs)

    return imported_count


def sync_single_concert(concert, user=None, client=None, force_refresh=True) -> Dict[str, Any]:
    """
    Syncs a single concert with Setlist.fm without triggering a full database sync.
    Searches for matching setlists for the concert's artist(s) on the concert's date,
    attaching Setlist.fm IDs, setlist URLs, tracklists, venue geocoding, and running
    album/musician catalog enrichment. When force_refresh=True, bypasses and clears
    local setlist disk caches and replaces existing ConcertSong records with updated tracks.
    """
    from datetime import datetime
    from django.db import transaction
    from apps.catalog.models import Venue, Artist, Song, ApiCache
    from apps.concerts.models import Concert, ConcertArtist, ConcertSong
    from src.setlist_api import SetlistFMClient
    from src.gap_analysis import find_global_setlist_match, is_ignored_artist, match_score
    from src.album_enricher import AlbumEnricher
    from src.musician_enricher import MusicianEnricher
    from src.config import SETLISTFM_API_KEY, SETLIST_CACHE_DIR

    user = user or concert.user
    profile = getattr(user, 'profile', None)
    if not profile or not (profile.setlistfm_username or "").strip():
        return {"status": "skipped", "message": "No Setlist.fm username configured.", "matched": 0, "songs_added": 0}

    api_key = profile.setlistfm_api_key or SETLISTFM_API_KEY or "api_key_placeholder"
    if client is None:
        try:
            client = SetlistFMClient(api_key=api_key)
        except Exception as e:
            logger.warning("Could not initialize SetlistFMClient: %s", e)

    if not client:
        return {"status": "skipped", "message": "Setlist.fm client unavailable.", "matched": 0, "songs_added": 0}

    ignored_artists = profile.ignored_artist_names
    date_str = None
    if concert.date:
        date_str = concert.date.strftime("%d-%m-%Y")
    elif concert.raw_date:
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                dt = datetime.strptime(concert.raw_date.strip(), fmt)
                date_str = dt.strftime("%d-%m-%Y")
                break
            except ValueError:
                pass

    venue_name = concert.raw_venue or (concert.venue.name if concert.venue else "")
    matched_count = 0
    total_songs_added = 0
    new_songs_to_enrich = []

    ca_list = list(concert.artists.all().select_related('artist'))
    with transaction.atomic():
        for ca in ca_list:
            if not ca.artist:
                continue
            art_name = ca.artist.name
            if is_ignored_artist(art_name, ignored_artists):
                continue

            # Invalidate specific setlist file cache if ca already had a setlistfm_id and force_refresh is True
            if force_refresh and ca.setlistfm_id:
                try:
                    c_file = SETLIST_CACHE_DIR / f"{ca.setlistfm_id}.json"
                    if c_file.exists():
                        c_file.unlink(missing_ok=True)
                except Exception:
                    pass

            # If ca already has a setlistfm_id, try fetching fresh by ID first
            matched_sl = None
            if ca.setlistfm_id:
                try:
                    matched_sl = client.get_setlist_by_id(ca.setlistfm_id, use_cache=not force_refresh)
                except Exception as e:
                    logger.debug("get_setlist_by_id error for %s (%s): %s", art_name, ca.setlistfm_id, e)

            # First try user attended setlists if available / cached
            if not matched_sl:
                try:
                    user_attended = client.get_user_attended(profile.setlistfm_username.strip(), use_cache=not force_refresh)
                    for sl in user_attended:
                        sl_art = (sl.get("artist", {}).get("name") or "").strip()
                        sl_date = (sl.get("eventDate") or "").strip()
                        if date_str and sl_date == date_str:
                            sc = match_score(art_name, venue_name, sl)
                            if sc >= 60:
                                matched_sl = sl
                                break
                except Exception:
                    pass

            # If not found in user_attended, try global search
            if not matched_sl and date_str:
                try:
                    matched_sl = find_global_setlist_match(client, art_name, date_str, concert.year, venue_name)
                except Exception as e:
                    logger.debug("find_global_setlist_match error for %s on %s: %s", art_name, date_str, e)

            if matched_sl:
                matched_count += 1
                sl_id = matched_sl.get("id", "")
                sl_url = matched_sl.get("url", "")
                if sl_id:
                    ca.setlistfm_id = sl_id
                if sl_url:
                    ca.setlist_url = sl_url

                tracks = parse_setlistfm_sets_to_tracks(matched_sl)
                if tracks:
                    ca.has_setlist = True
                    # If force_refresh is enabled, delete existing tracks for this concert artist to ingest corrections
                    if force_refresh:
                        ca.songs.all().delete()

                    # If concert artist has no songs (or just deleted for refresh), populate them
                    if not ca.songs.exists():
                        total_tracks = len(tracks)
                        cs_objs = []
                        for idx, t in enumerate(tracks):
                            track_num = idx + 1
                            pct = round((track_num / total_tracks) * 100) if total_tracks > 0 else 100
                            if track_num == 1:
                                slot = "Opener"
                                slot_category = "opener"
                            elif t["is_encore"]:
                                slot = "Show Closer" if track_num == total_tracks else (f"Encore {t['encore_number']}" if t['encore_number'] else "Encore")
                                slot_category = "encore"
                            elif track_num == total_tracks:
                                slot = "Show Closer"
                                slot_category = "closer"
                            elif pct <= 35:
                                slot = "Early Set"
                                slot_category = "early"
                            elif pct <= 70:
                                slot = "Mid-Set"
                                slot_category = "mid"
                            else:
                                slot = "Late Set"
                                slot_category = "late"

                            clean_title_key = t["title"].lower().strip()
                            song_obj = Song.objects.filter(artist=ca.artist, clean_title=clean_title_key).first()
                            if not song_obj:
                                song_obj = Song.objects.create(
                                    artist=ca.artist,
                                    clean_title=clean_title_key,
                                    title=t["title"],
                                    is_cover=t["is_cover"],
                                    original_artist=t["original_artist"] or None
                                )

                            cs_objs.append(ConcertSong(
                                concert_artist=ca,
                                song=song_obj,
                                raw_song_name=t["title"],
                                set_name=t["set_name"],
                                is_encore=t["is_encore"],
                                encore_number=t["encore_number"],
                                track_num=track_num,
                                total_tracks=total_tracks,
                                pct_position=pct,
                                slot=slot,
                                slot_category=slot_category,
                                is_cover=t["is_cover"],
                                original_artist=t["original_artist"] or '',
                                info=t["info"] or ''
                            ))
                            new_songs_to_enrich.append((ca.artist.name, t["title"]))
                        ConcertSong.objects.bulk_create(cs_objs)
                        total_songs_added += len(cs_objs)

                ca.save(update_fields=['setlistfm_id', 'setlist_url', 'has_setlist'])

                # Venue geolocation update from Setlist.fm venue coords if missing
                if concert.venue and (concert.venue.latitude is None or concert.venue.longitude is None):
                    v_dict = matched_sl.get("venue") or {}
                    city_dict = v_dict.get("city") or {}
                    coords = city_dict.get("coords") or {}
                    lat = coords.get("lat")
                    lng = coords.get("long")
                    if lat is not None and lng is not None:
                        concert.venue.latitude = lat
                        concert.venue.longitude = lng
                        concert.venue.geocode_source = 'setlistfm'
                        concert.venue.save(update_fields=['latitude', 'longitude', 'geocode_source'])

    # Catalog enrichment for new songs & musicians
    if new_songs_to_enrich:
        try:
            enricher = AlbumEnricher()
            enrich_results = enricher.enrich_catalog(
                [{"artist": a, "song": s} for a, s in new_songs_to_enrich],
                refresh_unresolved=True
            )
            # Update Song / Album database models if enriched
            if enrich_results:
                from apps.catalog.models import Album
                for (art_name, s_name) in new_songs_to_enrich:
                    key = f"{art_name}_{s_name}".lower()
                    info = enrich_results.get(key)
                    if not info:
                        continue
                    clean_s_key = s_name.lower().strip()
                    song_obj = Song.objects.filter(artist__name__iexact=art_name, clean_title=clean_s_key).first()
                    if song_obj:
                        alb_title = info.get("album")
                        rel_year = info.get("release_year")
                        if alb_title and alb_title != "Non-Album / Singles":
                            clean_alb_key = alb_title.lower().strip()
                            album_obj, _ = Album.objects.get_or_create(
                                artist=song_obj.artist,
                                clean_title=clean_alb_key,
                                defaults={
                                    'title': alb_title,
                                    'release_year': rel_year
                                }
                            )
                            if rel_year and not album_obj.release_year:
                                album_obj.release_year = rel_year
                                album_obj.save(update_fields=['release_year'])
                            song_obj.album = album_obj
                        if rel_year and not song_obj.release_year:
                            song_obj.release_year = rel_year
                        song_obj.save()
        except Exception as e:
            logger.warning("Error during album enrichment in sync_single_concert: %s", e)

    current_year = datetime.now().year
    is_upcoming_or_current = bool(concert.date and concert.date.year >= current_year)
    for ca in ca_list:
        if ca.artist:
            try:
                m_enricher = MusicianEnricher()
                if not ca.artist.members.exists():
                    m_enricher.enrich_artist(ca.artist.name, artist_obj=ca.artist)
                elif is_upcoming_or_current:
                    m_enricher.enrich_artist(ca.artist.name, artist_obj=ca.artist, refresh=True)
            except Exception:
                pass

    # Clear user cache
    ApiCache.objects.filter(cache_key=f"user_dashboard_bundle_{user.id}").delete()

    return {
        "status": "success",
        "matched": matched_count,
        "songs_added": total_songs_added,
        "message": f"Concert synced with Setlist.fm: {matched_count} artist setlist(s) matched, {total_songs_added} songs linked."
    }


