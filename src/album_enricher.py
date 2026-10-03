import os
import json
import re
import time
import urllib.parse
import threading
import unicodedata
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import requests
from rapidfuzz import fuzz
from src.config import MB_CACHE_DIR, CONTACT_EMAIL, APP_URL

def clean_track_title(title: str) -> str:
    """Normalizes track title by standardizing punctuation and correcting known typographical errors."""
    if not title:
        return ""
    cleaned = title.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-").replace("‐", "-").strip()
    cleaned = re.sub(r'\bThroug\b', 'Through', cleaned, flags=re.IGNORECASE)
    return cleaned

def clean_album_title(title: str) -> str:
    """Normalizes album title by removing edition/remaster tags and standardizing casing."""
    if not title:
        return ""
    # Normalize unicode quotes and dashes
    cleaned = title.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-").replace("‐", "-")
    cleaned = re.sub(
        r'\s*[\(\[](?:special edition|deluxe edition|deluxe box set|deluxe|box set|expanded edition|expanded|remastered|remaster|bonus track[s]? edition|bonus tracks|\d+th anniversary edition|anniversary edition|explicit|clean|tour edition|limited edition)[\)\]]',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()
    
    return cleaned

def is_blacklisted_album(title: str) -> bool:
    """Checks if an album title belongs to a compilation, greatest hits, live archive, or soundtrack."""
    if not title:
        return True
    t = title.lower()
    blacklisted = [
        "lost not forgotten", "archive", "bootleg", "official bootleg", "demos 20", "demos 19",
        "live at", "live in", "live & alive", "greatest hit", "greatest hits", "the best of",
        "best of", "anthology", "the collection", "singles collection", "gold", "platinum",
        "essential", "very best of", "motion picture", "soundtrack", "original album series",
        "studio albums 1992", "the story so far", "the album network", "tune up"
    ]
    return any(b in t for b in blacklisted)

SOLO_INTRO_PATTERNS = [
    # Solos of any instrument
    r'\b(?:bass|guitar|drum|drums|keyboard|keyboards|piano|vocal|vocals|percussion|violin|fiddle|synth|synthesizer|organ|acoustic\s+guitar|harmonica|clarinet|flute|trumpet|trombone|saxophone|sax|cello|harp|accordion|banjo|mandolin|horns?|lead\s+guitar|rhythm\s+guitar)\s+solo\b',
    r'\b(?:drum\s+duet|drums?\s+and\s+percussion|percussion\s+duet|guitar\s+duet|bass\s+duet)\b',
    r'\b(?:solo\s+(?:duet|duel|battle|medley|spotlight|break|interlude))\b',
    r'^\s*(?:solo|solos|drum\s+solo|bass\s+solo|guitar\s+solo|keyboard\s+solo|piano\s+solo|vocal\s+solo|sax\s+solo)\s*$',
    # Intros, outros, walk-ons, tapes, intermissions, tuning, soundchecks
    r'\b(?:intro|outro|tape|walk-on|walkon|walk\s+on|intermission|schmedley|tuning|soundcheck|intro\s+tape|outro\s+tape|intro\s+jam|outro\s+jam|jam\s+session|improv\s+jam|band\s+introductions?|crowd\s+noise|stage\s+banter)\b',
    r'\b(?:also\s+sprach\s+zarathustra)\b',
]
SOLO_INTRO_RE = re.compile('|'.join(SOLO_INTRO_PATTERNS), re.IGNORECASE)

def is_solo_or_intro_track(title: str) -> bool:
    """Checks if a track is a solo, intro, outro tape, intermission, or live jam that should not be queried against MusicBrainz."""
    if not title:
        return False
    return bool(SOLO_INTRO_RE.search(str(title).strip()))

# Empty dicts preserved for backwards-compatibility imports
CANONICAL_ALBUM_YEARS: Dict[Tuple[str, str], int] = {}
CANONICAL_TRACK_ALBUMS: Dict[Tuple[str, str], Tuple[str, int]] = {}

class AlbumEnricher:
    """
    Enriches track metadata dynamically with original studio album names and release years
    by querying MusicBrainz with studio release filtering, title variation handling,
    and thread-safe persistent disk caching.
    """
    def __init__(self, contact_email: Optional[str] = None):
        email = contact_email or CONTACT_EMAIL or "admin@localhost"
        url_part = f" {APP_URL};" if APP_URL else ""
        self.headers = {
            "User-Agent": f"SetloreConcertAnalytics/1.0 ({email};{url_part})",
            "Accept": "application/json"
        }
        self.session = requests.Session()
        self._last_req_time = 0.0
        self._req_lock = threading.Lock()

    def _rate_limited_get(self, url: str, max_retries: int = 4) -> Optional[requests.Response]:
        """Performs a rate-limited HTTP GET with exponential backoff on 429/503/network errors."""
        for attempt in range(max_retries):
            with self._req_lock:
                elapsed = time.time() - self._last_req_time
                if elapsed < 1.15:
                    time.sleep(1.15 - elapsed)
                self._last_req_time = time.time()
            try:
                r = self.session.get(url, headers=self.headers, timeout=12)
                if r.status_code == 200:
                    return r
                elif r.status_code in [429, 503]:
                    wait_s = 2 ** (attempt + 1)
                    time.sleep(wait_s)
                elif r.status_code == 404:
                    return None
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        return None

    def _generate_query_variations(self, song_name: str) -> List[str]:
        """Generates clean query strings for suites, subtitles, medleys, and roman numerals."""
        queries = [song_name.strip()]

        # Medleys / Slashes: take individual parts
        if "/" in song_name:
            for part in song_name.split("/"):
                p = part.strip()
                if p and p not in queries:
                    queries.append(p)

        # Suite / Act / Part / Colon variations
        if ":" in song_name or " - " in song_name or ", Pt" in song_name or ", Part" in song_name:
            # First major part (parent suite)
            first_part = re.split(r'[:\-]', song_name)[0].strip()
            if not re.match(r'^(?:act|scene)\s+[ivxlcdm0-9]+$', first_part, re.IGNORECASE):
                if first_part and len(first_part) > 2 and first_part not in queries:
                    queries.append(first_part)

            # Last part (specific movement/title)
            parts = re.split(r'[:\-]', song_name)
            last_part = parts[-1].strip()
            cleaned_last = re.sub(r'^[IVXLCDM0-9]+[\.\:\s\-]+', '', last_part).strip()
            if cleaned_last and len(cleaned_last) > 2 and cleaned_last not in queries:
                queries.append(cleaned_last)

            # Unicode accent normalization (e.g. Déjà Vu -> Deja Vu)
            normalized = unicodedata.normalize('NFKD', song_name).encode('ASCII', 'ignore').decode('utf-8')
            if normalized != song_name and normalized not in queries:
                queries.append(normalized)
            if cleaned_last:
                norm_last = unicodedata.normalize('NFKD', cleaned_last).encode('ASCII', 'ignore').decode('utf-8')
                if norm_last != cleaned_last and norm_last not in queries:
                    queries.append(norm_last)

        # Parenthetical variations: "Song (Remastered)" -> "Song"
        if "(" in song_name and ")" in song_name:
            no_parens = re.sub(r'\s*\([^)]*\)', '', song_name).strip()
            if no_parens and len(no_parens) > 2 and no_parens not in queries:
                queries.append(no_parens)

        return queries

    def _query_musicbrainz_studio_album(self, artist_name: str, song_name: str) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str]]:
        """Queries local disk dump if available, otherwise queries MusicBrainz API dynamically.
        Returns: (album_title, release_year, release_group_mbid, recording_mbid)
        """
        try:
            from src.musicbrainz_dump import MusicBrainzDumpManager
            mb_dump = MusicBrainzDumpManager.get_instance()
            if mb_dump.is_dump_available():
                local_album, local_yr = mb_dump.lookup_studio_album(artist_name, song_name)
                if local_album or local_yr:
                    rg_mbid = mb_dump.lookup_release_group_mbid(artist_name, local_album)
                    return local_album, local_yr, rg_mbid, None
                # Check variations locally in SQLite (instant)
                queries = self._generate_query_variations(song_name)
                for q_song in queries:
                    if q_song != song_name:
                        local_album, local_yr = mb_dump.lookup_studio_album(artist_name, q_song)
                        if local_album or local_yr:
                            rg_mbid = mb_dump.lookup_release_group_mbid(artist_name, local_album)
                            return local_album, local_yr, rg_mbid, None
                # Song was not found in local disk dump
                if not mb_dump.get_online_fallback():
                    return None, None, None, None
                # If online fallback is enabled, proceed to query live MusicBrainz API below
        except Exception:
            pass

        queries = self._generate_query_variations(song_name)

        for status_filter in [" AND status:official", ""]:
            candidates = []
            album_years: Dict[str, int] = {}

            for q_song in queries:
                clean_q = q_song.replace('"', '').strip()
                q_str = f'recording:"{clean_q}" AND artist:"{artist_name}"{status_filter}'
                url = f'https://musicbrainz.org/ws/2/recording?query={urllib.parse.quote(q_str)}&limit=100&fmt=json'

                resp = self._rate_limited_get(url)
                if not resp or resp.status_code != 200:
                    continue
                try:
                    data = resp.json()
                except Exception:
                    continue

                for rec in data.get("recordings", []):
                    rec_title = rec.get("title", "")
                    rec_disam = (rec.get("disambiguation") or "").lower()
                    rec_mbid = rec.get("id") or None
                    rec_lower = rec_title.lower()
                    clean_q_lower = clean_q.lower()

                    if fuzz.ratio(rec_lower, clean_q_lower) < 65 and not any(q.lower() in rec_lower for q in queries):
                        continue

                    # Filter out demo/live/bootleg recordings unless those terms are part of the song query
                    skip_rec = False
                    for kw in ["demo", "live", "instrumental demo", "bootleg"]:
                        if kw not in clean_q_lower and re.search(r'\b' + re.escape(kw) + r'\b', rec_lower):
                            skip_rec = True
                            break
                    if skip_rec:
                        continue

                    if any(re.search(r'\b' + re.escape(kw) + r'\b', rec_disam) for kw in ["live", "bootleg", "instrumental demo", "remix"]):
                        continue

                    for rel in rec.get("releases", []):
                        rg = rel.get("release-group", {})
                        primary = rg.get("primary-type")
                        sec_types = rg.get("secondary-types") or []
                        rg_mbid = rg.get("id") or None

                        if any(t.lower() in ["live", "demo", "compilation", "remix", "soundtrack", "dj-mix"] for t in sec_types):
                            continue

                        raw_title = rg.get("title") or rel.get("title") or ""
                        if not raw_title or is_blacklisted_album(raw_title):
                            continue

                        title = clean_album_title(raw_title)

                        # Extract earliest valid year across release-group, recording, and release dates
                        rg_date = rg.get("first-release-date", "")
                        rec_date = rec.get("first-release-date", "")
                        rel_date = rel.get("date", "")

                        years = []
                        for d_str in [rg_date, rec_date, rel_date]:
                            if d_str and len(d_str) >= 4 and d_str[:4].isdigit():
                                y = int(d_str[:4])
                                if 1950 <= y <= 2030:
                                    years.append(y)

                        yr = min(years) if years else None
                        if yr:
                            album_years[title] = min(album_years.get(title, 9999), yr)

                        score = 100
                        if primary == "Album":
                            score += 80
                        elif primary == "EP":
                            score += 40
                        if not sec_types:
                            score += 40
                        else:
                            score -= 50

                        candidates.append((score, title, rg_mbid, rec_mbid))

                if candidates:
                    break

            if candidates:
                scored = []
                for sc, title, rg_mbid, rec_mbid in candidates:
                    yr = album_years.get(title)
                    final_sc = sc
                    if yr and yr < 9999:
                        final_sc += max(0, 2030 - yr)
                    scored.append((final_sc, yr if yr and yr < 9999 else None, title, rg_mbid, rec_mbid))
                scored.sort(key=lambda x: x[0], reverse=True)
                best = scored[0]
                return best[2], best[1], best[3], best[4]

        return None, None, None, None

    def _query_song_from_db(self, artist_name: str, song_name: str) -> Optional[Dict[str, Any]]:
        """Queries the database Song and Album tables if Django is available."""
        try:
            from apps.catalog.models import Song
            from src.csv_parser import normalize_artist_name

            clean_song = clean_track_title(song_name).strip()
            norm_art = normalize_artist_name(artist_name).strip()

            song_obj = (
                Song.objects.filter(
                    artist__normalized_name=norm_art.lower(),
                    clean_title__iexact=clean_song
                ).select_related('artist', 'album').first()
                or Song.objects.filter(
                    artist__name__iexact=artist_name,
                    title__iexact=song_name
                ).select_related('artist', 'album').first()
            )

            if song_obj and song_obj.album:
                alb = song_obj.album
                rel_yr = song_obj.release_year or alb.release_year
                return {
                    "song": song_obj.title,
                    "artist": song_obj.artist.name,
                    "album": clean_album_title(alb.clean_title or alb.title),
                    "release_year": rel_yr,
                    "is_cover": song_obj.is_cover,
                    "original_artist": song_obj.original_artist,
                    "album_mbid": alb.mbid or (alb.id if not alb.is_custom_offline else None),
                    "mbid": song_obj.mbid or (song_obj.id if not song_obj.is_custom_offline else None),
                    "resolved": True
                }
        except Exception:
            pass
        return None

    def _is_valid_cache_entry(self, cached: Dict[str, Any], refresh_unresolved: bool = False) -> bool:
        """Returns False if cached entry has bad attributions or needs unresolved refresh."""
        if not cached:
            return False
        if cached.get("resolved") is False:
            return False
        album = cached.get("album")
        if not album:
            return False
        if is_blacklisted_album(album):
            return False
        if re.search(r'[\(\[](?:special edition|deluxe edition|remastered|bonus tracks|box set)[\)\]]', album, re.IGNORECASE):
            return False
        if refresh_unresolved and album == "Non-Album / Singles":
            return False
        return True

    def get_track_info(self, artist_name: str, song_name: str, song_obj: Optional[Dict[str, Any]] = None, refresh_unresolved: bool = False) -> Dict[str, Any]:
        """Retrieves track studio album information dynamically from database, cache, or MusicBrainz."""
        result = {
            "song": song_name,
            "artist": artist_name,
            "album": "Non-Album / Singles",
            "release_year": None,
            "is_cover": False,
            "original_artist": None,
            "resolved": True
        }

        if song_obj:
            if song_obj.get("cover"):
                result["is_cover"] = True
                result["original_artist"] = song_obj.get("cover", {}).get("name") if isinstance(song_obj.get("cover"), dict) else (song_obj.get("cover") or None)
            elif song_obj.get("cover_original"):
                result["is_cover"] = True
                result["original_artist"] = song_obj.get("cover_original")
            elif song_obj.get("is_cover"):
                result["is_cover"] = True
                result["original_artist"] = song_obj.get("original_artist")

            if song_obj.get("with"):
                result["with_guest"] = song_obj.get("with", {}).get("name") if isinstance(song_obj.get("with"), dict) else (song_obj.get("with") or None)
            if song_obj.get("info"):
                result["info"] = song_obj.get("info")

        # 1. Skip MusicBrainz query entirely for solos, audio clips, walk-ons, and non-song performances
        if is_solo_or_intro_track(song_name):
            result["album"] = "Non-Album / Singles"
            result["release_year"] = None
            result["resolved"] = True
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass
            return result

        # 2. Check database first unless refresh_unresolved is forced
        if not refresh_unresolved:
            db_res = self._query_song_from_db(artist_name, song_name)
            if db_res and self._is_valid_cache_entry(db_res, refresh_unresolved=refresh_unresolved):
                result.update(db_res)
                return result

        # 3. Check disk cache
        cache_key = "".join(c if c.isalnum() else "_" for c in f"{artist_name}_{song_name}".lower())
        cache_file = MB_CACHE_DIR / f"{cache_key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if self._is_valid_cache_entry(cached, refresh_unresolved=refresh_unresolved):
                        result.update(cached)
                        result["album"] = clean_album_title(result.get("album", "Non-Album / Singles"))
                        if song_obj and (song_obj.get("is_cover") or song_obj.get("cover_original") or song_obj.get("cover")):
                            result["is_cover"] = True
                            if not result.get("original_artist"):
                                result["original_artist"] = (
                                    song_obj.get("cover", {}).get("name") if isinstance(song_obj.get("cover"), dict)
                                    else (song_obj.get("cover_original") or song_obj.get("original_artist"))
                                )
                        return result
            except Exception:
                pass

        # 4. Query MusicBrainz canonical studio database dynamically
        # If the track is a cover with a known original artist, prioritize querying MusicBrainz under the original artist
        lookup_artist = result["original_artist"] if (result.get("is_cover") and result.get("original_artist")) else artist_name
        album_name, release_yr, alb_mbid, rec_mbid = self._query_musicbrainz_studio_album(lookup_artist, song_name)

        if not album_name and lookup_artist != artist_name:
            # Fallback to performing artist if original artist query yielded no studio album
            album_name, release_yr, alb_mbid, rec_mbid = self._query_musicbrainz_studio_album(artist_name, song_name)

        if album_name:
            result["album"] = clean_album_title(album_name)
            result["release_year"] = release_yr
            if alb_mbid:
                result["mbid"] = alb_mbid
                result["album_mbid"] = alb_mbid
                result["release_group_mbid"] = alb_mbid
            if rec_mbid:
                result["recording_mbid"] = rec_mbid
                result["recording_id"] = rec_mbid
            result["resolved"] = True
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass
        else:
            # Genuinely unresolved studio album
            result["resolved"] = False

        return result

    def get_release_group_mbid(self, artist_name: str, album_title: str) -> Optional[str]:
        """Resolves the canonical MusicBrainz Release Group MBID for an artist's album."""
        if not artist_name or not album_title:
            return None
        clean_alb = clean_album_title(album_title)
        if clean_alb in ["Covers", "Non-Album / Singles"] or clean_alb.lower() in ["covers", "non-album / singles"]:
            return None

        # 1. Check local dump if available
        try:
            from src.musicbrainz_dump import MusicBrainzDumpManager
            mb_dump = MusicBrainzDumpManager.get_instance()
            if mb_dump.is_dump_available():
                rg_mbid = mb_dump.lookup_release_group_mbid(artist_name, clean_alb)
                if rg_mbid:
                    return rg_mbid
        except Exception:
            pass

        # 2. Query MusicBrainz live release-group endpoint
        clean_q = clean_alb.replace('"', '').strip()
        q_str = f'releasegroup:"{clean_q}" AND artist:"{artist_name}"'
        url = f'https://musicbrainz.org/ws/2/release-group?query={urllib.parse.quote(q_str)}&limit=5&fmt=json'
        resp = self._rate_limited_get(url)
        if resp and resp.status_code == 200:
            try:
                data = resp.json()
                rgs = data.get("release-groups", [])
                if rgs:
                    # Prefer standard Album primary type
                    for rg in rgs:
                        if rg.get("primary-type") == "Album" and rg.get("id"):
                            return rg["id"]
                    if rgs[0].get("id"):
                        return rgs[0]["id"]
            except Exception:
                pass

        return None

    def enrich_catalog(self, songs_list: list, max_workers: int = 1,
                       refresh_unresolved: bool = False, refresh_all: bool = False,
                       progress_callback: Optional[Any] = None,
                       batch_save_callback: Optional[Any] = None,
                       cancel_check: Optional[Any] = None) -> Dict[str, Any]:
        """
        Enriches a list of song records with album info, deduplicating unique artist-song
        pairs, pre-loading from disk cache, and fetching uncached tracks with live progress reporting.
        """
        import sys

        results, uncached, total_unique = self.load_cached_catalog(
            songs_list, refresh_all=refresh_all, refresh_unresolved=refresh_unresolved
        )

        cached_count = len(results)
        print(f"      [CACHE] Album cache: {cached_count}/{total_unique} songs loaded from disk cache.")

        if not uncached:
            print(f"      [OK] All {total_unique} songs ready (0 API calls needed).")
            return results

        print(f"      [FETCH] Fetching/refreshing studio albums for {len(uncached)} uncached/new songs...")

        completed = cached_count
        for item in uncached:
            if len(item) == 4:
                key, art, song, s_obj = item
            else:
                key, art, song = item[:3]
                s_obj = None
            if cancel_check and cancel_check():
                print("\n      [CANCEL] Album enrichment cancelled.")
                break
            try:
                info = self.get_track_info(art, song, song_obj=s_obj, refresh_unresolved=refresh_unresolved)
                results[key] = info
            except Exception:
                results[key] = {
                    "song": song,
                    "artist": art,
                    "album": "Non-Album / Singles",
                    "release_year": None,
                    "is_cover": bool(s_obj and (s_obj.get("is_cover") or s_obj.get("cover_original") or s_obj.get("cover"))),
                    "original_artist": s_obj.get("cover", {}).get("name") if (s_obj and isinstance(s_obj.get("cover"), dict)) else (s_obj.get("cover_original") or s_obj.get("original_artist") if s_obj else None),
                    "resolved": False
                }
            completed += 1
            disp = f"{art} - {song}"
            if len(disp) > 35:
                disp = disp[:32] + "..."
            pct = (completed / total_unique) * 100
            sys.stdout.write(f"\r      [{completed}/{total_unique}] ({pct:4.1f}%) Resolving: {disp:<35} ")
            sys.stdout.flush()

            if progress_callback and (completed % 5 == 0 or completed == total_unique):
                try:
                    progress_callback(completed, total_unique, f"{art} - {song}")
                except Exception:
                    pass

            if batch_save_callback and completed % 25 == 0:
                try:
                    batch_save_callback(results)
                except Exception:
                    pass

        sys.stdout.write("\n")
        print(f"      [OK] Finished album enrichment: {total_unique} songs ready.")
        return results

    def load_cached_catalog(self, songs_list: List[Dict[str, Any]],
                            refresh_all: bool = False,
                            refresh_unresolved: bool = False) -> Tuple[Dict[str, Any], List[Tuple[str, str, str, Optional[Dict[str, Any]]]], int]:
        """Loads all existing cached album entries from database and disk without making any external API calls."""
        unique_pairs: Dict[str, Tuple[str, str, Optional[Dict[str, Any]]]] = {}
        for item in songs_list:
            if isinstance(item, dict):
                art = item.get("artist")
                song = item.get("song")
                song_obj = item
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                art = str(item[0])
                song = str(item[1])
                song_obj = None
            else:
                continue
            if not art or not song:
                continue
            key = f"{art}_{song}".lower()
            if key not in unique_pairs:
                unique_pairs[key] = (art, song, song_obj)
            elif song_obj and (song_obj.get("is_cover") or song_obj.get("cover_original") or song_obj.get("cover")):
                unique_pairs[key] = (art, song, song_obj)

        total_unique = len(unique_pairs)
        results: Dict[str, Any] = {}
        uncached = []

        # 1. Batch load from database if available and not refresh_all
        db_entries = {}
        if not refresh_all:
            try:
                from apps.catalog.models import Song
                from src.csv_parser import normalize_artist_name

                db_songs = Song.objects.filter(album__isnull=False).select_related('artist', 'album')
                for s_obj in db_songs:
                    art_name = s_obj.artist.name
                    song_title = s_obj.title
                    alb = s_obj.album
                    rel_yr = s_obj.release_year or alb.release_year
                    entry = {
                        "song": song_title,
                        "artist": art_name,
                        "album": clean_album_title(alb.clean_title or alb.title),
                        "release_year": rel_yr,
                        "is_cover": s_obj.is_cover,
                        "original_artist": s_obj.original_artist,
                        "album_mbid": alb.mbid or (alb.id if not alb.is_custom_offline else None),
                        "mbid": s_obj.mbid or (s_obj.id if not s_obj.is_custom_offline else None),
                        "resolved": True
                    }
                    k1 = f"{art_name}_{song_title}".lower()
                    k2 = f"{normalize_artist_name(art_name)}_{clean_track_title(song_title)}".lower()
                    db_entries[k1] = entry
                    db_entries[k2] = entry
            except Exception:
                pass

        for key, (art, song, song_obj) in unique_pairs.items():
            if is_solo_or_intro_track(song):
                results[key] = {
                    "song": song,
                    "artist": art,
                    "album": "Non-Album / Singles",
                    "release_year": None,
                    "is_cover": False,
                    "original_artist": None,
                    "resolved": True
                }
                continue

            if refresh_all:
                uncached.append((key, art, song, song_obj))
                continue

            # 1. Check database entry
            if key in db_entries:
                db_entry = db_entries[key]
                if self._is_valid_cache_entry(db_entry, refresh_unresolved=refresh_unresolved):
                    results[key] = db_entry
                    continue

            # 2. Check disk cache
            cache_key = "".join(c if c.isalnum() else "_" for c in key)
            cache_file = MB_CACHE_DIR / f"{cache_key}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                        if self._is_valid_cache_entry(cached, refresh_unresolved=refresh_unresolved):
                            cached["album"] = clean_album_title(cached.get("album", "Non-Album / Singles"))
                            if song_obj and (song_obj.get("is_cover") or song_obj.get("cover_original") or song_obj.get("cover")):
                                cached["is_cover"] = True
                                if not cached.get("original_artist"):
                                    cached["original_artist"] = (
                                        song_obj.get("cover", {}).get("name") if isinstance(song_obj.get("cover"), dict)
                                        else (song_obj.get("cover_original") or song_obj.get("original_artist"))
                                    )
                            results[key] = cached
                            continue
                except Exception:
                    pass
            uncached.append((key, art, song, song_obj))

        return results, uncached, total_unique

    def get_album_tracklist(self, artist_name: str, album_name: str) -> Dict[str, Any]:
        """
        Retrieves the canonical studio album tracklist with track numbers and titles,
        caching the result to disk for instant subsequent lookups.
        """
        if not artist_name or not album_name:
            return {"artist": artist_name, "album": album_name, "tracks": [], "release_year": None}

        clean_alb = clean_album_title(album_name)
        if clean_alb in ["Covers", "Non-Album / Singles"] or clean_alb.lower() in ["covers", "non-album / singles"]:
            return {"artist": artist_name, "album": clean_alb, "tracks": [], "release_year": None}

        tracklist_dir = MB_CACHE_DIR / "tracklists"
        tracklist_dir.mkdir(parents=True, exist_ok=True)
        cache_key = "".join(c if c.isalnum() else "_" for c in f"{artist_name}_{clean_alb}".lower())
        cache_file = tracklist_dir / f"{cache_key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if cached and isinstance(cached.get("tracks"), list) and len(cached["tracks"]) > 0:
                        return cached
            except Exception:
                pass

        # Query MusicBrainz release endpoint
        queries = [
            f'release:"{clean_alb}" AND artist:"{artist_name}" AND primarytype:Album',
            f'release:"{clean_alb}" AND artist:"{artist_name}"'
        ]

        tracks = []
        release_yr = None

        deluxe_keywords = [
            "deluxe", "expanded", "anniversary", "bonus", "special edition", 
            "remastered edition", "box set", "boxset", "super deluxe", "tour edition", 
            "collector", "complete", "instrumental", "5.1", "surround", "bluray", "blu-ray", "dvd"
        ]

        for q_str in queries:
            url = f'https://musicbrainz.org/ws/2/release?query={urllib.parse.quote(q_str)}&limit=10&fmt=json'
            resp = self._rate_limited_get(url)
            if not resp or resp.status_code != 200:
                continue

            try:
                data = resp.json()
            except Exception:
                continue

            releases = data.get("releases", [])
            if not releases:
                continue

            # Prioritize official standard studio album releases over deluxe/bonus/expanded editions
            candidates = []
            for r in releases:
                rg = r.get("release-group", {})
                p_type = rg.get("primary-type") or ""
                s_types = [s.lower() for s in rg.get("secondary-types") or []]
                status = r.get("status") or ""
                r_title = (r.get("title") or "").lower()
                r_disam = (r.get("disambiguation") or "").lower()
                rg_disam = (rg.get("disambiguation") or "").lower()

                if any(st in ["live", "demo", "remix", "soundtrack", "compilation"] for st in s_types):
                    continue

                score = 100
                if p_type == "Album":
                    score += 50
                if status == "Official":
                    score += 40

                # Check for deluxe/expanded/bonus signals in release title & disambiguation
                text_to_check = f"{r_title} {r_disam} {rg_disam}"
                for kw in deluxe_keywords:
                    if kw in text_to_check:
                        score -= 60

                # Prefer releases with earliest date (usually the original standard release)
                d_val = r.get("date") or rg.get("first-release-date") or ""
                candidates.append((score, d_val, r))

            # Sort candidate releases by score descending, then earliest date ascending
            candidates.sort(key=lambda x: (-x[0], x[1] if x[1] else "9999"))
            chosen_releases = [c[2] for c in candidates] if candidates else releases

            for chosen_rel in chosen_releases[:3]:
                rel_id = chosen_rel.get("id")
                if not rel_id:
                    continue

                # Extract release year
                d_str = chosen_rel.get("date") or chosen_rel.get("release-group", {}).get("first-release-date") or ""
                if d_str and len(d_str) >= 4 and d_str[:4].isdigit():
                    y = int(d_str[:4])
                    if 1950 <= y <= 2030:
                        release_yr = y

                # Fetch release with recordings
                rel_url = f'https://musicbrainz.org/ws/2/release/{rel_id}?inc=recordings&fmt=json'
                rel_resp = self._rate_limited_get(rel_url)
                if not rel_resp or rel_resp.status_code != 200:
                    continue

                try:
                    rel_data = rel_resp.json()
                except Exception:
                    continue

                media_list = rel_data.get("media", [])
                candidate_tracks = []
                running_track_num = 1

                for medium_idx, m in enumerate(media_list):
                    m_format = (m.get("format") or "").lower()
                    m_title = (m.get("title") or "").lower()

                    # Skip video media (DVD, Blu-ray, etc.) or bonus media
                    if any(vf in m_format for vf in ["dvd", "blu-ray", "bluray", "video", "vhd"]):
                        continue
                    if any(bk in m_title for bk in ["bonus", "demo", "live", "outtake", "instrumental", "5.1", "mix", "making of", "documentary"]):
                        continue

                    # If multiple discs and medium 2+ is not named or is marked disc 2, check if medium 1 was already a full album
                    # But keep multi-disc albums (e.g. The Wall, Tales from Topographic Oceans)
                    for trk in m.get("tracks", []):
                        t_title = trk.get("title") or (trk.get("recording") or {}).get("title")
                        if not t_title:
                            continue
                        # Skip bonus/live/demo tracks at the track level if marked in title
                        t_title_clean = t_title.strip()
                        t_lower = t_title_clean.lower()
                        if any(b_tag in t_lower for b_tag in ["(bonus track", "(bonus demo", "[bonus track", "(live at", "(live in", "(demo version", "(instrumental)"]):
                            continue

                        rec_id = (trk.get("recording") or {}).get("id") or trk.get("id") or None
                        candidate_tracks.append({
                            "track_number": running_track_num,
                            "title": clean_track_title(t_title_clean),
                            "mbid": rec_id
                        })
                        running_track_num += 1

                if candidate_tracks:
                    tracks = candidate_tracks
                    break

            if tracks:
                break

        result = {
            "artist": artist_name,
            "album": clean_alb,
            "release_year": release_yr,
            "mbid": rg_id if 'rg_id' in locals() else None,
            "tracks": tracks
        }

        if tracks:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass

        return result

