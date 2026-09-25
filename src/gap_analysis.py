from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from rapidfuzz import fuzz
from src.setlist_api import SetlistFMClient
from src.config import IGNORED_ARTISTS

COMMON_VENUE_WORDS = {"club", "theater", "theatre", "hall", "arena", "center", "centre", "stage", "amphitheater", "amphitheatre", "pavilion", "park", "auditorium", "stadium"}

def normalize_name(s: str) -> str:
    if not s:
        return ""
    s = s.lower().strip()
    for ch in [".", ",", "!", "?", "'", '"', "-", "(", ")", "/", ":"]:
        s = s.replace(ch, " ")
    return " ".join(s.split())

def clean_venue_keywords(s: str) -> str:
    words = normalize_name(s).split()
    filtered = [w for w in words if w not in COMMON_VENUE_WORDS]
    return " ".join(filtered) if filtered else " ".join(words)

def is_ignored_artist(name: str, ignored_list: List[str]) -> bool:
    if not name:
        return False
    norm = name.strip().lower()
    for ign in ignored_list:
        if ign.strip().lower() == norm or ign.strip().lower() in norm:
            return True
    return False

def format_setlist_date_us(event_date_str: str) -> str:
    if not event_date_str:
        return ""
    parts = event_date_str.split("-")
    if len(parts) == 3:
        d, m, y = parts
        return f"{m}-{d}-{y}"
    return event_date_str

def match_score(artist_candidate: str, venue_csv: str, setlist_obj: Dict[str, Any], require_venue: bool = False) -> float:
    sl_artist = normalize_name(setlist_obj.get("artist", {}).get("name", ""))
    norm_artist = normalize_name(artist_candidate)

    # Special handling for band "3" / "Three"
    if (norm_artist == "three" and sl_artist == "3") or (norm_artist == "3" and sl_artist == "three"):
        artist_score = 100.0
    elif norm_artist in ["three", "3"] and sl_artist not in ["3", "three"]:
        artist_score = 0.0
    elif "three 6 mafia" in sl_artist and norm_artist != "three 6 mafia":
        artist_score = 0.0
    else:
        artist_score = fuzz.ratio(norm_artist, sl_artist)
        if len(norm_artist) > 4 and (norm_artist in sl_artist or sl_artist in norm_artist):
            artist_score = max(artist_score, 88.0)

    # If artist name does not match sufficiently, it must NEVER match regardless of venue/date
    if artist_score < 60.0:
        return 0.0

    # Clean venue names by stripping generic words like "club", "theater" to avoid false partial matches
    cleaned_csv_venue = clean_venue_keywords(venue_csv)
    cleaned_sl_venue = clean_venue_keywords(setlist_obj.get("venue", {}).get("name", ""))
    cleaned_sl_city = clean_venue_keywords(setlist_obj.get("venue", {}).get("city", {}).get("name", ""))

    venue_name_score = fuzz.ratio(cleaned_csv_venue, cleaned_sl_venue)
    # Check partial if long enough
    if len(cleaned_csv_venue) > 3:
        venue_name_score = max(venue_name_score, fuzz.partial_ratio(cleaned_csv_venue, cleaned_sl_venue))
    
    city_score = fuzz.ratio(cleaned_csv_venue, cleaned_sl_city)
    venue_score = max(venue_name_score, city_score)

    if require_venue and venue_score < 60:
        return 0.0

    return (artist_score * 0.60) + (venue_score * 0.40)

def find_global_setlist_match(client: SetlistFMClient, artist: str, date_str: Optional[str],
                               year: Optional[int], venue: str) -> Optional[Dict[str, Any]]:
    # Global search strictly by matching event date
    search_terms = [artist]
    if artist.strip().lower() == "three":
        search_terms.append("3")
    elif artist.strip() == "3":
        search_terms.append("Three")

    best_match = None
    # Convert date string to Setlist.fm's required DD-MM-YYYY format
    sl_date_str = None
    if date_str:
        parts = date_str.split("-")
        if len(parts) == 3:
            if len(parts[0]) == 4:  # YYYY-MM-DD
                y, m, d = parts
                sl_date_str = f"{d}-{m}-{y}"
            elif len(parts[2]) == 4:  # MM-DD-YYYY
                m, d, y = parts
                sl_date_str = f"{d}-{m}-{y}"
            else:
                sl_date_str = date_str
        else:
            sl_date_str = date_str

    if sl_date_str:
        for term in search_terms:
            res = client.search_setlists(artist_name=term, date_str=sl_date_str)
            for s in res:
                s_date = s.get("eventDate", "")
                if s_date != sl_date_str:
                    continue
                score = match_score(artist, venue, s, require_venue=True)
                if score >= 60 and score > best_score:
                    best_score = score
                    best_match = s

    return best_match

