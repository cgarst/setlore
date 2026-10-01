import re
import math
import urllib.parse
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import requests
from django.conf import settings
from apps.catalog.models import ApiCache
from apps.concerts.models import Concert, ConcertArtist
from apps.concerts.utils import resolve_venue_coordinates

logger = logging.getLogger(__name__)

BANDSINTOWN_API_URL = "https://rest.bandsintown.com/artists/{artist}/events"

def normalize_artist_name(name: str) -> str:
    """Normalize artist name for comparison and caching."""
    if not name:
        return ""
    # Normalize common variations
    s = name.strip().lower()
    s = re.sub(r'^(the|a|an)\s+', '', s)
    s = re.sub(r'[^\w\s]', '', s)
    return re.sub(r'\s+', ' ', s).strip()

def format_relative_date(dt: datetime) -> str:
    """Format a relative date label (e.g. 'Today', 'Tomorrow', 'In 5 days', 'In 3 weeks')."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    diff_sec = (dt - now).total_seconds()
    days = int(round(diff_sec / 86400.0))
    if diff_sec > 0 and days == 0:
        days = 1
    
    if days < 0:
        return "Past show"
    elif days == 0:
        return "Today"
    elif days == 1:
        return "Tomorrow"
    elif days < 14:
        return f"In {days} days"
    elif days < 30:
        weeks = days // 7
        return f"In {weeks} weeks"
    elif days < 60:
        return "In 1 month"
    else:
        months = days // 30
        return f"In {months} months"

def haversine_distance_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great circle distance between two lat/lon coordinates in miles."""
    try:
        r = 3958.8  # Earth radius in miles
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        delta_phi = math.radians(float(lat2) - float(lat1))
        delta_lambda = math.radians(float(lon2) - float(lon1))
        a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return r * c
    except (ValueError, TypeError):
        return 999999.0

