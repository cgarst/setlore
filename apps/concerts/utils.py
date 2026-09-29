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
                headers={"User-Agent": "concert-trakr/1.0 (concert analytics app; admin@localhost)"}
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
