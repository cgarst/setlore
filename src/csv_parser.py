import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import requests
from src.config import IGNORED_ARTISTS

def parse_date(date_str: str) -> Optional[datetime]:
    if not date_str or not date_str.strip():
        return None
    s = date_str.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year < 1970:
                dt = dt.replace(year=dt.year + 100)
            return dt
        except ValueError:
            pass
    return None

def is_ignored_artist(name: str, ignored_list: List[str]) -> bool:
    if not name:
        return False
    norm = name.strip().lower()
    for ign in ignored_list:
        if ign.strip().lower() == norm or ign.strip().lower() in norm:
            return True
    return False

CANONICAL_ARTIST_NAMES: Dict[str, str] = {}

def normalize_artist_name(name: str) -> str:
    if not name:
        return ""
    # Strip VIP, Meet & Greet, Acoustic suffixes
    cleaned = re.sub(r'\s*\((?:vip|vip\s+.*|meet\s*&\s*greet|acoustic|guest)\)', '', name.strip(), flags=re.IGNORECASE).strip()
    return CANONICAL_ARTIST_NAMES.get(cleaned.lower(), cleaned)

def clean_artists_string(artists_raw: str, ignored_list: Optional[List[str]] = None) -> List[str]:
    if not artists_raw:
        return []
    if ignored_list is None:
        ignored_list = IGNORED_ARTISTS

    raw = artists_raw.strip()
    prefix_match = re.match(r"^([^:]+):\s*(.*)$", raw)
    if prefix_match:
        artists_part = prefix_match.group(2).strip()
        raw = artists_part

    tokens = [normalize_artist_name(t) for t in raw.split(",") if t.strip()]
    filtered = [t for t in tokens if not is_ignored_artist(t, ignored_list)]
    return filtered

def parse_csv_rows(reader, ignored_list: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if ignored_list is None:
        ignored_list = IGNORED_ARTISTS

    records = []
    first_row = next(reader, None)
    if first_row is None:
        return []

    header_indices = {}
    is_header = False
    for idx, col in enumerate(first_row):
        col_clean = (col or "").strip().lower()
        if any(k in col_clean for k in ['date', 'artist', 'band', 'venue', 'setlist', 'song', 'track', 'note', 'tour']):
            is_header = True
            if 'date' in col_clean and 'date' not in header_indices:
                header_indices['date'] = idx
            elif any(k in col_clean for k in ['artist', 'band', 'performer']) and 'artists' not in header_indices:
                header_indices['artists'] = idx
            elif any(k in col_clean for k in ['venue', 'location']) and 'venue' not in header_indices:
                header_indices['venue'] = idx
            elif any(k in col_clean for k in ['setlist', 'song', 'track']) and 'setlist' not in header_indices:
                header_indices['setlist'] = idx
            elif any(k in col_clean for k in ['note', 'tour', 'comment']) and 'notes' not in header_indices:
                header_indices['notes'] = idx

    rows_to_process = []
    if is_header:
        for r in reader:
            rows_to_process.append(r)
    else:
        rows_to_process.append(first_row)
        for r in reader:
            rows_to_process.append(r)

    row_id = 0
    for row in rows_to_process:
        if not row or not any(row):
            continue
        row_id += 1

        def get_col(field, default_idx):
            idx = header_indices.get(field, default_idx)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ""

        date_raw = get_col('date', 0)
        artists_raw = get_col('artists', 1)
        venue_raw = get_col('venue', 2)

        # Setlist column detection
        setlist_raw = ""
        if 'setlist' in header_indices:
            setlist_raw = get_col('setlist', None)
        elif len(row) >= 4:
            setlist_raw = row[3].strip()

        notes_raw = get_col('notes', None)

        if not date_raw and not artists_raw and not venue_raw:
            continue

        dt = parse_date(date_raw)
        artists = clean_artists_string(artists_raw, ignored_list)

        # If all artists in row were ignored, skip row entirely
        if not artists and artists_raw:
            continue

        display_date = dt.strftime("%m-%d-%Y") if dt else date_raw

        records.append({
            "id": f"row_{row_id}",
            "raw_date": display_date,
            "date": dt.strftime("%d-%m-%Y") if dt else None,
            "display_date": display_date,
            "date_obj": dt,
            "year": dt.year if dt else None,
            "raw_artists": ", ".join(artists) if artists else artists_raw,
            "artists": artists,
            "primary_artist": artists[0] if artists else artists_raw,
            "venue": venue_raw,
            "notes": notes_raw,
            "setlist": setlist_raw
        })
    return records

def parse_concerts_source(source: str, ignored_list: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    if source.startswith("https://docs.google.com/spreadsheets/"):
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", source)
        if not match:
            raise ValueError(f"Could not parse spreadsheet ID from URL: {source}")
        sheet_id = match.group(1)
        gid = "0"
        gid_match = re.search(r"[#&?]gid=([0-9]+)", source)
        if gid_match:
            gid = gid_match.group(1)
            
        export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
        try:
            res = requests.get(export_url, timeout=10)
            if res.status_code == 200:
                print(f"      Successfully fetched live data from Google Sheets!")
                return parse_csv_rows(csv.reader(io.StringIO(res.text)), ignored_list)
            elif res.status_code == 401 or res.status_code == 403:
                print(f"      [!] Google Sheet requires permission (HTTP {res.status_code}).")
                print("      Please set Sheet sharing to 'Anyone with the link can view' or use local CSV.")
        except Exception as e:
            print(f"      Failed to fetch from Google Sheets: {e}")

    p = Path(source)
    if not p.exists():
        from src.config import DEFAULT_CSV_PATH
        if DEFAULT_CSV_PATH and DEFAULT_CSV_PATH.exists():
            print(f"      Falling back to local file: {DEFAULT_CSV_PATH.name}")
            p = DEFAULT_CSV_PATH
        else:
            raise FileNotFoundError(f"Source file not found: {source}")

    with open(p, mode="r", encoding="utf-8-sig") as f:
        return parse_csv_rows(csv.reader(f), ignored_list)
