from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional

# Band Member Tenures Database (dynamic via MusicBrainz MusicianEnricher)
BAND_MEMBERS_TENURE: Dict[str, List[Dict[str, Any]]] = {}

from src.csv_parser import normalize_artist_name

def get_effective_band_tenures() -> Dict[str, List[Dict[str, Any]]]:
    """
    Returns a unified mapping of normalized band name (lowercase) -> list of member dicts.
    If Django is initialized and MusicianTenure records exist, they are loaded.
    For any band present in the database, its database tenures take precedence.
    For any band not present in the database, BAND_MEMBERS_TENURE is used as a fallback.
    This guarantees zero duplication between DB records and static dictionary data.
    """
    tenure_map = defaultdict(list)
    db_artists_seen = set()

    try:
        from django.apps import apps
        if apps.ready:
            from apps.catalog.models import MusicianTenure
            db_tenures = MusicianTenure.objects.select_related('artist').all()
            for mt in db_tenures:
                art_name = mt.artist.name
                art_norm = (mt.artist.normalized_name or art_name).lower().strip()
                db_artists_seen.add(art_norm)
                db_artists_seen.add(art_name.lower().strip())

                member_dict = {
                    "musician": mt.musician_name,
                    "role": mt.role,
                    "instrument": mt.instrument,
                    "start": mt.start_year,
                    "end": mt.end_year
                }
                tenure_map[art_norm].append(member_dict)
                if art_name.lower().strip() != art_norm:
                    tenure_map[art_name.lower().strip()].append(member_dict)
    except Exception:
        pass

    # Fallback to BAND_MEMBERS_TENURE for any bands not populated in DB
    for band_key, members in BAND_MEMBERS_TENURE.items():
        k = band_key.lower().strip()
        if k not in db_artists_seen and not tenure_map[k]:
            tenure_map[k] = list(members)

    return tenure_map

def analyze_musicians_live(all_csv_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Cross-references every attended concert with band member tenures (by year)
    to calculate which individual musicians you have seen most often, including
    multi-band tenures and instrument breakdowns.
    """
    musician_shows = defaultdict(list)
    musician_bands = defaultdict(lambda: Counter())
    musician_roles = {}
    musician_instruments = {}
    seen_show_keys = set()

    effective_tenures = get_effective_band_tenures()

    for rec in all_csv_records:
        year = rec.get("year")
        date_str = rec.get("display_date", rec.get("raw_date", ""))
        venue = rec.get("venue", "")
        artists = rec.get("artists", [])

        for art in artists:
            canonical_art = normalize_artist_name(art)
            art_key = canonical_art.lower().strip()
            tenures = effective_tenures.get(art_key, [])

            for member in tenures:
                m_name = member["musician"]
                m_role = member["role"]
                m_instr = member.get("instrument")
                start_yr = member.get("start", 1900)
                end_yr = member.get("end")

                # Check if musician was in band during concert year
                if year:
                    if year < start_yr:
                        continue
                    if end_yr is not None and year > end_yr:
                        continue

                # Deduplicate: Avoid counting the same musician twice for the same show and band
                show_key = (m_name, date_str, venue, canonical_art)
                if show_key in seen_show_keys:
                    continue
                seen_show_keys.add(show_key)

                musician_roles[m_name] = m_role
                if m_instr and m_instr != "Other":
                    musician_instruments[m_name] = m_instr

                musician_bands[m_name][canonical_art] += 1
                musician_shows[m_name].append({
                    "date": date_str,
                    "year": year,
                    "venue": venue,
                    "band": canonical_art,
                    "role": m_role
                })

    musician_list = []
    supergroup_musicians = []
    role_counters = defaultdict(list)

    for m_name, shows in musician_shows.items():
        bands_dict = dict(musician_bands[m_name])
        total_shows = len(shows)
        unique_bands = len(bands_dict)
        primary_role = musician_roles.get(m_name, "Musician")

        # Categorize primary instrument (use pre-stored instrument if available)
        instr = musician_instruments.get(m_name)
        if not instr or instr == "Other":
            instr = "Other"
            r_low = primary_role.lower()
            if "drum" in r_low:
                instr = "Drums"
            elif "guitar" in r_low:
                instr = "Guitar"
            elif "bass" in r_low:
                instr = "Bass"
            elif "vocal" in r_low or "singer" in r_low:
                instr = "Vocals"
            elif "key" in r_low or "piano" in r_low or "synth" in r_low:
                instr = "Keyboards"

        data = {
            "musician": m_name,
            "role": primary_role,
            "instrument": instr,
            "total_shows": total_shows,
            "unique_bands_count": unique_bands,
            "bands": bands_dict,
            "shows": sorted(shows, key=lambda s: s["date"], reverse=True),
            "first_seen": shows[-1]["date"] if shows else None,
            "last_seen": shows[0]["date"] if shows else None
        }

        musician_list.append(data)
        role_counters[instr].append(data)

        if unique_bands > 1:
            supergroup_musicians.append(data)

    musician_list.sort(key=lambda m: (m["total_shows"], m["unique_bands_count"]), reverse=True)
    supergroup_musicians.sort(key=lambda m: (m["unique_bands_count"], m["total_shows"]), reverse=True)

    for instr in role_counters:
        role_counters[instr].sort(key=lambda m: m["total_shows"], reverse=True)

    return {
        "top_musicians": musician_list,
        "supergroup_musicians": supergroup_musicians,
        "by_instrument": dict(role_counters),
        "total_musicians_tracked": len(musician_list)
    }
