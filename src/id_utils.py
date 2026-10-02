import uuid
import hashlib
from typing import Optional

# Base namespace UUID for deterministic offline IDs across the concerts application
CONCERTS_NAMESPACE = uuid.UUID('9e32a4e2-6b97-4b71-9df6-7b26d8ce0870')

def generate_offline_artist_id(name: str) -> str:
    """Generates a deterministic offline ID for an artist without a MusicBrainz MBID."""
    norm = (name or "").strip().lower()
    return f"offline:artist:{uuid.uuid5(CONCERTS_NAMESPACE, f'artist:{norm}')}"

def generate_offline_album_id(artist_id: str, clean_title: str) -> str:
    """Generates a deterministic offline ID for an album without a MusicBrainz Release Group MBID."""
    art_norm = (artist_id or "").strip()
    title_norm = (clean_title or "").strip().lower()
    return f"offline:album:{uuid.uuid5(CONCERTS_NAMESPACE, f'album:{art_norm}:{title_norm}')}"

def generate_offline_song_id(artist_id: str, clean_title: str) -> str:
    """Generates a deterministic offline ID for a song without a MusicBrainz Recording MBID."""
    art_norm = (artist_id or "").strip()
    title_norm = (clean_title or "").strip().lower()
    return f"offline:song:{uuid.uuid5(CONCERTS_NAMESPACE, f'song:{art_norm}:{title_norm}')}"

def generate_offline_venue_id(name: str, city: str = '', state: str = '', country: str = '') -> str:
    """Generates a deterministic offline ID for a venue without a Setlist.fm Venue ID."""
    components = [
        (name or "").strip().lower(),
        (city or "").strip().lower(),
        (state or "").strip().lower(),
        (country or "").strip().lower(),
    ]
    key = ":".join(components)
    return f"offline:venue:{uuid.uuid5(CONCERTS_NAMESPACE, f'venue:{key}')}"

def generate_offline_concert_id(date_str: str, venue_id: str, primary_artist_id: str, user_id: Optional[int] = None) -> str:
    """Generates a deterministic offline ID for a custom/offline concert."""
    d_norm = (date_str or "").strip()
    v_norm = (venue_id or "").strip()
    a_norm = (primary_artist_id or "").strip()
    u_norm = str(user_id) if user_id is not None else "global"
    return f"offline:concert:{uuid.uuid5(CONCERTS_NAMESPACE, f'concert:{u_norm}:{d_norm}:{v_norm}:{a_norm}')}"

def generate_offline_concert_artist_id(concert_id: str, artist_id: str) -> str:
    """Generates a deterministic offline ID for a concert artist appearance."""
    c_norm = (str(concert_id) or "").strip()
    a_norm = (str(artist_id) or "").strip()
    return f"offline:ca:{uuid.uuid5(CONCERTS_NAMESPACE, f'ca:{c_norm}:{a_norm}')}"

def is_offline_id(entity_id: Optional[str]) -> bool:
    """Returns True if the ID is a synthetic offline identifier."""
    if not entity_id:
        return False
    return str(entity_id).startswith("offline:")