def reconcile_history(csv_records: List[Dict[str, Any]], user_attended_setlists: List[Dict[str, Any]],
                      client: Optional[SetlistFMClient] = None,
                      ignored_artists: Optional[List[str]] = None) -> Dict[str, Any]:
    if ignored_artists is None:
        ignored_artists = IGNORED_ARTISTS

    user_attended_filtered = [
        s for s in user_attended_setlists
        if not is_ignored_artist(s.get("artist", {}).get("name", ""), ignored_artists)
    ]

    sl_by_date = {}
    for s in user_attended_filtered:
        d = s.get("eventDate")
        if d:
            sl_by_date.setdefault(d, []).append(s)

    matched_pairs = []
    matched_setlist_ids = set()
    csv_status = {}

    for csv_rec in csv_records:
        csv_id = csv_rec["id"]
        csv_date = csv_rec.get("date")
        artists = [a for a in csv_rec.get("artists", []) if not is_ignored_artist(a, ignored_artists)]
        venue = csv_rec.get("venue", "")

        date_candidates = sl_by_date.get(csv_date, [])
        matched_bands = []
        missing_bands_info = []
        matched_sl_for_row = []
        used_sl_ids_in_row = set()

        for art in artists:
            best_sl = None
            best_score = 0.0
            norm_art = normalize_name(art)
            for sl in date_candidates:
                sl_art_name = normalize_name(sl.get("artist", {}).get("name", ""))
                # If setlist was already matched to another artist in this row, only allow reuse if it's a joint/collaborative billing
                if sl.get("id") in used_sl_ids_in_row:
                    if len(norm_art) > 3 and norm_art in sl_art_name:
                        pass  # Collaborative band containing this member/artist
                    else:
                        continue

                score = match_score(art, venue, sl)
                if score >= 65 and score > best_score:
                    best_score = score
                    best_sl = sl

            if best_sl:
                used_sl_ids_in_row.add(best_sl.get("id"))
                matched_bands.append(art)
                matched_sl_for_row.append(best_sl)
                matched_setlist_ids.add(best_sl.get("id"))
                matched_pairs.append({
                    "csv": csv_rec,
                    "artist": art,
                    "setlist": best_sl,
                    "score": best_score
                })
            else:
                global_sl = None
                is_offline = csv_rec.get("is_custom_offline", False)
                if client and not is_offline:
                    global_sl = find_global_setlist_match(client, art, csv_date, csv_rec.get("year"), venue)

                if global_sl:
                    missing_bands_info.append({
                        "artist": art,
                        "status": "exists_unattended",
                        "status_label": "Exists on Setlist.fm (Not Tagged)",
                        "url": global_sl.get("url"),
                        "global_venue": global_sl.get("venue", {}).get("name", ""),
                        "global_date": format_setlist_date_us(global_sl.get("eventDate", ""))
                    })
                elif is_offline:
                    missing_bands_info.append({
                        "artist": art,
                        "status": "custom_offline",
                        "status_label": "Offline / Custom Show (Unlisted)",
                        "url": None,
                        "search_url": f"https://www.setlist.fm/search?query={art}+{venue}"
                    })
                else:
                    missing_bands_info.append({
                        "artist": art,
                        "status": "missing_from_setlistfm",
                        "status_label": "Missing from Setlist.fm Entirely",
                        "url": None,
                        "search_url": f"https://www.setlist.fm/search?query={art}+{venue}"
                    })

        csv_status[csv_id] = {
            "record": csv_rec,
            "matched_bands": matched_bands,
            "missing_bands_info": missing_bands_info,
            "missing_bands": [b["artist"] for b in missing_bands_info],
            "has_exists": any(b.get("status") == "exists_unattended" for b in missing_bands_info),
            "has_missing": any(b.get("status") == "missing_from_setlistfm" for b in missing_bands_info),
            "is_custom_offline": csv_rec.get("is_custom_offline", False),
            "setlists": matched_sl_for_row,
            "is_fully_matched": len(missing_bands_info) == 0 and len(matched_bands) > 0,
            "is_partially_matched": len(matched_bands) > 0 and len(missing_bands_info) > 0,
            "is_unmatched": len(matched_bands) == 0
        }

    csv_missing_or_partial = []
    offline_shows = []
    for csv_id, st in csv_status.items():
        if st.get("is_custom_offline") and not st.get("has_exists"):
            offline_shows.append(st)
        elif not st["is_fully_matched"]:
            csv_missing_or_partial.append(st)

    setlist_only = []
    for s in user_attended_filtered:
        if s.get("id") not in matched_setlist_ids:
            s_copy = dict(s)
            s_copy["display_date"] = format_setlist_date_us(s.get("eventDate", ""))
            setlist_only.append(s_copy)

    fully_matched_csv_count = sum(1 for st in csv_status.values() if st["is_fully_matched"] or st["is_partially_matched"] or st.get("is_custom_offline"))

    return {
        "matched": matched_pairs,
        "csv_status": csv_status,
        "csv_only": [st["record"] for st in csv_status.values() if st["is_unmatched"] and not st.get("is_custom_offline")],
        "csv_missing_or_partial": csv_missing_or_partial,
        "offline_shows": offline_shows,
        "setlist_only": setlist_only,
        "total_csv": len(csv_records),
        "total_setlist_user": len(user_attended_filtered),
        "matched_count": fully_matched_csv_count,
        "coverage_percentage": round((fully_matched_csv_count / len(csv_records) * 100), 1) if csv_records else 0
    }
