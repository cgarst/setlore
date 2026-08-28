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
    # Normalize unicode quotes and dashes
    cleaned = title.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-").replace("‐", "-")
    cleaned = re.sub(
        r'\s*[\(\[](?:special edition|deluxe edition|deluxe box set|deluxe|box set|expanded edition|expanded|remastered|remaster|bonus track[s]? edition|bonus tracks|\d+th anniversary edition|anniversary edition|explicit|clean|tour edition|limited edition)[\)\]]',
        '',
        cleaned,
        flags=re.IGNORECASE
    ).strip()
    
    # Fix known casing and name duplicates
    c_low = cleaned.lower()
    if c_low == "metropolis, pt. 2: scenes from a memory":
        return "Metropolis, Pt. 2: Scenes from a Memory"
    if c_low in ["kill 'em all", "kill 'em all (remastered)"]:
        return "Kill 'Em All"
    if c_low == "reload":
        return "Reload"
    if c_low == "load":
        return "Load"
    if c_low in ["hardwired... to self-destruct", "hardwired...to self-destruct"]:
        return "Hardwired... to Self-Destruct"
    if c_low == "...and justice for all":
        return "...And Justice for All"
    if c_low == "ride the lightning":
        return "Ride the Lightning"
    if c_low == "master of puppets":
        return "Master of Puppets"
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

CANONICAL_ALBUM_YEARS = {
    # Metallica
    ("metallica", "kill 'em all"): 1983,
    ("metallica", "ride the lightning"): 1984,
    ("metallica", "master of puppets"): 1986,
    ("metallica", "...and justice for all"): 1988,
    ("metallica", "metallica"): 1991,
    ("metallica", "load"): 1996,
    ("metallica", "reload"): 1997,
    ("metallica", "garage inc."): 1998,
    ("metallica", "st. anger"): 2003,
    ("metallica", "death magnetic"): 2008,
    ("metallica", "hardwired... to self-destruct"): 2016,
    ("metallica", "72 seasons"): 2023,
    # Dream Theater
    ("dream theater", "when dream and day unite"): 1989,
    ("dream theater", "images and words"): 1992,
    ("dream theater", "awake"): 1994,
    ("dream theater", "falling into infinity"): 1997,
    ("dream theater", "metropolis, pt. 2: scenes from a memory"): 1999,
    ("dream theater", "six degrees of inner turbulence"): 2002,
    ("dream theater", "train of thought"): 2003,
    ("dream theater", "octavarium"): 2005,
    ("dream theater", "systematic chaos"): 2007,
    ("dream theater", "black clouds & silver linings"): 2009,
    ("dream theater", "a dramatic turn of events"): 2011,
    ("dream theater", "dream theater"): 2013,
    ("dream theater", "the astonishing"): 2016,
    ("dream theater", "distance over time"): 2019,
    ("dream theater", "a view from the top of the world"): 2021,
    ("dream theater", "parasomnia"): 2025,
    # Iron Maiden
    ("iron maiden", "iron maiden"): 1980,
    ("iron maiden", "killers"): 1981,
    ("iron maiden", "the number of the beast"): 1982,
    ("iron maiden", "piece of mind"): 1983,
    ("iron maiden", "powerslave"): 1984,
    ("iron maiden", "somewhere in time"): 1986,
    ("iron maiden", "seventh son of a seventh son"): 1988,
    ("iron maiden", "no prayer for the dying"): 1990,
    ("iron maiden", "fear of the dark"): 1992,
    ("iron maiden", "the x factor"): 1995,
    ("iron maiden", "virtual xi"): 1998,
    ("iron maiden", "brave new world"): 2000,
    ("iron maiden", "dance of death"): 2003,
    ("iron maiden", "a matter of life and death"): 2006,
    ("iron maiden", "the final frontier"): 2010,
    ("iron maiden", "the book of souls"): 2015,
    ("iron maiden", "senjutsu"): 2021,
    # Megadeth
    ("megadeth", "killing is my business... and business is good!"): 1985,
    ("megadeth", "peace sells... but who's buying?"): 1986,
    ("megadeth", "so far, so good... so what!"): 1988,
    ("megadeth", "rust in peace"): 1990,
    ("megadeth", "countdown to extinction"): 1992,
    ("megadeth", "youthanasia"): 1994,
    ("megadeth", "cryptic writings"): 1997,
    ("megadeth", "risk"): 1999,
    ("megadeth", "the world needs a hero"): 2001,
    ("megadeth", "the system has failed"): 2004,
    ("megadeth", "united abominations"): 2007,
    ("megadeth", "endgame"): 2009,
    ("megadeth", "th1rt3en"): 2011,
    ("megadeth", "super collider"): 2013,
    ("megadeth", "dystopia"): 2016,
    ("megadeth", "the sick, the dying... and the dead!"): 2022,
}

