import json
import re
import time
import urllib.parse
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import requests
from rapidfuzz import fuzz
from src.config import MB_CACHE_DIR

def clean_album_title(title: str) -> str:
    """Normalizes album title by removing edition/remaster tags and standardizing casing."""
    if not title:
        return ""
    # Strip edition suffixes like (Special Edition), [Remastered], etc.
    cleaned = re.sub(
        r'\s*[\(\[](?:special edition|deluxe edition|deluxe|expanded edition|expanded|remastered|remaster|bonus track[s]? edition|bonus tracks|\d+th anniversary edition|anniversary edition|explicit|clean|tour edition|limited edition)[\)\]]',
        '',
        title,
        flags=re.IGNORECASE
    ).strip()
    
    # Fix known casing duplicates
    if cleaned.lower() == "metropolis, pt. 2: scenes from a memory":
        return "Metropolis, Pt. 2: Scenes from a Memory"
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
        "studio albums 1992", "the story so far"
    ]
    return any(b in t for b in blacklisted)

class AlbumEnricher:
    """
    Enriches track metadata with original studio album name and release year
    by querying MusicBrainz with strict studio release filtering, edition normalization,
    and thread-safe disk caching.
    """
    def __init__(self):
        self.headers = {
            "User-Agent": "ConcertAnalyticsApp/1.0 ( cgarst@gmail.com )",
            "Accept": "application/json"
        }
        self._last_req_time = 0.0
        self._req_lock = threading.Lock()

    def _rate_limited_get(self, url: str) -> Optional[requests.Response]:
        with self._req_lock:
            elapsed = time.time() - self._last_req_time
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            self._last_req_time = time.time()
        try:
            r = requests.get(url, headers=self.headers, timeout=8)
            return r
        except Exception:
            return None

    def _generate_query_variations(self, song_name: str) -> List[str]:
        queries = [song_name]
        # Medleys: take first song
        if "/" in song_name:
            parts = [p.strip() for p in song_name.split("/") if p.strip()]
            if parts and parts[0] not in queries:
                queries.append(parts[0])
        # Suite / Act / Roman numeral variations
        if ":" in song_name:
            parts = [p.strip() for p in song_name.split(":") if p.strip()]
            last_part = parts[-1]
            cleaned_last = re.sub(r'^[IVXLCDM]+\.?\s*', '', last_part).strip()
            if cleaned_last and cleaned_last not in queries:
                queries.append(cleaned_last)
            if "A Change of Seasons" in song_name and "A Change of Seasons" not in queries:
                queries.append("A Change of Seasons")
            if "Six Degrees of Inner Turbulence" in song_name and "Six Degrees of Inner Turbulence" not in queries:
                queries.append("Six Degrees of Inner Turbulence")
        return queries

    def _query_musicbrainz_studio_album(self, artist_name: str, song_name: str) -> Tuple[Optional[str], Optional[int]]:
        queries = self._generate_query_variations(song_name)

        for q_song in queries:
            q_str = f'recording:"{q_song}" AND artist:"{artist_name}"'
            url = f'https://musicbrainz.org/ws/2/recording?query={urllib.parse.quote(q_str)}&limit=60&fmt=json'
            
            resp = self._rate_limited_get(url)
            if not resp or resp.status_code != 200:
                continue
            try:
                data = resp.json()
            except Exception:
                continue

            album_years: Dict[str, int] = {}
            candidates = []

            for rec in data.get("recordings", []):
                rec_title = rec.get("title", "")
                if fuzz.ratio(rec_title.lower(), q_song.lower()) < 70 and not any(q.lower() in rec_title.lower() for q in queries):
                    continue
                if any(kw in rec_title.lower() for kw in ["demo", "live", "instrumental demo", "bootleg"]):
                    continue

                for rel in rec.get("releases", []):
                    rg = rel.get("release-group", {})
                    primary = rg.get("primary-type")
                    sec_types = rg.get("secondary-types") or []

                    if any(t.lower() in ["live", "demo", "compilation", "remix", "soundtrack", "dj-mix"] for t in sec_types):
                        continue

                    raw_title = rg.get("title") or rel.get("title") or ""
                    if not raw_title or is_blacklisted_album(raw_title):
                        continue

                    title = clean_album_title(raw_title)
                    date_str = rel.get("date", "")
                    yr = int(date_str[:4]) if date_str and date_str[:4].isdigit() else None
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

                    candidates.append((score, title))

            if candidates:
                scored = []
                for sc, title in candidates:
                    yr = album_years.get(title)
                    final_sc = sc
                    if yr and yr < 9999:
                        final_sc += max(0, 2030 - yr)
                    scored.append((final_sc, yr if yr and yr < 9999 else None, title))
                scored.sort(key=lambda x: x[0], reverse=True)
                best = scored[0]
                return best[2], best[1]

        return None, None

    def _is_valid_cache_entry(self, cached: Dict[str, Any], refresh_unresolved: bool = False) -> bool:
        """Returns False if cached entry has bad attributions or needs unresolved refresh."""
        if not cached:
            return False
        album = cached.get("album")
        if not album:
            return False
        if is_blacklisted_album(album):
            return False
        # Check if contains unclean edition tags
        if re.search(r'[\(\[](?:special edition|deluxe edition|remastered|bonus tracks)[\)\]]', album, re.IGNORECASE):
            return False
        # Check casing consistency
        if album == "Metropolis, Pt. 2: Scenes From a Memory":
            return False
        # If user explicitly requested refresh of unresolved tracks
        if refresh_unresolved and album == "Non-Album / Singles":
            return False
        return True

    def get_track_info(self, artist_name: str, song_name: str, song_obj: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
                result["original_artist"] = song_obj.get("cover", {}).get("name")
            if song_obj.get("with"):
                result["with_guest"] = song_obj.get("with", {}).get("name")
            if song_obj.get("info"):
                result["info"] = song_obj.get("info")

        # Cache key
        cache_key = "".join(c if c.isalnum() else "_" for c in f"{artist_name}_{song_name}".lower())
        cache_file = MB_CACHE_DIR / f"{cache_key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if self._is_valid_cache_entry(cached):
                        result.update(cached)
                        # Ensure cleaned title
                        result["album"] = clean_album_title(result.get("album", "Non-Album / Singles"))
                        return result
            except Exception:
                pass

        # Query MusicBrainz canonical studio database
        album_name, release_yr = self._query_musicbrainz_studio_album(artist_name, song_name)

        if album_name:
            result["album"] = clean_album_title(album_name)
            result["release_year"] = release_yr

        result["resolved"] = True

        # Save to disk cache
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except Exception:
            pass

        return result

    def enrich_catalog(self, songs_list: list, max_workers: int = 1, refresh_unresolved: bool = False, refresh_all: bool = False) -> Dict[str, Any]:
        """
        Enriches a list of song records with album info, deduplicating unique artist-song
        pairs, pre-loading from disk cache, and fetching uncached tracks with live progress reporting.
        """
        import sys

        # Deduplicate
        unique_pairs = {}
        for item in songs_list:
            art = item.get("artist")
            song = item.get("song")
            if not art or not song:
                continue
            key = f"{art}_{song}".lower()
            if key not in unique_pairs:
                unique_pairs[key] = (art, song)

        total_unique = len(unique_pairs)
        results: Dict[str, Any] = {}
        uncached = []

        # 1. Quick local cache check with validity verification
        for key, (art, song) in unique_pairs.items():
            if refresh_all:
                uncached.append((key, art, song))
                continue

            cache_key = "".join(c if c.isalnum() else "_" for c in key)
            cache_file = MB_CACHE_DIR / f"{cache_key}.json"
            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                        if self._is_valid_cache_entry(cached, refresh_unresolved=refresh_unresolved):
                            cached["album"] = clean_album_title(cached.get("album", "Non-Album / Singles"))
                            results[key] = cached
                            continue
                except Exception:
                    pass
            uncached.append((key, art, song))

        cached_count = len(results)
        print(f"      ⚡ Album cache: {cached_count}/{total_unique} songs loaded from disk cache.")

        if not uncached:
            print(f"      ✅ All {total_unique} songs ready (0 API calls needed).")
            return results

        print(f"      🌐 Fetching/refreshing studio albums for {len(uncached)} uncached/new songs...")

        completed = cached_count
        for key, art, song in uncached:
            try:
                info = self.get_track_info(art, song)
                results[key] = info
            except Exception:
                results[key] = {
                    "song": song,
                    "artist": art,
                    "album": "Non-Album / Singles",
                    "release_year": None,
                    "is_cover": False,
                    "original_artist": None
                }
            completed += 1
            disp = f"{art} - {song}"
            if len(disp) > 35:
                disp = disp[:32] + "..."
            pct = (completed / total_unique) * 100
            sys.stdout.write(f"\r      ⏳ [{completed}/{total_unique}] ({pct:4.1f}%) Resolving: {disp:<35} ")
            sys.stdout.flush()

        sys.stdout.write("\n")
        print(f"      ✅ Finished album enrichment: {total_unique} songs ready.")
        return results

