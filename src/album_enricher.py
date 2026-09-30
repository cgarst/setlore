import os
import json
import re
import time
import urllib.parse
import threading
import unicodedata
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
import requests
from rapidfuzz import fuzz
from src.config import MB_CACHE_DIR, CONTACT_EMAIL, APP_URL

def clean_album_title(title: str) -> str:
    """Normalizes album title by removing edition/remaster tags and standardizing casing."""
    if not title:
        return ""
    # Normalize unicode quotes and dashes
    cleaned = title.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-").replace("‐", "-")
    cleaned = re.sub(
        r'\s*[\(\[](?:(?:\d+th\s+)?anniversary\s+)?(?:super\s+deluxe|deluxe(?:\s+box\s+set)?|expanded|special|tour|limited)?\s*(?:edition|box\s+set|remastered|remaster|version|mix|\d{4}\s+remaster(?:ed)?(?:\s+version)?)[\)\]]',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()
    cleaned = re.sub(
        r'\s*[\(\[](?:bonus\s+track[s]?(?:\s+edition)?|explicit|clean|tour edition|limited edition)[\)\]]',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()
    
    # Fix known casing and name duplicates
    c_low = cleaned.lower()
    canonical_titles = {
        "metropolis, pt. 2: scenes from a memory": "Metropolis, Pt. 2: Scenes from a Memory",
        "metropolis pt. 2: scenes from a memory": "Metropolis, Pt. 2: Scenes from a Memory",
        "kill 'em all": "Kill 'Em All",
        "kill 'em all (remastered)": "Kill 'Em All",
        "reload": "Reload",
        "load": "Load",
        "hardwired... to self-destruct": "Hardwired... to Self-Destruct",
        "hardwired...to self-destruct": "Hardwired... to Self-Destruct",
        "...and justice for all": "...And Justice for All",
        "and justice for all": "...And Justice for All",
        "ride the lightning": "Ride the Lightning",
        "master of puppets": "Master of Puppets",
        "72 seasons": "72 Seasons",
        "garage inc.": "Garage Inc.",
        "death magnetic": "Death Magnetic",
        "st. anger": "St. Anger",
        # Dream Theater
        "when dream and day unite": "When Dream and Day Unite",
        "images and words": "Images and Words",
        "awake": "Awake",
        "a change of seasons": "A Change of Seasons",
        "falling into infinity": "Falling into Infinity",
        "six degrees of inner turbulence": "Six Degrees of Inner Turbulence",
        "train of thought": "Train of Thought",
        "octavarium": "Octavarium",
        "systematic chaos": "Systematic Chaos",
        "black clouds & silver linings": "Black Clouds & Silver linings",
        "a dramatic turn of events": "A Dramatic Turn of Events",
        "dream theater": "Dream Theater",
        "the astonishing": "The Astonishing",
        "distance over time": "Distance over Time",
        "a view from the top of the world": "A View from the Top of the World",
        "parasomnia": "Parasomnia",
        # Iron Maiden
        "the number of the beast": "The Number of the Beast",
        "piece of mind": "Piece of Mind",
        "seventh son of a seventh son": "Seventh Son of a Seventh Son",
        "somewhere in time": "Somewhere in Time",
        "fear of the dark": "Fear of the Dark",
        "the book of souls": "The Book of Souls",
        "the final frontier": "The Final Frontier",
        "a matter of life and death": "A Matter of Life and Death",
        "brave new world": "Brave New World",
        "dance of death": "Dance of Death",
        "the x factor": "The X Factor",
        "virtual xi": "Virtual XI",
        # Megadeth
        "rust in peace": "Rust in Peace",
        "peace sells... but who's buying?": "Peace Sells... But Who's Buying?",
        "countdown to extinction": "Countdown to Extinction",
        "youthanasia": "Youthanasia",
        "cryptic writings": "Cryptic Writings",
        "killing is my business... and business is good!": "Killing Is My Business... and Business Is Good!",
        "so far, so good... so what!": "So Far, So Good... So What!",
    }
    if c_low in canonical_titles:
        return canonical_titles[c_low]
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
        "studio albums 1992", "the story so far", "the album network", "tune up", "retrospective",
        "presents...", "various artists", "90's oldies", "classic rock hits"
    ]
    return any(b in t for b in blacklisted)

def is_known_non_album_track(song_name: str) -> bool:
    """Detects intros, solos, tuning, tape playback, audio clips, and medleys that don't belong to studio albums."""
    if not song_name:
        return True
    s_low = song_name.lower().strip()
    non_album_patterns = [
        r'\b(?:guitar|drum|bass|keyboard|piano|synth|sax|violin|flute|vocal)\s+solo\b',
        r'\b(?:drum\s*&\s*bass\s+solo|drum\s+duet|guitar\s+duel|drum\s+solo)\b',
        r'^(?:intro|outro|interlude|prelude|prologue|epilogue|tape|tuning|speech|banter|audience|encore|jam|intermission|applause|walk\s*on\s*music|soundcheck)$',
        r'\b(?:tape\s*intro|intro\s*tape|intro\s*video|outro\s*tape)\b',
        r'^(?:\[unknown\]|\(unknown\)|unknown|untitled|\?)$',
    ]
    for pat in non_album_patterns:
        if re.search(pat, s_low):
            return True
    return False

# Empty dicts preserved for backwards-compatibility imports
CANONICAL_ALBUM_YEARS: Dict[Tuple[str, str], int] = {}
CANONICAL_TRACK_ALBUMS: Dict[Tuple[str, str], Tuple[str, int]] = {}

class AlbumEnricher:
    """
    Enriches track metadata dynamically with original studio album names and release years
    by combining fast multi-track discovery, artist studio discography validation,
    and MusicBrainz canonical studio database querying with thread-safe disk caching.
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
        self._artist_studio_cache: Dict[str, Dict[str, Tuple[str, int]]] = {}
        
        # Ensure artists sub-cache directory exists
        self._artist_cache_dir = MB_CACHE_DIR / "artists"
        self._artist_cache_dir.mkdir(parents=True, exist_ok=True)

    def _rate_limited_get(self, url: str, max_retries: int = 4) -> Optional[requests.Response]:
        """Performs a rate-limited HTTP GET with exponential backoff on 429/503/network errors."""
        for attempt in range(max_retries):
            with self._req_lock:
                elapsed = time.time() - self._last_req_time
                if elapsed < 1.02:
                    time.sleep(1.02 - elapsed)
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

    def get_artist_studio_albums(self, artist_name: str) -> Dict[str, Tuple[str, int]]:
        """
        Retrieves the canonical list of studio albums and release years for an artist
        from disk cache or MusicBrainz release-groups.
        Returns a dict mapping { cleaned_album_title_lower: (canonical_title, release_year) }.
        """
        if not artist_name:
            return {}
        art_key = "".join(c if c.isalnum() else "_" for c in artist_name.lower())
        if art_key in self._artist_studio_cache:
            return self._artist_studio_cache[art_key]

        cache_file = self._artist_cache_dir / f"{art_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._artist_studio_cache[art_key] = data
                    return data
            except Exception:
                pass

        # Query MusicBrainz artist MBID
        q_art = urllib.parse.quote(f'artist:"{artist_name}"')
        artist_url = f'https://musicbrainz.org/ws/2/artist?query={q_art}&limit=1&fmt=json'
        resp = self._rate_limited_get(artist_url)
        if not resp or resp.status_code != 200:
            return {}

        try:
            art_data = resp.json()
            artists = art_data.get("artists", [])
            if not artists:
                return {}
            mbid = artists[0].get("id")
            if not mbid:
                return {}
        except Exception:
            return {}

        # Query release groups of primary type Album for this artist
        rg_url = f'https://musicbrainz.org/ws/2/release-group?artist={mbid}&type=album&limit=100&fmt=json'
        resp_rg = self._rate_limited_get(rg_url)
        if not resp_rg or resp_rg.status_code != 200:
            return {}

        studio_albums: Dict[str, Tuple[str, int]] = {}
        try:
            rg_data = resp_rg.json()
            for rg in rg_data.get("release-groups", []):
                sec_types = rg.get("secondary-types") or []
                if sec_types:  # skip Live, Compilation, Demo, Soundtrack, etc.
                    continue
                raw_title = rg.get("title") or ""
                if not raw_title or is_blacklisted_album(raw_title):
                    continue
                cleaned = clean_album_title(raw_title)
                date_str = rg.get("first-release-date", "")
                yr = int(date_str[:4]) if date_str and len(date_str) >= 4 and date_str[:4].isdigit() else None
                if cleaned and yr and 1950 <= yr <= 2030:
                    c_low = cleaned.lower()
                    if c_low not in studio_albums or yr < studio_albums[c_low][1]:
                        studio_albums[c_low] = (cleaned, yr)
        except Exception:
            pass

        if studio_albums:
            self._artist_studio_cache[art_key] = studio_albums
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(studio_albums, f, indent=2)
            except Exception:
                pass

        return studio_albums

    def _query_itunes_studio_album(self, artist_name: str, song_name: str,
                                   studio_albums: Optional[Dict[str, Tuple[str, int]]] = None) -> Tuple[Optional[str], Optional[int]]:
        """
        Fast unauthenticated track lookup via iTunes Search API, verified against canonical studio albums.
        """
        clean_s = song_name.split(':')[0].strip()
        queries = [clean_s]
        if "(" in song_name:
            no_parens = re.sub(r'\s*\([^)]*\)', '', song_name).strip()
            if no_parens and no_parens not in queries:
                queries.append(no_parens)

        for q_term in queries:
            term = f'{artist_name} {q_term}'
            url = f'https://itunes.apple.com/search?term={urllib.parse.quote(term)}&entity=song&limit=20'
            try:
                r = requests.get(url, timeout=5)
                if r.status_code != 200:
                    continue
                data = r.json()
                candidates = []
                for item in data.get('results', []):
                    art = item.get('artistName', '')
                    track = item.get('trackName', '')
                    album_raw = item.get('collectionName', '')
                    date_str = item.get('releaseDate', '')
                    coll_artist = item.get('collectionArtistName', '')

                    # Skip compilations and various artists
                    if coll_artist and (coll_artist.lower() == 'various artists' or fuzz.partial_ratio(coll_artist.lower(), artist_name.lower()) < 60):
                        continue
                    if fuzz.partial_ratio(art.lower(), artist_name.lower()) < 65:
                        continue
                    if fuzz.ratio(track.lower(), q_term.lower()) < 60 and q_term.lower() not in track.lower():
                        continue
                    if is_blacklisted_album(album_raw):
                        continue

                    cleaned_album = clean_album_title(album_raw)
                    yr = int(date_str[:4]) if date_str and len(date_str) >= 4 and date_str[:4].isdigit() else None

                    # Validate against known artist studio discography if available
                    if studio_albums:
                        matched_studio = False
                        for alb_low, (orig_title, orig_yr) in studio_albums.items():
                            if fuzz.ratio(cleaned_album.lower(), alb_low) >= 80 or alb_low in cleaned_album.lower() or cleaned_album.lower() in alb_low:
                                return orig_title, orig_yr
                        # If studio albums list exists and didn't match, this is likely a live album or re-issue
                        continue

                    if cleaned_album and yr:
                        score = 100 + max(0, 2030 - yr)
                        candidates.append((score, cleaned_album, yr))

                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    return candidates[0][1], candidates[0][2]
            except Exception:
                continue

        return None, None

    def _query_musicbrainz_studio_album(self, artist_name: str, song_name: str) -> Tuple[Optional[str], Optional[int]]:
        """Queries MusicBrainz API dynamically to find the original studio album and release year."""
        queries = self._generate_query_variations(song_name)

        for q_song in queries:
            clean_q = q_song.replace('"', '').strip()
            q_str = f'recording:"{clean_q}" AND artist:"{artist_name}"'
            url = f'https://musicbrainz.org/ws/2/recording?query={urllib.parse.quote(q_str)}&limit=40&fmt=json'

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
                rec_disam = (rec.get("disambiguation") or "").lower()
                if fuzz.ratio(rec_title.lower(), clean_q.lower()) < 65 and not any(q.lower() in rec_title.lower() for q in queries):
                    continue
                if any(kw in rec_title.lower() for kw in ["demo", "live", "instrumental demo", "bootleg"]):
                    continue
                if any(kw in rec_disam for kw in ["live", "bootleg", "instrumental demo", "remix"]):
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
        if re.search(r'[\(\[](?:special edition|deluxe edition|remastered|bonus tracks|box set)[\)\]]', album, re.IGNORECASE):
            return False
        if refresh_unresolved and album == "Non-Album / Singles":
            return False
        return True

    def get_track_info(self, artist_name: str, song_name: str, song_obj: Optional[Dict[str, Any]] = None,
                       studio_albums: Optional[Dict[str, Tuple[str, int]]] = None) -> Dict[str, Any]:
        """Retrieves track studio album information dynamically from cache, fast lookup, or MusicBrainz."""
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

        # Instant check for solos, intros, audio clips, soundchecks
        if is_known_non_album_track(song_name):
            result["resolved"] = True
            return result

        # Cache key
        cache_key = "".join(c if c.isalnum() else "_" for c in f"{artist_name}_{song_name}".lower())
        cache_file = MB_CACHE_DIR / f"{cache_key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                    if self._is_valid_cache_entry(cached):
                        result.update(cached)
                        result["album"] = clean_album_title(result.get("album", "Non-Album / Singles"))
                        return result
            except Exception:
                pass

        # 1. Fast lookup via iTunes + studio albums discography
        if studio_albums is None:
            studio_albums = self.get_artist_studio_albums(artist_name)

        album_name, release_yr = self._query_itunes_studio_album(artist_name, song_name, studio_albums=studio_albums)

        # 2. Fallback to MusicBrainz canonical recording search if needed
        if not album_name:
            album_name, release_yr = self._query_musicbrainz_studio_album(artist_name, song_name)

        if album_name:
            result["album"] = clean_album_title(album_name)
            result["release_year"] = release_yr
            result["resolved"] = True
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass
        else:
            result["resolved"] = False

        return result

    def enrich_catalog(self, songs_list: list, max_workers: int = 6,
                       refresh_unresolved: bool = False, refresh_all: bool = False,
                       progress_callback: Optional[Any] = None,
                       batch_save_callback: Optional[Any] = None) -> Dict[str, Any]:
        """
        Enriches a list of song records with album info, deduplicating unique artist-song
        pairs, pre-loading from disk cache, grouping uncached tracks by artist for batch
        discography prefetching and high-speed concurrent resolution.
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

        print(f"      [FETCH] Fast resolving studio albums for {len(uncached)} uncached tracks across artists...")

        # Group uncached tracks by artist for batch efficiency
        artist_map: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        for key, art, song in uncached:
            artist_map[art].append((key, song))

        completed = cached_count

        for art, art_tracks in artist_map.items():
            # 1. Fast resolve non-album tracks (solos/intros) first
            remaining_tracks = []
            for key, song in art_tracks:
                if is_known_non_album_track(song):
                    info = {
                        "song": song,
                        "artist": art,
                        "album": "Non-Album / Singles",
                        "release_year": None,
                        "is_cover": False,
                        "original_artist": None,
                        "resolved": True
                    }
                    results[key] = info
                    completed += 1
                    cache_key = "".join(c if c.isalnum() else "_" for c in f"{art}_{song}".lower())
                    try:
                        with open(MB_CACHE_DIR / f"{cache_key}.json", "w", encoding="utf-8") as f:
                            json.dump(info, f, indent=2)
                    except Exception:
                        pass
                else:
                    remaining_tracks.append((key, song))

            if not remaining_tracks:
                continue

            # 2. Prefetch studio albums for this artist once
            studio_albums = self.get_artist_studio_albums(art)

            # 3. Concurrently resolve tracks for this artist
            def _resolve_one(pair):
                k, s = pair
                try:
                    return k, self.get_track_info(art, s, studio_albums=studio_albums)
                except Exception:
                    return k, {
                        "song": s,
                        "artist": art,
                        "album": "Non-Album / Singles",
                        "release_year": None,
                        "is_cover": False,
                        "original_artist": None,
                        "resolved": False
                    }

            worker_count = min(max_workers, len(remaining_tracks))
            if worker_count <= 1:
                for pair in remaining_tracks:
                    k, info = _resolve_one(pair)
                    results[k] = info
                    completed += 1
                    disp = f"{art} - {info.get('song')}"
                    if len(disp) > 35:
                        disp = disp[:32] + "..."
                    pct = (completed / total_unique) * 100
                    sys.stdout.write(f"\r      [{completed}/{total_unique}] ({pct:4.1f}%) Resolving: {disp:<35} ")
                    sys.stdout.flush()
                    if progress_callback and (completed % 5 == 0 or completed == total_unique):
                        try:
                            progress_callback(completed, total_unique, f"{art} - {info.get('song')}")
                        except Exception:
                            pass
                    if batch_save_callback and completed % 25 == 0:
                        try:
                            batch_save_callback(results)
                        except Exception:
                            pass
            else:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    futures = [executor.submit(_resolve_one, pair) for pair in remaining_tracks]
                    for fut in as_completed(futures):
                        k, info = fut.result()
                        results[k] = info
                        completed += 1
                        disp = f"{art} - {info.get('song')}"
                        if len(disp) > 35:
                            disp = disp[:32] + "..."
                        pct = (completed / total_unique) * 100
                        sys.stdout.write(f"\r      [{completed}/{total_unique}] ({pct:4.1f}%) Resolving: {disp:<35} ")
                        sys.stdout.flush()

                        if progress_callback and (completed % 5 == 0 or completed == total_unique):
                            try:
                                progress_callback(completed, total_unique, f"{art} - {info.get('song')}")
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
                            refresh_unresolved: bool = False) -> Tuple[Dict[str, Any], List[Tuple[str, str, str]], int]:
        """Loads all existing cached album entries from disk without making any external API calls."""
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

        return results, uncached, total_unique
