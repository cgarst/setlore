import json
import time
import urllib.parse
from datetime import datetime
from typing import Dict, Any, List, Optional
import requests
from rapidfuzz import fuzz

from src.config import SETLISTFM_API_KEY


def _get_api_cache(cache_key: str) -> Optional[Any]:
    """Retrieves a cached JSON payload from the ApiCache database model."""
    try:
        from apps.catalog.models import ApiCache
        entry = ApiCache.objects.filter(cache_key=cache_key).first()
        if entry:
            return entry.payload
    except Exception:
        pass
    return None


def _set_api_cache(cache_key: str, endpoint: str, payload: Any):
    """Stores a JSON payload into the ApiCache database model."""
    try:
        from apps.catalog.models import ApiCache
        ApiCache.objects.update_or_create(
            cache_key=cache_key,
            defaults={'endpoint': endpoint, 'payload': payload}
        )
    except Exception:
        pass


def _delete_api_cache(cache_key: str):
    """Deletes a cached payload from ApiCache."""
    try:
        from apps.catalog.models import ApiCache
        ApiCache.objects.filter(cache_key=cache_key).delete()
    except Exception:
        pass


class SetlistFMClient:
    BASE_URL = "https://api.setlist.fm/rest/1.0"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or SETLISTFM_API_KEY
        if not self.api_key:
            raise ValueError("Setlist.fm API key is missing. Add setlistfm_key to .env")
        self.headers = {
            "x-api-key": self.api_key,
            "Accept": "application/json"
        }
        self.last_request_time = 0.0
        self.min_interval = 0.6  # Rate limit: ~1.6 req/sec to be safe under 2 req/s limit

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        self._rate_limit()
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"
        max_retries = 4
        for attempt in range(max_retries):
            try:
                res = requests.get(url, headers=self.headers, params=params, timeout=15)
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 404:
                    return None
                elif res.status_code == 429:
                    wait_time = 2 ** (attempt + 1)
                    print(f"Rate limited by Setlist.fm (429). Waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Setlist.fm API error ({res.status_code}) for {url}: {res.text[:200]}")
                    return None
            except Exception as e:
                print(f"Request error: {e}")
                time.sleep(1)
        return None

    def get_user_attended(self, username: str, use_cache: bool = True) -> List[Dict[str, Any]]:
        clean_user = username.strip().lower()
        cache_key = f"setlistfm_user_{clean_user}"
        cached_data = _get_api_cache(cache_key) if use_cache else None

        # Check live page 1 to verify total count
        page1_data = self._get(f"user/{username}/attended", params={"p": 1})
        if page1_data:
            live_total = page1_data.get("total", 0)
            page1_setlists = page1_data.get("setlist", [])

            # If cached count matches live total and caching requested, return cached data
            if use_cache and cached_data and len(cached_data) == live_total:
                return cached_data

            print(f"Syncing attended setlists for '{username}' (Live Total: {live_total}, Cached: {len(cached_data) if cached_data else 0})...")
            all_setlists = list(page1_setlists)
            items_per_page = page1_data.get("itemsPerPage", 20)

            page = 2
            while len(all_setlists) < live_total:
                data = self._get(f"user/{username}/attended", params={"p": page})
                if not data:
                    break
                setlists = data.get("setlist", [])
                if not setlists:
                    break
                all_setlists.extend(setlists)
                print(f"  Fetched page {page} ({len(all_setlists)}/{live_total} setlists)...")
                page += 1

            _set_api_cache(cache_key, "user_attended", all_setlists)
            for s in all_setlists:
                s_id = s.get("id")
                if s_id:
                    _set_api_cache(f"setlistfm_sl_{s_id}", "setlist", s)

            return all_setlists

        # Fallback to cached if offline/API fails
        if cached_data:
            return cached_data
        return []

    def get_setlist_by_id(self, setlist_id: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        cache_key = f"setlistfm_sl_{setlist_id.strip()}"
        if use_cache:
            cached = _get_api_cache(cache_key)
            if cached is not None:
                return cached

        data = self._get(f"setlist/{setlist_id.strip()}")
        if data:
            _set_api_cache(cache_key, "setlist", data)
        return data

    def search_setlists(self, artist_name: Optional[str] = None, date_str: Optional[str] = None,
                         year: Optional[int] = None, venue_name: Optional[str] = None,
                         use_cache: bool = True) -> List[Dict[str, Any]]:
        query_key = f"{artist_name or ''}_{date_str or ''}_{year or ''}_{venue_name or ''}"
        safe_key = "".join(c if c.isalnum() else "_" for c in query_key)
        cache_key = f"setlistfm_search_{safe_key}"

        if use_cache:
            cached = _get_api_cache(cache_key)
            if cached is not None:
                return cached

        params = {}
        if artist_name:
            params["artistName"] = artist_name
        if date_str:
            params["date"] = date_str
        if year:
            params["year"] = year
        if venue_name:
            params["venueName"] = venue_name

        data = self._get("search/setlists", params=params)
        setlists = data.get("setlist", []) if data else []
        _set_api_cache(cache_key, "search_setlists", setlists)
        return setlists
