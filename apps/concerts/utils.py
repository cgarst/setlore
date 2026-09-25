import re
import urllib.request
import urllib.parse
import json
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

def parse_setlist_text(text: str) -> List[Dict[str, Any]]:
    """
    Parses a user-pasted setlist string into a structured list of track dictionaries.
    Supports set headers (e.g., 'Set 1:', 'Encore:'), numbered tracks, and cover tags.
    """
    if not text or not text.strip():
        return []

    lines = [line.strip() for line in text.strip().splitlines()]
    tracks = []

    current_set_name = "Main Set"
    is_encore = False
    encore_number = None
    encore_count = 0
    main_set_count = 1

    # Regex patterns
    encore_regex = re.compile(r'^(?:[-*#=_\s]*)?encore(?:\s*(\d+))?(?:[-*#=_\s:]*)$', re.IGNORECASE)
    set_regex = re.compile(r'^(?:[-*#=_\s]*)?set\s*(\d+)(?:[-*#=_\s:]*)$', re.IGNORECASE)
    custom_set_regex = re.compile(r'^(?:[-*#=_\s]*)?(acoustic set|b-sides set|orchestral set|stage \w+)(?:[-*#=_\s:]*)$', re.IGNORECASE)
    divider_regex = re.compile(r'^[-=*_~]{3,}$')
    leading_number_regex = re.compile(r'^\s*(?:\d+[\.\)\-:]\s*|\#\d+\s*)')

    # Cover detection: (Artist cover), (Cover - Artist), (cover of Artist), (Cover)
    cover_regex = re.compile(
        r'[\(\[]\s*(?:cover\s*(?:of|-|by)?\s*([^\)\]]+)|([^\)\]]+?)\s+cover)\s*[\)\]]',
        re.IGNORECASE
    )
    generic_cover_regex = re.compile(r'[\(\[]\s*cover\s*[\)\]]', re.IGNORECASE)

    for line in lines:
        if not line or divider_regex.match(line):
            continue

        # Check for Encore header
        enc_match = encore_regex.match(line)
        if enc_match:
            is_encore = True
            encore_count += 1
            num_str = enc_match.group(1)
            encore_number = int(num_str) if num_str else encore_count
            current_set_name = f"Encore {encore_number}" if encore_number > 1 else "Encore"
            continue

        # Check for Set header
        set_match = set_regex.match(line)
        if set_match:
            is_encore = False
            encore_number = None
            set_num = int(set_match.group(1))
            main_set_count = max(main_set_count, set_num)
            current_set_name = f"Set {set_num}"
            continue

        # Check for Custom Set header (e.g. Acoustic Set)
        custom_set_match = custom_set_regex.match(line)
        if custom_set_match:
            is_encore = False
            encore_number = None
            current_set_name = custom_set_match.group(1).title()
            continue

        # Process as a song track line
        raw_song_line = leading_number_regex.sub('', line).strip()
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
            # If not part of a standard song title
            if any(k in possible_info.lower() for k in ["acoustic", "guest", "intro", "extended", "snippet", "jam", "instrumental"]):
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

def resolve_venue_coordinates(venue_name: str, city: str = "", state: str = "", country: str = "United States") -> Tuple[Optional[float], Optional[float], str]:
    """
    Attempts to resolve latitude and longitude for a venue.
    1. Checks the known static coordinates table.
    2. Falls back to OpenStreetMap Nominatim geocoding if city/state provided.
    """
    from src.venue_mapper import VENUE_COORDINATES

    v_key = venue_name.strip().lower()
    if v_key in VENUE_COORDINATES:
        lat, lng, _, _ = VENUE_COORDINATES[v_key]
        return lat, lng, "canonical_lookup"

    # Query Nominatim with strict 2.5s timeout
    query_parts = [p.strip() for p in [venue_name, city, state, country] if p and p.strip()]
    if len(query_parts) >= 2:
        query_str = ", ".join(query_parts)
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query_str)}&format=json&limit=1"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "concert-trakr/1.0 (concert analytics app)"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    if data and len(data) > 0:
                        lat = float(data[0]['lat'])
                        lon = float(data[0]['lon'])
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
