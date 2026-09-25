from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional

# Band Member Tenures Database
# Format: band_name_lower -> list of dicts:
# { "musician": str, "role": str, "start": int, "end": int or None (None = present) }
BAND_MEMBERS_TENURE = {
    "dream theater": [
        {"musician": "John Petrucci", "role": "Guitar / Backing Vocals", "start": 1985, "end": None},
        {"musician": "John Myung", "role": "Bass", "start": 1985, "end": None},
        {"musician": "James LaBrie", "role": "Lead Vocals", "start": 1991, "end": None},
        {"musician": "Jordan Rudess", "role": "Keyboards", "start": 1999, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Backing Vocals", "start": 1985, "end": 2010},
        {"musician": "Mike Mangini", "role": "Drums", "start": 2010, "end": 2023},
        {"musician": "Mike Portnoy", "role": "Drums / Backing Vocals", "start": 2023, "end": None},
        {"musician": "Derek Sherinian", "role": "Keyboards", "start": 1994, "end": 1999},
        {"musician": "Kevin Moore", "role": "Keyboards", "start": 1985, "end": 1994},
    ],
    "john petrucci": [
        {"musician": "John Petrucci", "role": "Guitar", "start": 2000, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums", "start": 2022, "end": 2023},
        {"musician": "Dave LaRue", "role": "Bass", "start": 2001, "end": None},
    ],
    "the winery dogs": [
        {"musician": "Richie Kotzen", "role": "Lead Vocals / Guitar", "start": 2012, "end": None},
        {"musician": "Billy Sheehan", "role": "Bass / Vocals", "start": 2012, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2012, "end": None},
    ],
    "winery dogs": [
        {"musician": "Richie Kotzen", "role": "Lead Vocals / Guitar", "start": 2012, "end": None},
        {"musician": "Billy Sheehan", "role": "Bass / Vocals", "start": 2012, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2012, "end": None},
    ],
    "the neal morse band": [
        {"musician": "Neal Morse", "role": "Lead Vocals / Keyboards / Guitar", "start": 2012, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2012, "end": None},
        {"musician": "Randy George", "role": "Bass", "start": 2012, "end": None},
        {"musician": "Eric Gillette", "role": "Lead Guitar / Vocals", "start": 2012, "end": None},
        {"musician": "Bill Hubauer", "role": "Keyboards / Vocals", "start": 2012, "end": None},
    ],
    "neal morse band": [
        {"musician": "Neal Morse", "role": "Lead Vocals / Keyboards / Guitar", "start": 2012, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2012, "end": None},
        {"musician": "Randy George", "role": "Bass", "start": 2012, "end": None},
        {"musician": "Eric Gillette", "role": "Lead Guitar / Vocals", "start": 2012, "end": None},
        {"musician": "Bill Hubauer", "role": "Keyboards / Vocals", "start": 2012, "end": None},
    ],
    "neal morse": [
        {"musician": "Neal Morse", "role": "Lead Vocals / Keyboards / Guitar", "start": 1995, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2003, "end": None},
        {"musician": "Randy George", "role": "Bass", "start": 2003, "end": None},
    ],
    "steve morse band": [
        {"musician": "Steve Morse", "role": "Lead Guitar", "start": 1984, "end": None},
        {"musician": "Dave LaRue", "role": "Bass", "start": 1989, "end": None},
        {"musician": "Van Romaine", "role": "Drums", "start": 1991, "end": None},
    ],
    "transatlantic": [
        {"musician": "Neal Morse", "role": "Lead Vocals / Keyboards / Guitar", "start": 1999, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 1999, "end": None},
        {"musician": "Roine Stolt", "role": "Guitar / Vocals", "start": 1999, "end": None},
        {"musician": "Pete Trewavas", "role": "Bass / Vocals", "start": 1999, "end": None},
    ],
    "flying colors": [
        {"musician": "Casey McPherson", "role": "Lead Vocals / Rhythm Guitar", "start": 2011, "end": None},
        {"musician": "Steve Morse", "role": "Lead Guitar", "start": 2011, "end": None},
        {"musician": "Neal Morse", "role": "Keyboards / Vocals", "start": 2011, "end": None},
        {"musician": "Dave LaRue", "role": "Bass", "start": 2011, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2011, "end": None},
    ],
    "sons of apollo": [
        {"musician": "Jeff Scott Soto", "role": "Lead Vocals", "start": 2017, "end": None},
        {"musician": "Ron \"Bumblefoot\" Thal", "role": "Guitar / Vocals", "start": 2017, "end": None},
        {"musician": "Billy Sheehan", "role": "Bass / Vocals", "start": 2017, "end": None},
        {"musician": "Derek Sherinian", "role": "Keyboards", "start": 2017, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums / Vocals", "start": 2017, "end": None},
    ],
    "liquid tension experiment": [
        {"musician": "John Petrucci", "role": "Guitar", "start": 1997, "end": None},
        {"musician": "Jordan Rudess", "role": "Keyboards", "start": 1997, "end": None},
        {"musician": "Tony Levin", "role": "Bass / Chapman Stick", "start": 1997, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums", "start": 1997, "end": None},
    ],
    "adrenaline mob": [
        {"musician": "Russell Allen", "role": "Lead Vocals", "start": 2011, "end": None},
        {"musician": "Mike Orlando", "role": "Guitar", "start": 2011, "end": None},
        {"musician": "Mike Portnoy", "role": "Drums", "start": 2011, "end": 2013},
        {"musician": "John Moyer", "role": "Bass", "start": 2012, "end": 2014},
    ],
    "porcupine tree": [
        {"musician": "Steven Wilson", "role": "Lead Vocals / Guitar / Keys", "start": 1987, "end": None},
        {"musician": "Richard Barbieri", "role": "Keyboards / Synthesizers", "start": 1993, "end": None},
        {"musician": "Gavin Harrison", "role": "Drums", "start": 2002, "end": None},
        {"musician": "Colin Edwin", "role": "Bass", "start": 1993, "end": 2010},
        {"musician": "Nate Navarro", "role": "Bass (Touring)", "start": 2022, "end": None},
        {"musician": "Randy McStine", "role": "Guitar / Vocals (Touring)", "start": 2022, "end": None},
    ],
    "steven wilson": [
        {"musician": "Steven Wilson", "role": "Lead Vocals / Guitar / Keys", "start": 2008, "end": None},
        {"musician": "Nick Beggs", "role": "Bass / Chapman Stick / Vocals", "start": 2011, "end": None},
        {"musician": "Adam Holzman", "role": "Keyboards", "start": 2011, "end": None},
        {"musician": "Craig Blundell", "role": "Drums", "start": 2015, "end": 2020},
        {"musician": "Marco Minnemann", "role": "Drums", "start": 2011, "end": 2015},
        {"musician": "Guthrie Govan", "role": "Guitar", "start": 2012, "end": 2015},
        {"musician": "Alex Hutchings", "role": "Guitar", "start": 2017, "end": 2020},
    ],
    "the aristocrats": [
        {"musician": "Guthrie Govan", "role": "Guitar", "start": 2011, "end": None},
        {"musician": "Bryan Beller", "role": "Bass", "start": 2011, "end": None},
        {"musician": "Marco Minnemann", "role": "Drums", "start": 2011, "end": None},
    ],
    "aristocrats": [
        {"musician": "Guthrie Govan", "role": "Guitar", "start": 2011, "end": None},
        {"musician": "Bryan Beller", "role": "Bass", "start": 2011, "end": None},
        {"musician": "Marco Minnemann", "role": "Drums", "start": 2011, "end": None},
    ],
    "joe satriani": [
        {"musician": "Joe Satriani", "role": "Lead Guitar", "start": 1986, "end": None},
        {"musician": "Marco Minnemann", "role": "Drums", "start": 2013, "end": 2016},
        {"musician": "Bryan Beller", "role": "Bass", "start": 2013, "end": None},
        {"musician": "Mike Keneally", "role": "Keyboards / Guitar", "start": 2010, "end": 2020},
    ],
    "opeth": [
        {"musician": "Mikael Åkerfeldt", "role": "Lead Vocals / Guitar", "start": 1990, "end": None},
        {"musician": "Martin Mendez", "role": "Bass", "start": 1997, "end": None},
        {"musician": "Fredrik Åkesson", "role": "Guitar / Backing Vocals", "start": 2007, "end": None},
        {"musician": "Joakim Svalberg", "role": "Keyboards / Backing Vocals", "start": 2011, "end": None},
        {"musician": "Waltteri Väyrynen", "role": "Drums", "start": 2022, "end": None},
        {"musician": "Martin Axenrot", "role": "Drums", "start": 2006, "end": 2021},
        {"musician": "Per Wiberg", "role": "Keyboards", "start": 2005, "end": 2011},
        {"musician": "Peter Lindgren", "role": "Guitar", "start": 1991, "end": 2007},
    ],
    "symphony x": [
        {"musician": "Michael Romeo", "role": "Lead Guitar / Backing Vocals", "start": 1994, "end": None},
        {"musician": "Russell Allen", "role": "Lead Vocals", "start": 1995, "end": None},
        {"musician": "Michael Pinnella", "role": "Keyboards / Backing Vocals", "start": 1994, "end": None},
        {"musician": "Michael LePond", "role": "Bass / Backing Vocals", "start": 1998, "end": None},
        {"musician": "Jason Rullo", "role": "Drums", "start": 1994, "end": None},
    ],
    "haken": [
        {"musician": "Ross Jennings", "role": "Lead Vocals", "start": 2007, "end": None},
        {"musician": "Richard Henshall", "role": "Guitar / Keyboards", "start": 2007, "end": None},
        {"musician": "Charlie Griffiths", "role": "Guitar / Backing Vocals", "start": 2008, "end": None},
        {"musician": "Ray Hearne", "role": "Drums / Backing Vocals", "start": 2007, "end": None},
        {"musician": "Conner Green", "role": "Bass / Backing Vocals", "start": 2014, "end": None},
        {"musician": "Diego Tejeida", "role": "Keyboards / Backing Vocals", "start": 2007, "end": 2021},
        {"musician": "Peter Jones", "role": "Keyboards / Backing Vocals", "start": 2022, "end": None},
        {"musician": "Thomas MacLean", "role": "Bass", "start": 2007, "end": 2013},
    ],
    "devin townsend": [
        {"musician": "Devin Townsend", "role": "Lead Vocals / Guitar", "start": 1997, "end": None},
        {"musician": "Mike St-Jean", "role": "Keyboards / Percussion", "start": 2014, "end": 2022},
        {"musician": "Ryan Van Poederooyen", "role": "Drums", "start": 2009, "end": 2018},
        {"musician": "Brian Waddell", "role": "Bass", "start": 2009, "end": 2018},
        {"musician": "Dave Young", "role": "Guitar / Keys", "start": 2009, "end": 2018},
        {"musician": "Darby Todd", "role": "Drums", "start": 2022, "end": None},
        {"musician": "Diego Tejeida", "role": "Keyboards", "start": 2022, "end": None},
    ],
    "primus": [
        {"musician": "Les Claypool", "role": "Lead Vocals / Bass", "start": 1984, "end": None},
        {"musician": "Larry LaLonde", "role": "Guitar", "start": 1989, "end": None},
        {"musician": "Tim Alexander", "role": "Drums", "start": 1989, "end": 2024},
        {"musician": "Jay Lane", "role": "Drums", "start": 2010, "end": 2013},
    ],
    "megadeth": [
        {"musician": "Dave Mustaine", "role": "Lead Vocals / Guitar", "start": 1983, "end": None},
        {"musician": "David Ellefson", "role": "Bass", "start": 2010, "end": 2021},
        {"musician": "James LoMenzo", "role": "Bass", "start": 2021, "end": None},
        {"musician": "Kiko Loureiro", "role": "Guitar", "start": 2015, "end": 2023},
        {"musician": "Dirk Verbeuren", "role": "Drums", "start": 2016, "end": None},
        {"musician": "Chris Broderick", "role": "Guitar", "start": 2008, "end": 2014},
        {"musician": "Shawn Drover", "role": "Drums", "start": 2004, "end": 2014},
        {"musician": "Chris Adler", "role": "Drums (Session/Tour)", "start": 2015, "end": 2016},
        {"musician": "Teemu Mäntysaari", "role": "Guitar", "start": 2023, "end": None},
    ],
    "iron maiden": [
        {"musician": "Bruce Dickinson", "role": "Lead Vocals", "start": 1999, "end": None},
        {"musician": "Steve Harris", "role": "Bass / Backing Vocals", "start": 1975, "end": None},
        {"musician": "Dave Murray", "role": "Guitar", "start": 1976, "end": None},
        {"musician": "Adrian Smith", "role": "Guitar / Backing Vocals", "start": 1999, "end": None},
        {"musician": "Janick Gers", "role": "Guitar", "start": 1990, "end": None},
        {"musician": "Nicko McBrain", "role": "Drums", "start": 1982, "end": None},
    ],
    "paul mccartney": [
        {"musician": "Paul McCartney", "role": "Lead Vocals / Bass / Guitar / Piano", "start": 1970, "end": None},
        {"musician": "Rusty Anderson", "role": "Guitar / Backing Vocals", "start": 2001, "end": None},
        {"musician": "Brian Ray", "role": "Bass / Guitar / Backing Vocals", "start": 2002, "end": None},
        {"musician": "Paul Wickens", "role": "Keyboards / Accordion", "start": 1989, "end": None},
        {"musician": "Abe Laboriel Jr.", "role": "Drums / Backing Vocals", "start": 2001, "end": None},
    ],
    "animals as leaders": [
        {"musician": "Tosin Abasi", "role": "8-String Lead Guitar", "start": 2007, "end": None},
        {"musician": "Javier Reyes", "role": "8-String Rhythm Guitar", "start": 2009, "end": None},
        {"musician": "Matt Garstka", "role": "Drums", "start": 2012, "end": None},
    ],
    "between the buried and me": [
        {"musician": "Tommy Rogers", "role": "Lead Vocals / Keyboards", "start": 2000, "end": None},
        {"musician": "Paul Waggoner", "role": "Lead Guitar / Backing Vocals", "start": 2000, "end": None},
        {"musician": "Dustie Waring", "role": "Rhythm Guitar", "start": 2004, "end": None},
        {"musician": "Dan Briggs", "role": "Bass", "start": 2005, "end": None},
        {"musician": "Blake Richardson", "role": "Drums", "start": 2005, "end": None},
    ],
    "leprous": [
        {"musician": "Einar Solberg", "role": "Lead Vocals / Keyboards", "start": 2001, "end": None},
        {"musician": "Tor Oddmund Suhrke", "role": "Guitar / Backing Vocals", "start": 2001, "end": None},
        {"musician": "Baard Kolstad", "role": "Drums", "start": 2014, "end": None},
        {"musician": "Simen Børven", "role": "Bass / Backing Vocals", "start": 2015, "end": None},
        {"musician": "Robin Ognedal", "role": "Guitar / Backing Vocals", "start": 2017, "end": None},
    ],
    "ghost": [
        {"musician": "Tobias Forge (Papa Emeritus / Cardinal Copia)", "role": "Lead Vocals", "start": 2006, "end": None},
    ],
    "dio": [
        {"musician": "Ronnie James Dio", "role": "Lead Vocals", "start": 1982, "end": 2010},
        {"musician": "Craig Goldy", "role": "Guitar", "start": 2000, "end": 2010},
        {"musician": "Rudy Sarzo", "role": "Bass", "start": 2004, "end": 2010},
        {"musician": "Simon Wright", "role": "Drums", "start": 1998, "end": 2010},
        {"musician": "Scott Warren", "role": "Keyboards", "start": 1994, "end": 2010},
    ],
    "motorhead": [
        {"musician": "Lemmy Kilmister", "role": "Lead Vocals / Bass", "start": 1975, "end": 2015},
        {"musician": "Phil Campbell", "role": "Guitar", "start": 1984, "end": 2015},
        {"musician": "Mikkey Dee", "role": "Drums", "start": 1992, "end": 2015},
    ],
    "mastodon": [
        {"musician": "Troy Sanders", "role": "Bass / Vocals", "start": 2000, "end": None},
        {"musician": "Brent Hinds", "role": "Lead Guitar / Vocals", "start": 2000, "end": None},
        {"musician": "Bill Kelliher", "role": "Rhythm Guitar / Backing Vocals", "start": 2000, "end": None},
        {"musician": "Brann Dailor", "role": "Drums / Vocals", "start": 2000, "end": None},
    ],
    "tesseract": [
        {"musician": "Daniel Tompkins", "role": "Lead Vocals", "start": 2014, "end": None},
        {"musician": "Acle Kahney", "role": "Lead Guitar", "start": 2003, "end": None},
        {"musician": "James Monteith", "role": "Rhythm Guitar", "start": 2006, "end": None},
        {"musician": "Amos Williams", "role": "Bass / Backing Vocals", "start": 2006, "end": None},
        {"musician": "Jay Postones", "role": "Drums", "start": 2005, "end": None},
    ],
    "katatonia": [
        {"musician": "Jonas Renkse", "role": "Lead Vocals", "start": 1991, "end": None},
        {"musician": "Anders Nyström", "role": "Guitar / Backing Vocals", "start": 1991, "end": None},
        {"musician": "Niklas Sandin", "role": "Bass", "start": 2009, "end": None},
        {"musician": "Daniel Moilanen", "role": "Drums", "start": 2015, "end": None},
        {"musician": "Roger Öjersson", "role": "Guitar / Vocals", "start": 2016, "end": None},
    ],
    "nevermore": [
        {"musician": "Warrel Dane", "role": "Lead Vocals", "start": 1991, "end": 2011},
        {"musician": "Jeff Loomis", "role": "Lead Guitar", "start": 1991, "end": 2011},
        {"musician": "Jim Sheppard", "role": "Bass", "start": 1991, "end": 2011},
        {"musician": "Van Williams", "role": "Drums", "start": 1994, "end": 2011},
    ],
    "fear factory": [
        {"musician": "Burton C. Bell", "role": "Lead Vocals", "start": 1989, "end": 2020},
        {"musician": "Dino Cazares", "role": "Guitar", "start": 2009, "end": None},
        {"musician": "Gene Hoglan", "role": "Drums", "start": 2009, "end": 2012},
        {"musician": "Byron Stroud", "role": "Bass", "start": 2003, "end": 2012},
    ],
    "a sound of thunder": [
        {"musician": "Nina Osegueda", "role": "Lead Vocals", "start": 2009, "end": None},
        {"musician": "Josh Schwartz", "role": "Guitar", "start": 2009, "end": None},
        {"musician": "Jesse Keen", "role": "Bass / Keys", "start": 2010, "end": None},
        {"musician": "Chris Haren", "role": "Drums", "start": 2009, "end": None},
    ],
    "iris divine": [
        {"musician": "Navid Rashid", "role": "Lead Vocals / Guitar", "start": 2008, "end": None},
        {"musician": "Brian Dobbs", "role": "Bass", "start": 2008, "end": None},
        {"musician": "Kris Combs", "role": "Drums", "start": 2008, "end": None},
    ],
    "eyes of the nile": [
        {"musician": "Eyes of the Nile (Iron Maiden Tribute)", "role": "Full Lineup", "start": 2010, "end": None},
    ]
}

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

