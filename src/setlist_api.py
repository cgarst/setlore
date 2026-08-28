import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests
from rapidfuzz import fuzz

from src.config import SETLISTFM_API_KEY, SETLIST_CACHE_DIR, USER_CACHE_DIR

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
        cache_file = USER_CACHE_DIR / f"{username}_attended.json"
        cached_data = None
        if use_cache and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
            except Exception as e:
                print(f"Error reading user cache: {e}")

        # Always check live page 1 to verify total count and check for newly tagged shows
        page1_data = self._get(f"user/{username}/attended", params={"p": 1})
        if page1_data:
            live_total = page1_data.get("total", 0)
            page1_setlists = page1_data.get("setlist", [])
            
            # If cached count matches live total, cache is 100% up to date!
            if cached_data and len(cached_data) == live_total:
                return cached_data

            # If total changed (e.g. user tagged a new show), fetch remaining pages
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

            # Cache full attended list
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(all_setlists, f, indent=2)

            # Also cache individual setlists
            for s in all_setlists:
                s_id = s.get("id")
                if s_id:
                    s_cache_file = SETLIST_CACHE_DIR / f"{s_id}.json"
                    if not s_cache_file.exists():
                        with open(s_cache_file, "w", encoding="utf-8") as f:
                            json.dump(s, f, indent=2)

            return all_setlists

        # Fallback to cached if offline/API fails
        if cached_data:
            return cached_data
        return []

    def get_setlist_by_id(self, setlist_id: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        cache_file = SETLIST_CACHE_DIR / f"{setlist_id}.json"
        if use_cache and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        data = self._get(f"setlist/{setlist_id}")
        if data:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        return data

    def search_setlists(self, artist_name: Optional[str] = None, date_str: Optional[str] = None,
                         year: Optional[int] = None, venue_name: Optional[str] = None,
                         use_cache: bool = True) -> List[Dict[str, Any]]:
        query_key = f"{artist_name or ''}_{date_str or ''}_{year or ''}_{venue_name or ''}"
        safe_key = "".join(c if c.isalnum() else "_" for c in query_key)
        cache_file = SETLIST_CACHE_DIR / f"search_{safe_key}.json"

        if use_cache and cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

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
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(setlists, f, indent=2)
        return setlists