def fetch_bandsintown_events(artist_name: str, app_id: Optional[str] = None, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Fetch upcoming events for a single artist from Bandsintown API.
    Handles caching via ApiCache model and graceful fallbacks.
    """
    if not artist_name or not artist_name.strip():
        return []
    
    clean_name = artist_name.strip()
    norm_key = normalize_artist_name(clean_name)
    cache_key = f"bandsintown_events_{norm_key}"
    
    # 1. Check database API cache (valid for 24 hours, unless force_refresh)
    if not force_refresh:
        cache_entry = ApiCache.objects.filter(cache_key=cache_key).first()
        if cache_entry and cache_entry.updated_at:
            age = datetime.now(timezone.utc) - cache_entry.updated_at
            if age < timedelta(hours=24):
                return cache_entry.payload if isinstance(cache_entry.payload, list) else []
    
    # 2. Query Bandsintown API
    actual_app_id = app_id or getattr(settings, 'BANDSINTOWN_APP_ID', '12345') or '12345'
    encoded_artist = urllib.parse.quote(clean_name, safe='')
    url = f"https://rest.bandsintown.com/artists/{encoded_artist}/events?app_id={actual_app_id}&date=upcoming"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)',
        'Accept': 'application/json',
    }
    
    events_data = []
    try:
        response = requests.get(url, headers=headers, timeout=5.0)
        if response.status_code == 200:
            json_resp = response.json()
            if isinstance(json_resp, list):
                events_data = json_resp
            elif isinstance(json_resp, dict) and "errors" not in json_resp and "Message" not in json_resp:
                events_data = [json_resp]
        else:
            logger.debug("Bandsintown API returned %s for artist '%s'", response.status_code, clean_name)
    except Exception as e:
        logger.debug("Error requesting Bandsintown API for '%s': %s", clean_name, e)
    
    # Cache the result (even if empty to avoid hammering on 404/empty)
    try:
        ApiCache.objects.update_or_create(
            cache_key=cache_key,
            defaults={
                'endpoint': 'bandsintown_events',
                'payload': events_data
            }
        )
    except Exception as e:
        logger.debug("Could not cache Bandsintown events: %s", e)
    
    return events_data

def parse_event_item(event: Dict[str, Any], artist_name: str, times_seen: int = 1,
                     user_lat: Optional[float] = None, user_lon: Optional[float] = None,
                     user_loc_query: str = "") -> Optional[Dict[str, Any]]:
    """Parse raw Bandsintown event object into a clean structured display dict."""
    if not isinstance(event, dict):
        return None
    
    dt_raw = event.get('datetime', '') or event.get('starts_at', '')
    dt_obj = None
    date_display = ''
    time_display = ''
    days_until = 999
    rel_badge = ''
    
    if dt_raw:
        try:
            # Handle ISO format like 2026-10-15T20:00:00 or 2026-10-15T20:00:00Z
            clean_dt = dt_raw.replace('Z', '+00:00')
            dt_obj = datetime.fromisoformat(clean_dt)
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)
            date_display = dt_obj.strftime("%b %d, %Y")
            time_display = dt_obj.strftime("%I:%M %p").lstrip('0')
            rel_badge = format_relative_date(dt_obj)
            now = datetime.now(timezone.utc)
            diff_sec = (dt_obj - now).total_seconds()
            days_until = int(round(diff_sec / 86400.0))
            if diff_sec > 0 and days_until == 0:
                days_until = 1
        except Exception:
            date_display = str(dt_raw)[:10]
            rel_badge = "Upcoming"
    
    venue = event.get('venue', {}) or {}
    venue_name = venue.get('name', '') or 'TBA'
    city = venue.get('city', '') or ''
    region = venue.get('region', '') or ''
    country = venue.get('country', '') or ''
    
    loc_parts = [p for p in [city, region, country] if p]
    location_display = ", ".join(loc_parts) if loc_parts else "Location TBA"
    
    # Distance calculation
    venue_lat = None
    venue_lon = None
    try:
        if venue.get('latitude') is not None and venue.get('longitude') is not None:
            venue_lat = float(venue['latitude'])
            venue_lon = float(venue['longitude'])
    except (ValueError, TypeError):
        pass

    distance_miles = None
    distance_display = ''
    if user_lat is not None and user_lon is not None:
        if venue_lat is not None and venue_lon is not None:
            dist = haversine_distance_miles(user_lat, user_lon, venue_lat, venue_lon)
            distance_miles = round(dist, 1)
            distance_display = f"{int(round(dist))} mi away" if dist >= 1.0 else "< 1 mi away"
    
    # Ticket URL from offers or main url
    ticket_url = ''
    offers = event.get('offers', [])
    if isinstance(offers, list) and offers:
        for offer in offers:
            if isinstance(offer, dict) and offer.get('url'):
                ticket_url = offer.get('url')
                break
    
    event_url = event.get('url', '') or ticket_url or f"https://www.bandsintown.com/a/{urllib.parse.quote(artist_name)}"
    
    lineup = event.get('lineup', [])
    if isinstance(lineup, list):
        other_lineup = [a for a in lineup if isinstance(a, str) and a.lower() != artist_name.lower()]
    else:
        other_lineup = []
    
    title = event.get('title', '') or f"{artist_name} live at {venue_name}"
    description = event.get('description', '') or ''
    
    return {
        'id': event.get('id', f"{artist_name}_{venue_name}_{date_display}"),
        'artist': artist_name,
        'times_seen': times_seen,
        'datetime': dt_raw,
        'date_display': date_display,
        'time_display': time_display,
        'rel_badge': rel_badge,
        'days_until': days_until,
        'venue': venue_name,
        'city': city,
        'region': region,
        'country': country,
        'latitude': venue_lat,
        'longitude': venue_lon,
        'distance_miles': distance_miles,
        'distance_display': distance_display,
        'location_display': location_display,
        'event_url': event_url,
        'ticket_url': ticket_url or event_url,
        'lineup': lineup,
        'other_lineup': other_lineup,
        'title': title,
        'description': description,
    }

def get_user_seen_artists_summary(user) -> List[Dict[str, Any]]:
    """
    Get a list of all distinct artists the user has seen live, sorted by count and name.
    Includes is_hidden flag based on UserProfile.hidden_upcoming_artists.
    """
    if not user or not user.is_authenticated:
        return []
    
    hidden_list = []
    ignored_list = []
    if hasattr(user, 'profile'):
        hidden_list = user.profile.hidden_upcoming_artists or []
        ignored_list = user.profile.ignored_artists or []
    
    hidden_norm_set = {normalize_artist_name(a) for a in hidden_list if a} | {a.lower().strip() for a in hidden_list if a}
    ignored_norm_set = {normalize_artist_name(a) for a in ignored_list if a} | {a.lower().strip() for a in ignored_list if a}
    
    # Tally artist appearances from Concert and ConcertArtist
    artist_counts: Dict[str, int] = {}
    concerts = Concert.objects.filter(user=user).prefetch_related('artists__artist')
    
    for c in concerts:
        seen_in_concert = set()
        for ca in c.artists.all():
            if ca.artist and ca.artist.name:
                a_name = ca.artist.name.strip()
                if a_name and a_name.lower() not in seen_in_concert:
                    seen_in_concert.add(a_name.lower())
                    artist_counts[a_name] = artist_counts.get(a_name, 0) + 1
        
        if c.primary_artist:
            p_name = c.primary_artist.strip()
            if p_name and p_name.lower() not in seen_in_concert:
                artist_counts[p_name] = artist_counts.get(p_name, 0) + 1
    
    results = []
    for artist_name, count in sorted(artist_counts.items(), key=lambda x: (-x[1], x[0].lower())):
        norm = normalize_artist_name(artist_name)
        lower_name = artist_name.lower().strip()
        
        # Skip globally ignored artists
        if norm in ignored_norm_set or lower_name in ignored_norm_set:
            continue
        
        is_hidden = (norm in hidden_norm_set or lower_name in hidden_norm_set)
        results.append({
            'name': artist_name,
            'count': count,
            'is_hidden': is_hidden,
        })
    
    return results

def get_upcoming_shows_for_user(user, force_refresh: bool = False, limit: int = 50) -> Dict[str, Any]:
    """
    Main function to get upcoming shows for all non-hidden artists seen before by the user,
    with optional location & radius filtering.
    """
    seen_artists_summary = get_user_seen_artists_summary(user)
    
    # Artists eligible for upcoming shows (not hidden)
    eligible_artists = [a for a in seen_artists_summary if not a.get('is_hidden')]
    
    user_loc = ''
    user_radius = None
    user_lat = None
    user_lon = None

    if hasattr(user, 'profile'):
        user_loc = (user.profile.upcoming_location or '').strip()
        user_radius = user.profile.upcoming_radius_miles
        user_lat = user.profile.upcoming_latitude
        user_lon = user.profile.upcoming_longitude
        
        # If user has a location string but no resolved coordinates yet, resolve via Nominatim
        if user_loc and (user_lat is None or user_lon is None):
            res_lat, res_lon, _ = resolve_venue_coordinates(user_loc)
            if res_lat is not None and res_lon is not None:
                user_lat = res_lat
                user_lon = res_lon
                try:
                    user.profile.upcoming_latitude = res_lat
                    user.profile.upcoming_longitude = res_lon
                    user.profile.save(update_fields=['upcoming_latitude', 'upcoming_longitude'])
                except Exception:
                    pass
    
    upcoming_events = []
    
    # Query/fetch events for top seen artists
    for art_info in eligible_artists[:40]:
        artist_name = art_info['name']
        times_seen = art_info['count']
        
        events = fetch_bandsintown_events(artist_name, force_refresh=force_refresh)
        for ev in events:
            parsed = parse_event_item(
                ev,
                artist_name,
                times_seen=times_seen,
                user_lat=user_lat,
                user_lon=user_lon,
                user_loc_query=user_loc
            )
            if parsed and parsed['days_until'] >= 0:
                # Apply range filtering if specified
                if user_radius and user_radius > 0:
                    if parsed.get('distance_miles') is not None:
                        if parsed['distance_miles'] <= user_radius:
                            upcoming_events.append(parsed)
                    elif user_loc:
                        # Fallback text match when event coordinates are absent
                        loc_clean = user_loc.lower().strip()
                        city_clean = (parsed.get('city') or '').lower().strip()
                        region_clean = (parsed.get('region') or '').lower().strip()
                        if loc_clean in city_clean or loc_clean in region_clean or city_clean in loc_clean:
                            upcoming_events.append(parsed)
                else:
                    upcoming_events.append(parsed)
    
    # Sort chronologically by date
    upcoming_events.sort(key=lambda x: (x.get('days_until', 999), x.get('datetime', '')))
    
    hidden_count = sum(1 for a in seen_artists_summary if a.get('is_hidden'))
    
    return {
        'upcoming_shows': upcoming_events[:limit],
        'seen_artists': seen_artists_summary,
        'total_artists': len(seen_artists_summary),
        'eligible_artists_count': len(eligible_artists),
        'hidden_artists_count': hidden_count,
        'has_shows': len(upcoming_events) > 0,
        'user_location': user_loc,
        'user_radius_miles': user_radius,
        'user_latitude': user_lat,
        'user_longitude': user_lon,
    }
