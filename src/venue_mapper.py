from __future__ import annotations
from datetime import datetime
from collections import defaultdict, Counter
from typing import List, Dict, Any, Optional, Tuple

VENUE_COORDINATES: Dict[str, Tuple[float, float, str, str]] = {}
CANONICAL_VENUE_NAMES: Dict[str, str] = {}

def generate_venue_map_data(all_csv_records: List[Dict[str, Any]],
                            matched_setlists: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Extract rich venue metadata directly from matched Setlist.fm setlists
    sl_venue_info: Dict[str, Dict[str, Any]] = {}
    for p in matched_setlists:
        sl = p.get('setlist')
        if sl:
            v_obj = sl.get('venue', {})
            v_name = v_obj.get('name', '').strip()
            city_obj = v_obj.get('city', {})
            city_name = city_obj.get('name', '').strip()
            state_code = city_obj.get('stateCode') or city_obj.get('state', '').strip()
            country_obj = city_obj.get('country', {})
            country_name = country_obj.get('name', 'United States').strip() if isinstance(country_obj, dict) else (country_obj or 'United States')
            city_coords = city_obj.get('coords', {})
            lat = city_coords.get('lat')
            lng = city_coords.get('long')

            loc_str = f"{city_name}, {state_code}" if city_name and state_code else (city_name or country_name)
            
            info = {
                'name': v_name,
                'lat': lat,
                'lng': lng,
                'location': loc_str,
                'country': country_name
            }
            if v_name:
                sl_venue_info[v_name.lower().strip()] = info
            csv_rec = p.get('csv', {})
            csv_venue = csv_rec.get('venue', '').strip()
            if csv_venue:
                sl_venue_info[csv_venue.lower().strip()] = info

    venue_concerts = defaultdict(list)
    venue_artists = defaultdict(Counter)

    for rec in all_csv_records:
        raw_venue = rec.get('venue', '').strip()
        if not raw_venue:
            continue
        # Use canonical name from Setlist.fm if matched, otherwise raw name
        v_matched = sl_venue_info.get(raw_venue.lower())
        venue = v_matched['name'] if v_matched and v_matched.get('name') else CANONICAL_VENUE_NAMES.get(raw_venue.lower(), raw_venue)
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

    venues_list = []
    cities_set = set()
    countries_set = set()

    for venue_name, concerts in venue_concerts.items():
        v_key = venue_name.lower().strip()
        lat = None
        lng = None
        loc_name = "United States"
        country = "United States"

        if v_key in VENUE_COORDINATES:
            lat, lng, loc_name, country = VENUE_COORDINATES[v_key]
        elif v_key in sl_venue_info and sl_venue_info[v_key].get('lat') is not None:
            v_inf = sl_venue_info[v_key]
            lat = v_inf['lat']
            lng = v_inf['lng']
            loc_name = v_inf['location']
            country = v_inf['country']
        else:
            try:
                from apps.catalog.models import Venue
                v_db = Venue.objects.filter(name__iexact=venue_name).first()
                if v_db and v_db.latitude is not None and v_db.longitude is not None:
                    lat = v_db.latitude
                    lng = v_db.longitude
                    loc_name = f"{v_db.city}, {v_db.state}" if v_db.city and v_db.state else (v_db.city or venue_name)
                    country = v_db.country or 'United States'
                elif v_db and v_db.city:
                    loc_name = f"{v_db.city}, {v_db.state}" if v_db.state else v_db.city
                    country = v_db.country or 'United States'
            except Exception:
                pass

            if lat is None or lng is None:
                # Attempt OpenStreetMap Nominatim geocoding
                try:
                    from apps.concerts.utils import resolve_venue_coordinates
                    n_lat, n_lng, _ = resolve_venue_coordinates(venue_name)
                    if n_lat is not None and n_lng is not None:
                        lat = n_lat
                        lng = n_lng
                except Exception:
                    pass

            if lat is None or lng is None:
                lat, lng, loc_name, country = (38.9072, -77.0369, 'Washington, DC', 'United States')

        cities_set.add(loc_name)
        countries_set.add(country)

        sorted_concerts = sorted(
            concerts,
            key=lambda c: str(c.get('_date_obj') or ""),
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
