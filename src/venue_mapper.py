from datetime import datetime
from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional

VENUE_COORDINATES = {
    '9:30 club': (38.9174, -77.0238, 'Washington, DC', 'United States'),
    'arlenes grocery': (40.7208, -73.9882, 'New York, NY', 'United States'),
    'baker park bandshell': (39.4144, -77.4189, 'Frederick, MD', 'United States'),
    'blue fox': (38.7512, -77.4728, 'Winchester, VA', 'United States'),
    'blues alley': (38.9048, -77.0601, 'Washington, DC', 'United States'),
    'cafe 611': (39.4194, -77.4082, 'Frederick, MD', 'United States'),
    'center stage': (33.7911, -84.3892, 'Atlanta, GA', 'United States'),
    'chevy amphitheater': (40.4355, -80.0076, 'Pittsburgh, PA', 'United States'),
    'club orpheus': (39.2904, -76.6062, 'Baltimore, MD', 'United States'),
    'd.a.r.': (38.8927, -77.0401, 'Washington, DC', 'United States'),
    'dar': (38.8927, -77.0401, 'Washington, DC', 'United States'),
    'empire': (38.8744, -77.2183, 'Springfield, VA', 'United States'),
    'hilton lake las vegas': (36.1081, -114.9317, 'Henderson, NV', 'United States'),
    'hippodrome': (39.2892, -76.6214, 'Baltimore, MD', 'United States'),
    'howard theater': (38.9152, -77.0211, 'Washington, DC', 'United States'),
    'howard theatre': (38.9152, -77.0211, 'Washington, DC', 'United States'),
    "jammin' java": (38.8997, -77.2625, 'Vienna, VA', 'United States'),
    'jaxx': (38.7758, -77.1858, 'Springfield, VA', 'United States'),
    'jiffy lube live': (38.8021, -77.6044, 'Bristow, VA', 'United States'),
    'lincoln theater': (38.9174, -77.0261, 'Washington, DC', 'United States'),
    'magfest': (38.7828, -77.0163, 'National Harbor, MD', 'United States'),
    'merriweather': (39.2093, -76.8617, 'Columbia, MD', 'United States'),
    'metro gallery': (39.3090, -76.6166, 'Baltimore, MD', 'United States'),
    'meyerhoff': (39.3054, -76.6201, 'Baltimore, MD', 'United States'),
    'nation': (38.8753, -77.0069, 'Washington, DC', 'United States'),
    'nationals field': (38.8730, -77.0074, 'Washington, DC', 'United States'),
    'northwest stadium': (38.9076, -76.8644, 'Landover, MD', 'United States'),
    'ottobar': (39.3218, -76.6214, 'Baltimore, MD', 'United States'),
    'pier six': (39.2847, -76.6042, 'Baltimore, MD', 'United States'),
    'rfk stadium': (38.8898, -76.9720, 'Washington, DC', 'United States'),
    'rams head live': (39.2894, -76.6080, 'Baltimore, MD', 'United States'),
    'rams head live!': (39.2894, -76.6080, 'Baltimore, MD', 'United States'),
    'rams head on stage': (38.9774, -76.4925, 'Annapolis, MD', 'United States'),
    'royal albert hall': (51.5009, -0.1774, 'London, UK', 'United Kingdom'),
    'sirbaugh acres': (39.4678, -78.4719, 'Capon Bridge, WV', 'United States'),
    'sound stage': (39.2878, -76.6067, 'Baltimore, MD', 'United States'),
    'soundstage': (39.2878, -76.6067, 'Baltimore, MD', 'United States'),
    'state theater': (38.8824, -77.1711, 'Falls Church, VA', 'United States'),
    'state theatre': (38.8824, -77.1711, 'Falls Church, VA', 'United States'),
    'tally ho': (39.1157, -77.5644, 'Leesburg, VA', 'United States'),
    'terminal 5': (40.7697, -73.9928, 'New York, NY', 'United States'),
    'the anthem': (38.8804, -77.0270, 'Washington, DC', 'United States'),
    'the fillmore': (38.9912, -77.0267, 'Silver Spring, MD', 'United States'),
    'the lyric': (39.3045, -76.6198, 'Baltimore, MD', 'United States'),
    'verizon center': (38.8981, -77.0209, 'Washington, DC', 'United States'),
    'warner theater': (38.8966, -77.0289, 'Washington, DC', 'United States'),
    'warner theatre': (38.8966, -77.0289, 'Washington, DC', 'United States'),
    'weinberg': (39.4144, -77.4116, 'Frederick, MD', 'United States')
}

def generate_venue_map_data(all_csv_records: List[Dict[str, Any]],
                            matched_setlists: List[Dict[str, Any]]) -> Dict[str, Any]:
    venue_concerts = defaultdict(list)
    venue_artists = defaultdict(Counter)

    for rec in all_csv_records:
        venue = rec.get('venue', '').strip()
        if not venue:
            continue
        venue_concerts[venue].append({
            'id': rec['id'],
            'date': rec.get('display_date', rec.get('raw_date', '')),
            '_date_obj': rec.get('date_obj'),
            'year': rec.get('year'),
            'artists': rec.get('artists', []),
            'raw_artists': rec.get('raw_artists', '')
        })
        for a in rec.get('artists', []):
            venue_artists[venue][a] += 1

    sl_venue_coords = {}
    for p in matched_setlists:
        sl = p.get('setlist')
        if sl:
            v_obj = sl.get('venue', {})
            v_name = v_obj.get('name', '')
            city_coords = v_obj.get('city', {}).get('coords', {})
            if v_name and city_coords.get('lat') and city_coords.get('long'):
                sl_venue_coords[v_name.lower()] = (city_coords.get('lat'), city_coords.get('long'))

    venues_list = []
    cities_set = set()
    countries_set = set()

    for venue_name, concerts in venue_concerts.items():
        v_key = venue_name.lower().strip()
        if v_key in VENUE_COORDINATES:
            lat, lng, loc_name, country = VENUE_COORDINATES[v_key]
        elif v_key in sl_venue_coords:
            lat, lng = sl_venue_coords[v_key]
            loc_name = 'United States'
            country = 'United States'
        else:
            lat, lng, loc_name, country = (38.9072, -77.0369, 'Washington, DC', 'United States')

        cities_set.add(loc_name)
        countries_set.add(country)

        sorted_concerts = sorted(
            concerts,
            key=lambda c: c.get('_date_obj') if c.get('_date_obj') else datetime.min,
            reverse=True
        )

        clean_sorted_concerts = [
            {k: v for k, v in c.items() if not k.startswith('_')}
            for c in sorted_concerts
        ]

        top_artists_list = [
            {'artist': a, 'count': cnt}
            for a, cnt in venue_artists[venue_name].most_common(5)
        ]

        venues_list.append({
            'name': venue_name,
            'location': loc_name,
            'country': country,
            'lat': lat,
            'lng': lng,
            'concert_count': len(concerts),
            'concerts': clean_sorted_concerts,
            'top_artists': top_artists_list
        })

    venues_list.sort(key=lambda v: v['concert_count'], reverse=True)

    return {
        'venues': venues_list,
        'total_venues': len(venues_list),
        'total_concerts': len(all_csv_records),
        'unique_cities_count': len(cities_set),
        'unique_countries_count': len(countries_set),
        'top_venue': venues_list[0] if venues_list else None
    }