CANONICAL_TRACK_ALBUMS = {
    # Metallica - Kill 'Em All (1983)
    ("metallica", "hit the lights"): ("Kill 'Em All", 1983),
    ("metallica", "the four horsemen"): ("Kill 'Em All", 1983),
    ("metallica", "motorbreath"): ("Kill 'Em All", 1983),
    ("metallica", "jump in the fire"): ("Kill 'Em All", 1983),
    ("metallica", "(anesthesia) - pulling teeth"): ("Kill 'Em All", 1983),
    ("metallica", "(anesthesia) pulling teeth"): ("Kill 'Em All", 1983),
    ("metallica", "anesthesia - pulling teeth"): ("Kill 'Em All", 1983),
    ("metallica", "whiplash"): ("Kill 'Em All", 1983),
    ("metallica", "phantom lord"): ("Kill 'Em All", 1983),
    ("metallica", "no remorse"): ("Kill 'Em All", 1983),
    ("metallica", "seek & destroy"): ("Kill 'Em All", 1983),
    ("metallica", "seek and destroy"): ("Kill 'Em All", 1983),
    ("metallica", "metal militia"): ("Kill 'Em All", 1983),
    # Metallica - Ride the Lightning (1984)
    ("metallica", "fight fire with fire"): ("Ride the Lightning", 1984),
    ("metallica", "ride the lightning"): ("Ride the Lightning", 1984),
    ("metallica", "for whom the bell tolls"): ("Ride the Lightning", 1984),
    ("metallica", "fade to black"): ("Ride the Lightning", 1984),
    ("metallica", "trapped under ice"): ("Ride the Lightning", 1984),
    ("metallica", "escape"): ("Ride the Lightning", 1984),
    ("metallica", "creeping death"): ("Ride the Lightning", 1984),
    ("metallica", "the call of ktulu"): ("Ride the Lightning", 1984),
    # Metallica - Master of Puppets (1986)
    ("metallica", "battery"): ("Master of Puppets", 1986),
    ("metallica", "master of puppets"): ("Master of Puppets", 1986),
    ("metallica", "the thing that should not be"): ("Master of Puppets", 1986),
    ("metallica", "welcome home (sanitarium)"): ("Master of Puppets", 1986),
    ("metallica", "disposable heroes"): ("Master of Puppets", 1986),
    ("metallica", "leper messiah"): ("Master of Puppets", 1986),
    ("metallica", "orion"): ("Master of Puppets", 1986),
    ("metallica", "damage, inc."): ("Master of Puppets", 1986),
    ("metallica", "damage inc."): ("Master of Puppets", 1986),
    # Metallica - ...And Justice for All (1988)
    ("metallica", "blackened"): ("...And Justice for All", 1988),
    ("metallica", "...and justice for all"): ("...And Justice for All", 1988),
    ("metallica", "and justice for all"): ("...And Justice for All", 1988),
    ("metallica", "eye of the beholder"): ("...And Justice for All", 1988),
    ("metallica", "one"): ("...And Justice for All", 1988),
    ("metallica", "the shortest straw"): ("...And Justice for All", 1988),
    ("metallica", "harvester of sorrow"): ("...And Justice for All", 1988),
    ("metallica", "the frayed ends of sanity"): ("...And Justice for All", 1988),
    ("metallica", "to live is to die"): ("...And Justice for All", 1988),
    ("metallica", "dyers eve"): ("...And Justice for All", 1988),
    # Metallica - Metallica / The Black Album (1991)
    ("metallica", "enter sandman"): ("Metallica", 1991),
    ("metallica", "sad but true"): ("Metallica", 1991),
    ("metallica", "holier than thou"): ("Metallica", 1991),
    ("metallica", "the unforgiven"): ("Metallica", 1991),
    ("metallica", "wherever i may roam"): ("Metallica", 1991),
    ("metallica", "don't tread on me"): ("Metallica", 1991),
    ("metallica", "through the never"): ("Metallica", 1991),
    ("metallica", "nothing else matters"): ("Metallica", 1991),
    ("metallica", "of wolf and man"): ("Metallica", 1991),
    ("metallica", "the god that failed"): ("Metallica", 1991),
    ("metallica", "my friend of misery"): ("Metallica", 1991),
    ("metallica", "the struggle within"): ("Metallica", 1991),
    # Metallica - Load (1996)
    ("metallica", "ain't my bitch"): ("Load", 1996),
    ("metallica", "2 x 4"): ("Load", 1996),
    ("metallica", "2x4"): ("Load", 1996),
    ("metallica", "the house jack built"): ("Load", 1996),
    ("metallica", "until it sleeps"): ("Load", 1996),
    ("metallica", "king nothing"): ("Load", 1996),
    ("metallica", "hero of the day"): ("Load", 1996),
    ("metallica", "bleeding me"): ("Load", 1996),
    ("metallica", "cure"): ("Load", 1996),
    ("metallica", "poor twisted me"): ("Load", 1996),
    ("metallica", "wasting my hate"): ("Load", 1996),
    ("metallica", "mama said"): ("Load", 1996),
    ("metallica", "thorn within"): ("Load", 1996),
    ("metallica", "ronnie"): ("Load", 1996),
    ("metallica", "the outlaw torn"): ("Load", 1996),
    # Metallica - Reload (1997)
    ("metallica", "fuel"): ("Reload", 1997),
    ("metallica", "the memory remains"): ("Reload", 1997),
    ("metallica", "devil's dance"): ("Reload", 1997),
    ("metallica", "the unforgiven ii"): ("Reload", 1997),
    ("metallica", "the unforgiven 2"): ("Reload", 1997),
    ("metallica", "better than you"): ("Reload", 1997),
    ("metallica", "slither"): ("Reload", 1997),
    ("metallica", "carpe diem baby"): ("Reload", 1997),
    ("metallica", "bad seed"): ("Reload", 1997),
    ("metallica", "where the wild things are"): ("Reload", 1997),
    ("metallica", "prince charming"): ("Reload", 1997),
    ("metallica", "low man's lyric"): ("Reload", 1997),
    ("metallica", "attitude"): ("Reload", 1997),
    ("metallica", "fixxxer"): ("Reload", 1997),
    # Metallica - Garage Inc. (1998)
    ("metallica", "whiskey in the jar"): ("Garage Inc.", 1998),
    ("metallica", "turn the page"): ("Garage Inc.", 1998),
    ("metallica", "die, die my darling"): ("Garage Inc.", 1998),
    ("metallica", "am i evil?"): ("Garage Inc.", 1998),
    ("metallica", "breadfan"): ("Garage Inc.", 1998),
    ("metallica", "blitzkrieg"): ("Garage Inc.", 1998),
    ("metallica", "the prince"): ("Garage Inc.", 1998),
    ("metallica", "stone cold crazy"): ("Garage Inc.", 1998),
    ("metallica", "sabbra cadabra"): ("Garage Inc.", 1998),
    ("metallica", "mercyful fate"): ("Garage Inc.", 1998),
    ("metallica", "astronomy"): ("Garage Inc.", 1998),
    ("metallica", "it's electric"): ("Garage Inc.", 1998),
    # Metallica - St. Anger (2003)
    ("metallica", "frantic"): ("St. Anger", 2003),
    ("metallica", "st. anger"): ("St. Anger", 2003),
    ("metallica", "some kind of monster"): ("St. Anger", 2003),
    ("metallica", "dirty window"): ("St. Anger", 2003),
    ("metallica", "invisible kid"): ("St. Anger", 2003),
    ("metallica", "my world"): ("St. Anger", 2003),
    ("metallica", "shoot me again"): ("St. Anger", 2003),
    ("metallica", "sweet amber"): ("St. Anger", 2003),
    ("metallica", "the unnamed feeling"): ("St. Anger", 2003),
    ("metallica", "purify"): ("St. Anger", 2003),
    ("metallica", "all within my hands"): ("St. Anger", 2003),
    # Metallica - Death Magnetic (2008)
    ("metallica", "that was just your life"): ("Death Magnetic", 2008),
    ("metallica", "the end of the line"): ("Death Magnetic", 2008),
    ("metallica", "broken, beat & scarred"): ("Death Magnetic", 2008),
    ("metallica", "the day that never comes"): ("Death Magnetic", 2008),
    ("metallica", "all nightmare long"): ("Death Magnetic", 2008),
    ("metallica", "cyanide"): ("Death Magnetic", 2008),
    ("metallica", "the unforgiven iii"): ("Death Magnetic", 2008),
    ("metallica", "the unforgiven 3"): ("Death Magnetic", 2008),
    ("metallica", "the judas kiss"): ("Death Magnetic", 2008),
    ("metallica", "suicide & redemption"): ("Death Magnetic", 2008),
    ("metallica", "my apocalypse"): ("Death Magnetic", 2008),
    # Metallica - Hardwired... to Self-Destruct (2016)
    ("metallica", "hardwired"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "atlas, rise!"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "now that we're dead"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "moth into flame"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "dream no more"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "halo on fire"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "confusion"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "manunkind"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "here comes revenge"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "am i savage?"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "murder one"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "spit out the bone"): ("Hardwired... to Self-Destruct", 2016),
    ("metallica", "lords of summer"): ("Hardwired... to Self-Destruct", 2016),
    # Metallica - 72 Seasons (2023)
    ("metallica", "72 seasons"): ("72 Seasons", 2023),
    ("metallica", "shadows follow"): ("72 Seasons", 2023),
    ("metallica", "screaming suicide"): ("72 Seasons", 2023),
    ("metallica", "sleepwalk my life away"): ("72 Seasons", 2023),
    ("metallica", "you must burn!"): ("72 Seasons", 2023),
    ("metallica", "lux æterna"): ("72 Seasons", 2023),
    ("metallica", "lux aeterna"): ("72 Seasons", 2023),
    ("metallica", "crown of barbed wire"): ("72 Seasons", 2023),
    ("metallica", "chasing light"): ("72 Seasons", 2023),
    ("metallica", "if darkness had a son"): ("72 Seasons", 2023),
    ("metallica", "too far gone?"): ("72 Seasons", 2023),
    ("metallica", "room of mirrors"): ("72 Seasons", 2023),
    ("metallica", "inamorata"): ("72 Seasons", 2023),
}

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
                    
                    # Extract earliest valid year across release-group, recording, and release dates
                    rg_date = rg.get("first-release-date", "")
                    rec_date = rec.get("first-release-date", "")
                    rel_date = rel.get("date", "")
                    
                    years = []
                    for d_str in [rg_date, rec_date, rel_date]:
                        if d_str and len(d_str) >= 4 and d_str[:4].isdigit():
                            y = int(d_str[:4])
                            if 1950 <= y <= 2026:
                                years.append(y)
                    
                    yr = min(years) if years else None
                    
                    # Canonical year override for known albums
                    artist_clean = artist_name.strip().lower()
                    album_clean = title.strip().lower()
                    if (artist_clean, album_clean) in CANONICAL_ALBUM_YEARS:
                        yr = CANONICAL_ALBUM_YEARS[(artist_clean, album_clean)]

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
        if re.search(r'[\(\[](?:special edition|deluxe edition|remastered|bonus tracks|box set)[\)\]]', album, re.IGNORECASE):
            return False
        # Check casing consistency
        if album == "Metropolis, Pt. 2: Scenes From a Memory":
            return False
        # If user explicitly requested refresh of unresolved tracks
        if refresh_unresolved and album == "Non-Album / Singles":
            return False

        # Invalidate known misattributed or future-dated records
        artist = (cached.get("artist") or "").strip().lower()
        song = (cached.get("song") or "").strip().lower()
        album_clean = clean_album_title(album).strip().lower()
        yr = cached.get("release_year")

        if (artist, song) in CANONICAL_TRACK_ALBUMS:
            expected_album, expected_yr = CANONICAL_TRACK_ALBUMS[(artist, song)]
            if album_clean != expected_album.strip().lower() or yr != expected_yr:
                return False

        if (artist, album_clean) in CANONICAL_ALBUM_YEARS:
            expected_yr = CANONICAL_ALBUM_YEARS[(artist, album_clean)]
            if yr != expected_yr:
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

        # Check canonical track albums first!
        art_norm = artist_name.strip().lower()
        song_norm = song_name.strip().lower()
        if (art_norm, song_norm) in CANONICAL_TRACK_ALBUMS:
            can_album, can_yr = CANONICAL_TRACK_ALBUMS[(art_norm, song_norm)]
            result["album"] = can_album
            result["release_year"] = can_yr
            result["resolved"] = True
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, indent=2)
            except Exception:
                pass
            return result

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

