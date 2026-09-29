import os
import re
import json
import time
import urllib.parse
import urllib.request
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from rapidfuzz import fuzz
from src.config import MB_CACHE_DIR, CONTACT_EMAIL, APP_URL
from src.csv_parser import normalize_artist_name

ARTIST_MBID_CACHE_DIR = MB_CACHE_DIR / "artists"
TENURE_CACHE_DIR = MB_CACHE_DIR / "tenures"

for d in [ARTIST_MBID_CACHE_DIR, TENURE_CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

class MusicianEnricher:
    """
    Enriches artist catalog with dynamic musician line-up and historical tenures
    queried from MusicBrainz artist-artist relationships, with intelligent instrument
    classification, multi-stint support, rate limiting, and strict deduplication.
    """

    ROLE_MAP = {
        'drums (drum set)': 'Drums',
        'percussion': 'Percussion',
        'guitar': 'Guitar',
        'lead guitar': 'Lead Guitar',
        'rhythm guitar': 'Rhythm Guitar',
        'acoustic guitar': 'Acoustic Guitar',
        'electric guitar': 'Electric Guitar',
        'electric bass guitar': 'Bass',
        'bass guitar': 'Bass',
        'bass': 'Bass',
        'chapman stick': 'Chapman Stick',
        'lead vocals': 'Lead Vocals',
        'background vocals': 'Backing Vocals',
        'vocals': 'Vocals',
        'keyboard': 'Keyboards',
        'keyboards': 'Keyboards',
        'piano': 'Piano',
        'synthesizer': 'Synthesizer',
        'continuum': 'Continuum',
        'lap steel guitar': 'Lap Steel Guitar',
    }

    def __init__(self, contact_email: Optional[str] = None):
        email = contact_email or CONTACT_EMAIL or "admin@localhost"
        url_part = f" {APP_URL};" if APP_URL else ""
        self.headers = {
            "User-Agent": f"ConcertTrakr/1.0.0 ({email};{url_part})",
            "Accept": "application/json"
        }
        self._last_req_time = 0.0
        self._req_lock = threading.Lock()

    def _rate_limited_get(self, url: str) -> Optional[Dict[str, Any]]:
        """Executes a polite, rate-limited GET request to MusicBrainz with retry on 503/429."""
        for attempt in range(4):
            with self._req_lock:
                elapsed = time.time() - self._last_req_time
                if elapsed < 1.25:
                    time.sleep(1.25 - elapsed)
                self._last_req_time = time.time()

            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return None
            except Exception:
                return None
        return None

    def search_artist_mbid(self, artist_name: str) -> Optional[str]:
        """
        Searches MusicBrainz for an artist's MBID, using local disk cache first,
        prioritizing Group entity types and high name similarity.
        """
        if not artist_name:
            return None

        safe_key = "".join(c if c.isalnum() else "_" for c in artist_name.lower().strip())
        cache_file = ARTIST_MBID_CACHE_DIR / f"{safe_key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("mbid")
            except Exception:
                pass

        query = urllib.parse.quote(f'artist:"{artist_name}"')
        url = f"https://musicbrainz.org/ws/2/artist?query={query}&fmt=json"
        data = self._rate_limited_get(url)
        if not data:
            return None

        candidates = data.get("artists", [])
        if not candidates:
            return None

        scored = []
        for a in candidates[:8]:
            score = a.get("score", 0)
            a_name = a.get("name", "")
            a_type = a.get("type", "")
            ratio = fuzz.ratio(a_name.lower(), artist_name.lower())
            total = score + ratio
            if a_type == "Group":
                total += 25
            scored.append((total, a))

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        mbid = best.get("id")

        if mbid:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump({"artist": artist_name, "matched_name": best.get("name"), "mbid": mbid}, f, indent=2)
            except Exception:
                pass

        return mbid

    def get_artist_relations(self, mbid: str, refresh: bool = False) -> Optional[Dict[str, Any]]:
        """Fetches artist relationships from MusicBrainz with local disk caching."""
        if not mbid:
            return None

        cache_file = TENURE_CACHE_DIR / f"{mbid}.json"
        if not refresh and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        url = f"https://musicbrainz.org/ws/2/artist/{mbid}?inc=artist-rels&fmt=json"
        data = self._rate_limited_get(url)
        if data:
            try:
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

        return data

    @staticmethod
    def _parse_year(val: Any) -> Optional[int]:
        if not val:
            return None
        s = str(val).strip()
        if len(s) >= 4 and s[:4].isdigit():
            y = int(s[:4])
            if 1900 <= y <= 2030:
                return y
        return None

    @classmethod
    def classify_instrument(cls, attrs: List[str], role_str: str = "") -> str:
        """
        Infers standard instrument category (Drums, Bass, Guitar, Vocals, Keyboards, Other)
        from MusicBrainz attributes and role descriptions, with proper priority disambiguation.
        """
        attrs_low = [a.lower() for a in attrs]
        text = (" ".join(attrs_low) + " " + role_str).lower()

        # 1. Rhythm Section (check first)
        if any(k in text for k in ['drum', 'percussion']):
            return 'Drums'
        if any(k in text for k in ['bass', 'chapman stick']):
            return 'Bass'

        # 2. Keyboards (prevent lap steel guitar from mistakenly overriding keyboardists)
        has_keys = any(k in text for k in ['keyboard', 'piano', 'synth', 'organ', 'mellotron', 'continuum'])
        has_real_guitar = any(k in text for k in ['guitar', 'electric guitar', 'acoustic guitar']) and not ('lap steel' in text and len(attrs_low) <= 2)

        if has_keys and ('lap steel' in text or not has_real_guitar):
            return 'Keyboards'

        # 3. Guitar
        if has_real_guitar:
            return 'Guitar'

        # 4. Vocals
        if any(k in text for k in ['lead vocal', 'vocal', 'singer']):
            return 'Vocals'

        if has_keys:
            return 'Keyboards'

        return 'Other'

    @classmethod
    def format_role(cls, attrs: List[str]) -> str:
        """Constructs a clean human-readable role string from MusicBrainz attributes."""
        roles = []
        for a in attrs:
            al = a.lower().strip()
            if al in ['original', 'founder', 'member', 'guest']:
                continue
            mapped = cls.ROLE_MAP.get(al, a.title())
            if mapped not in roles:
                roles.append(mapped)
        if not roles:
            return 'Musician'
        return ' / '.join(roles)

    def parse_member_tenures(self, mb_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parses raw MusicBrainz relations into aggregated member tenures,
        combining multiple attribute entries for the same musician stint.
        """
        if not mb_data:
            return []

        raw_members: Dict[Tuple[str, int, Optional[int]], Dict[str, Any]] = {}

        for r in mb_data.get("relations", []):
            if r.get("type") != "member of band":
                continue

            musician = r.get("artist", {})
            m_name = (musician.get("name") or "").strip()
            if not m_name:
                continue

            start_yr = self._parse_year(r.get("begin")) or 1900
            ended = r.get("ended", False)
            end_yr = self._parse_year(r.get("end"))
            if not ended and end_yr is None:
                end_yr = None

            key = (m_name.lower(), start_yr, end_yr)
            if key not in raw_members:
                raw_members[key] = {
                    "musician": m_name,
                    "start": start_yr,
                    "end": end_yr,
                    "ended": ended,
                    "attributes": set()
                }

            for a in r.get("attributes", []):
                raw_members[key]["attributes"].add(a)

        parsed_list = []
        for key, m in sorted(raw_members.items(), key=lambda x: (x[1]["start"], x[1]["musician"])):
            attrs = sorted(list(m["attributes"]))
            instr = self.classify_instrument(attrs)
            role = self.format_role(attrs)
            parsed_list.append({
                "musician": m["musician"],
                "role": role,
                "instrument": instr,
                "start": m["start"],
                "end": m["end"],
                "attributes": attrs
            })

        return parsed_list

    def enrich_artist(self, artist_name: str, artist_obj: Optional[Any] = None,
                       refresh: bool = False) -> List[Dict[str, Any]]:
        """
        High-level helper to enrich an artist:
        1. Resolves MBID (using artist_obj.mbid if present, else searches MB).
        2. Fetches and parses relations.
        3. If artist_obj (Django model) is provided, updates artist.mbid and
           syncs into MusicianTenure with strict deduplication.
        """
        mbid = getattr(artist_obj, 'mbid', None) or self.search_artist_mbid(artist_name)
        if not mbid:
            return []

        if artist_obj and not getattr(artist_obj, 'mbid', None):
            try:
                artist_obj.mbid = mbid
                artist_obj.save(update_fields=['mbid'])
            except Exception:
                pass

        mb_data = self.get_artist_relations(mbid, refresh=refresh)
        if not mb_data:
            return []

        tenures = self.parse_member_tenures(mb_data)

        if artist_obj:
            self._sync_tenures_to_db(artist_obj, tenures)

        return tenures

    def _sync_tenures_to_db(self, artist_obj: Any, incoming_tenures: List[Dict[str, Any]]) -> Tuple[int, int]:
        """
        Persists parsed tenures into the MusicianTenure database table,
        strictly preventing duplicates while updating missing metadata.
        Returns (created_count, updated_count).
        """
        from apps.catalog.models import MusicianTenure

        existing_tenures = list(MusicianTenure.objects.filter(artist=artist_obj))
        created_count = 0
        updated_count = 0

        for inc in incoming_tenures:
            m_name = inc["musician"].strip()
            start_yr = inc["start"]
            end_yr = inc["end"]
            role = inc["role"]
            instr = inc["instrument"]

            # Check if this tenure matches an existing DB record
            matched = None
            for ex in existing_tenures:
                name_match = (
                    ex.musician_name.lower().strip() == m_name.lower() or
                    fuzz.ratio(ex.musician_name.lower(), m_name.lower()) >= 88
                )
                if not name_match:
                    continue

                # Check if tenure windows match or overlap
                start_diff = abs(ex.start_year - start_yr)
                ex_end = ex.end_year or 9999
                inc_end = end_yr or 9999

                # Matching conditions:
                # 1. Start years match closely (within 2 years)
                # 2. Or the tenure periods overlap significantly
                if start_diff <= 2 or (max(ex.start_year, start_yr) <= min(ex_end, inc_end) + 1):
                    matched = ex
                    break

            if matched:
                # Deduplication: already exists! Check if we can enhance missing metadata
                changed = False
                if matched.end_year is None and end_yr is not None:
                    matched.end_year = end_yr
                    changed = True
                if matched.instrument == 'Other' and instr != 'Other':
                    matched.instrument = instr
                    changed = True

                if changed:
                    matched.save()
                    updated_count += 1
            else:
                # Brand new stint/tenure
                new_obj = MusicianTenure.objects.create(
                    artist=artist_obj,
                    musician_name=m_name,
                    role=role,
                    instrument=instr,
                    start_year=start_yr,
                    end_year=end_yr
                )
                existing_tenures.append(new_obj)
                created_count += 1

        return created_count, updated_count
