import os
import json
import time
import re
import shutil
import sqlite3
import tarfile
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

from src.config import MB_DUMP_DIR, CONTACT_EMAIL
from src.album_enricher import clean_album_title

def clean_artist_name(name: str) -> str:
    if not name:
        return ""
    cleaned = name.strip()
    if cleaned.lower().startswith("the "):
        cleaned = cleaned[4:].strip()
    return cleaned

BASE_URL = "https://data.metabrainz.org/pub/musicbrainz/data/json-dumps"

AVAILABLE_COMPONENTS = [
    {
        "filename": "release.tar.xz",
        "name": "Releases & Tracklists (Song-to-Album Bridge)",
        "approx_size": "23 GB (~1.5 GB indexed DB)",
        "importance": "Essential / Recommended",
        "badge_class": "mb-badge-ready",
        "feature": "Song-to-Album & Release Year Enrichment",
        "why_needed": "ConcertTrakr uses this to map every song played in your concert history directly to its canonical studio album, track number, and original release year without making live API calls.",
        "default": True,
    },
    {
        "filename": "artist.tar.xz",
        "name": "Artists & Musician Tenures",
        "approx_size": "2.1 GB",
        "importance": "Optional",
        "badge_class": "mb-badge-busy",
        "feature": "Musicians & Multi-Band Lineups Tab",
        "why_needed": "ConcertTrakr uses this to populate the Musicians & Lineups tab — determining which band members (Drums, Bass, Guitar, Vocals, Keyboards) were active in the band during the exact year of each concert you attended, and tracking musicians across multiple bands.",
        "default": False,
    },
    {
        "filename": "release-group.tar.xz",
        "name": "Release Groups (Studio Albums Catalog)",
        "approx_size": "1.2 GB",
        "importance": "Optional",
        "badge_class": "mb-badge-api",
        "feature": "Album Discography & Era Visuals",
        "why_needed": "ConcertTrakr uses this to classify studio albums vs. live/compilation releases and calculate song release ages for the Albums & Visuals treemap. (Note: If you download Releases & Tracklists above, this classification is already included automatically).",
        "default": False,
    },
    {
        "filename": "recording.tar.xz",
        "name": "Recordings & Tracks",
        "approx_size": "34 MB",
        "importance": "Lightweight Alternative",
        "badge_class": "mb-badge-api",
        "feature": "Song Title Normalization",
        "why_needed": "ConcertTrakr uses this as a lightweight fallback to normalize and fuzzy-match song titles and track lengths if you choose not to download the full Releases & Tracklists archive.",
        "default": False,
    }
]

def _normalize_key(text: str) -> str:
    """Strips all non-alphanumeric characters and lowercases for fuzzy/clean DB index lookup."""
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())

class MusicBrainzDumpManager:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self.dump_dir = Path(MB_DUMP_DIR)
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dump_dir / "mb_dump.db"
        self.status_file = self.dump_dir / "status.json"
        self.latest_file = self.dump_dir / "LATEST"
        self._current_task_thread = None
        self._cancel_requested = False

    @classmethod
    def get_instance(cls) -> "MusicBrainzDumpManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = MusicBrainzDumpManager()
        return cls._instance

    def get_latest_upstream_version(self) -> Optional[str]:
        """Fetches the latest dump date tag from MetaBrainz."""
        url = f"{BASE_URL}/LATEST"
        req = urllib.request.Request(url, headers={"User-Agent": f"ConcertTrakr/1.0 ({CONTACT_EMAIL})"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    version = resp.read().decode("utf-8").strip()
                    return version
        except Exception as e:
            print(f"[MusicBrainzDump] Error checking upstream version: {e}")
        return None

    def get_status(self) -> Dict[str, Any]:
        """Returns the current state of local MusicBrainz dump files and active jobs."""
        local_version = None
        if self.latest_file.exists():
            try:
                local_version = self.latest_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        # Check existing files and compute disk usage
        files_on_disk = []
        total_bytes = 0
        if self.dump_dir.exists():
            for item in self.dump_dir.glob("*"):
                if item.is_file() and not item.name.startswith("."):
                    size = item.stat().st_size
                    total_bytes += size
                    files_on_disk.append({
                        "name": item.name,
                        "size_bytes": size,
                        "size_human": f"{size / (1024*1024):.1f} MB" if size >= 1024*1024 else f"{size / 1024:.1f} KB",
                        "modified": item.stat().st_mtime
                    })

        # Count indexed records if SQLite DB exists
        record_count = 0
        is_ready = False
        if self.db_path.exists() and self.db_path.stat().st_size > 0:
            try:
                with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COUNT(*) FROM recordings")
                    row = cur.fetchone()
                    if row:
                        record_count = row[0]
                        if record_count > 0:
                            is_ready = True
            except Exception:
                pass

        # Load persisted task status if present
        task_data = {}
        if self.status_file.exists():
            try:
                with open(self.status_file, "r", encoding="utf-8") as f:
                    task_data = json.load(f)
            except Exception:
                pass

        is_running = self._current_task_thread is not None and self._current_task_thread.is_alive()
        status_label = "not_downloaded"
        if is_running:
            status_label = task_data.get("status", "downloading")
        elif is_ready:
            status_label = "ready"
        elif task_data.get("status") == "error":
            status_label = "error"

        return {
            "status": status_label,
            "is_available": is_ready and not is_running,
            "is_running": is_running,
            "local_version": local_version,
            "upstream_version": task_data.get("upstream_version"),
            "record_count": record_count,
            "total_disk_size_mb": round(total_bytes / (1024 * 1024), 2),
            "files": files_on_disk,
            "components": AVAILABLE_COMPONENTS,
            "progress": task_data.get("progress", {}),
            "error": task_data.get("error"),
            "last_updated": task_data.get("last_updated")
        }

    def is_dump_available(self) -> bool:
        """Returns True if local SQLite dump exists and has indexed records."""
        if not self.db_path.exists():
            return False
        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT 1 FROM recordings LIMIT 1")
                return cur.fetchone() is not None
        except Exception:
            return False

    def _update_status_file(self, data: Dict[str, Any]):
        """Persists current state to status.json."""
        try:
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[MusicBrainzDump] Failed to update status file: {e}")

    def download_and_build(self, components: Optional[List[str]] = None, background: bool = True):
        """Starts background download and indexing of MusicBrainz JSON dump."""
        if self._current_task_thread and self._current_task_thread.is_alive():
            return False, "A download or build task is already currently running."

        if not components:
            components = ["recording.tar.xz"]

        self._cancel_requested = False

        if background:
            self._current_task_thread = threading.Thread(
                target=self._run_download_and_build,
                args=(components,),
                daemon=True
            )
            self._current_task_thread.start()
            return True, "Download started in background."
        else:
            self._run_download_and_build(components)
            return True, "Download and build completed."

    def cancel_task(self):
        """Signals active background job to cancel."""
        self._cancel_requested = True

    def delete_dump(self):
        """Deletes all local dump files and index database from disk."""
        if self._current_task_thread and self._current_task_thread.is_alive():
            self._cancel_requested = True
            time.sleep(0.5)

        for item in self.dump_dir.glob("*"):
            try:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception as e:
                print(f"[MusicBrainzDump] Error removing {item}: {e}")

        self._update_status_file({
            "status": "not_downloaded",
            "progress": {},
            "error": None,
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
        })

    def _init_db(self, conn: sqlite3.Connection):
        """Initializes SQLite tables and performance PRAGMAs."""
        cur = conn.cursor()
        cur.execute("PRAGMA synchronous = OFF;")
        cur.execute("PRAGMA journal_mode = MEMORY;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artist_name TEXT,
                clean_artist TEXT,
                song_title TEXT,
                clean_title TEXT,
                album_title TEXT,
                release_year INTEGER,
                primary_type TEXT,
                score INTEGER
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rec_artist_title ON recordings(clean_artist, clean_title);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rec_title ON recordings(clean_title);")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS artists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mbid TEXT UNIQUE,
                name TEXT,
                clean_name TEXT,
                type TEXT,
                country TEXT,
                relations_json TEXT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_art_clean_name ON artists(clean_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_art_mbid ON artists(mbid);")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS release_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mbid TEXT,
                artist_name TEXT,
                clean_artist TEXT,
                title TEXT,
                clean_title TEXT,
                primary_type TEXT,
                secondary_types TEXT,
                release_year INTEGER,
                score INTEGER
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_rg_artist_title ON release_groups(clean_artist, clean_title);")
        conn.commit()

    def _run_download_and_build(self, components: List[str]):
        """Worker thread loop to download dump archives and index them into SQLite."""
        upstream_version = self.get_latest_upstream_version() or time.strftime("%Y%m%d-000000")
        
        status_state = {
            "status": "downloading",
            "upstream_version": upstream_version,
            "progress": {
                "step": "Starting download...",
                "percent": 0,
                "bytes_downloaded": 0,
                "total_bytes": 0,
                "speed": "0 KB/s"
            },
            "error": None,
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        self._update_status_file(status_state)

        try:
            for comp in components:
                if self._cancel_requested:
                    raise Exception("Task was cancelled by user.")

                comp_url = f"{BASE_URL}/{upstream_version}/{comp}"
                dest_file = self.dump_dir / comp
                temp_dest = self.dump_dir / f"{comp}.part"

                status_state["status"] = "downloading"
                status_state["progress"]["step"] = f"Downloading {comp}..."
                self._update_status_file(status_state)

                req = urllib.request.Request(comp_url, headers={"User-Agent": f"ConcertTrakr/1.0 ({CONTACT_EMAIL})"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    total_size = int(resp.headers.get("Content-Length", 0))
                    status_state["progress"]["total_bytes"] = total_size

                    downloaded = 0
                    start_time = time.time()
                    last_update_time = start_time

                    with open(temp_dest, "wb") as out_f:
                        while True:
                            if self._cancel_requested:
                                raise Exception("Task was cancelled by user.")

                            chunk = resp.read(64 * 1024)
                            if not chunk:
                                break
                            out_f.write(chunk)
                            downloaded += len(chunk)

                            now = time.time()
                            if now - last_update_time >= 0.5:
                                elapsed = now - start_time
                                speed_kb = (downloaded / 1024) / elapsed if elapsed > 0 else 0
                                pct = int((downloaded / total_size * 100)) if total_size > 0 else 0
                                status_state["progress"]["percent"] = pct
                                status_state["progress"]["bytes_downloaded"] = downloaded
                                status_state["progress"]["speed"] = f"{speed_kb:.1f} KB/s"
                                self._update_status_file(status_state)
                                last_update_time = now

                if temp_dest.exists():
                    if dest_file.exists():
                        dest_file.unlink()
                    temp_dest.rename(dest_file)

            # Build / populate local SQLite database from downloaded archives
            temp_db = self.dump_dir / "mb_dump_building.db"
            if temp_db.exists():
                temp_db.unlink()

            conn = sqlite3.connect(str(temp_db))
            self._init_db(conn)

            # 1. Process Releases & Tracklists (release.tar.xz) -> Maps Song Titles directly to Albums & Years!
            release_archive = self.dump_dir / "release.tar.xz"
            if release_archive.exists():
                status_state["status"] = "extracting"
                status_state["progress"] = {
                    "step": "Indexing releases & tracklists (song-to-album mapping)...",
                    "percent": 0,
                    "indexed": 0
                }
                self._update_status_file(status_state)

                with tarfile.open(release_archive, mode="r:xz") as tar:
                    member_name = None
                    for m in tar.getmembers():
                        if "release" in m.name and not m.name.endswith(".txt") and not m.name.endswith(".asc"):
                            member_name = m.name
                            break
                    if member_name:
                        f = tar.extractfile(member_name)
                        batch = []
                        line_count = 0
                        inserted_count = 0

                        for line in f:
                            if self._cancel_requested:
                                conn.close()
                                if temp_db.exists():
                                    temp_db.unlink()
                                raise Exception("Task was cancelled by user.")

                            line_count += 1
                            try:
                                obj = json.loads(line.decode("utf-8"))
                                rel_title = obj.get("title") or ""
                                rg = obj.get("release-group") or {}
                                album_title = rg.get("title") or rel_title
                                if not album_title:
                                    continue

                                primary_type = rg.get("primary-type") or "Album"
                                sec_types = rg.get("secondary-types") or []
                                date_str = rg.get("first-release-date") or obj.get("date") or ""

                                release_year = None
                                if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                                    y = int(date_str[:4])
                                    if 1950 <= y <= 2030:
                                        release_year = y

                                score = 100
                                if primary_type == "Album":
                                    score += 80
                                elif primary_type == "EP":
                                    score += 40
                                if any(t.lower() in ["live", "demo", "compilation", "remix", "soundtrack", "dj-mix"] for t in sec_types):
                                    score -= 50
                                else:
                                    score += 40

                                rel_artists = obj.get("artist-credit", [])
                                default_artist = (rel_artists[0].get("name") or rel_artists[0].get("artist", {}).get("name")) if rel_artists else ""

                                for m in obj.get("media", []):
                                    for trk in m.get("tracks", []):
                                        trk_title = trk.get("title") or (trk.get("recording") or {}).get("title")
                                        if not trk_title:
                                            continue
                                        trk_artists = trk.get("artist-credit", [])
                                        artist_name = (trk_artists[0].get("name") or trk_artists[0].get("artist", {}).get("name")) if trk_artists else default_artist
                                        if not artist_name:
                                            continue

                                        clean_art = _normalize_key(clean_artist_name(artist_name))
                                        clean_trk = _normalize_key(trk_title)
                                        if not clean_art or not clean_trk:
                                            continue

                                        batch.append((
                                            artist_name,
                                            clean_art,
                                            trk_title,
                                            clean_trk,
                                            clean_album_title(album_title),
                                            release_year,
                                            primary_type,
                                            score
                                        ))

                                        if len(batch) >= 20000:
                                            cur = conn.cursor()
                                            cur.executemany("""
                                                INSERT INTO recordings (
                                                    artist_name, clean_artist, song_title, clean_title,
                                                    album_title, release_year, primary_type, score
                                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                            """, batch)
                                            conn.commit()
                                            inserted_count += len(batch)
                                            batch = []

                                            if line_count % 50000 == 0:
                                                status_state["progress"]["indexed"] = inserted_count
                                                self._update_status_file(status_state)
                            except Exception:
                                continue

                        if batch:
                            cur = conn.cursor()
                            cur.executemany("""
                                INSERT INTO recordings (
                                    artist_name, clean_artist, song_title, clean_title,
                                    album_title, release_year, primary_type, score
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, batch)
                            conn.commit()
                            inserted_count += len(batch)

            # 2. Process Standalone Recordings (recording.tar.xz) if release.tar.xz was not downloaded
            recording_archive = self.dump_dir / "recording.tar.xz"
            if recording_archive.exists() and not release_archive.exists():
                status_state["status"] = "extracting"
                status_state["progress"] = {
                    "step": "Indexing standalone recordings...",
                    "percent": 0,
                    "indexed": 0
                }
                self._update_status_file(status_state)

                with tarfile.open(recording_archive, mode="r:xz") as tar:
                    member_name = None
                    for m in tar.getmembers():
                        if "recording" in m.name and not m.name.endswith(".txt") and not m.name.endswith(".asc"):
                            member_name = m.name
                            break
                    if member_name:
                        f = tar.extractfile(member_name)
                        batch = []
                        line_count = 0
                        inserted_count = 0

                        for line in f:
                            if self._cancel_requested:
                                conn.close()
                                if temp_db.exists():
                                    temp_db.unlink()
                                raise Exception("Task was cancelled by user.")

                            line_count += 1
                            try:
                                obj = json.loads(line.decode("utf-8"))
                                title = obj.get("title")
                                if not title:
                                    continue
                                artist_credits = obj.get("artist-credit", [])
                                if not artist_credits:
                                    continue
                                artist_name = artist_credits[0].get("name") or artist_credits[0].get("artist", {}).get("name")
                                if not artist_name:
                                    continue

                                clean_artist = _normalize_key(clean_artist_name(artist_name))
                                clean_title = _normalize_key(title)
                                if not clean_artist or not clean_title:
                                    continue

                                release_year = None
                                date_str = obj.get("first-release-date") or ""
                                if date_str and len(date_str) >= 4 and date_str[:4].isdigit():
                                    y = int(date_str[:4])
                                    if 1950 <= y <= 2030:
                                        release_year = y

                                batch.append((
                                    artist_name, clean_artist, title, clean_title,
                                    None, release_year, "Recording", 100
                                ))

                                if len(batch) >= 20000:
                                    cur = conn.cursor()
                                    cur.executemany("""
                                        INSERT INTO recordings (
                                            artist_name, clean_artist, song_title, clean_title,
                                            album_title, release_year, primary_type, score
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    """, batch)
                                    conn.commit()
                                    inserted_count += len(batch)
                                    batch = []
                            except Exception:
                                continue

                        if batch:
                            cur = conn.cursor()
                            cur.executemany("""
                                INSERT INTO recordings (
                                    artist_name, clean_artist, song_title, clean_title,
                                    album_title, release_year, primary_type, score
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, batch)
                            conn.commit()
                            inserted_count += len(batch)

            # 3. Process Artists (artist.tar.xz) -> Band Member Tenures & Lineups
            artist_archive = self.dump_dir / "artist.tar.xz"
            if artist_archive.exists():
                status_state["status"] = "extracting"
                status_state["progress"] = {
                    "step": "Indexing artist lineups & tenures...",
                    "percent": 0,
                    "indexed": 0
                }
                self._update_status_file(status_state)

                with tarfile.open(artist_archive, mode="r:xz") as tar:
                    member_name = None
                    for m in tar.getmembers():
                        if "artist" in m.name and not m.name.endswith(".txt") and not m.name.endswith(".asc"):
                            member_name = m.name
                            break
                    if member_name:
                        f = tar.extractfile(member_name)
                        batch = []
                        for line in f:
                            if self._cancel_requested:
                                conn.close()
                                if temp_db.exists():
                                    temp_db.unlink()
                                raise Exception("Task was cancelled by user.")
                            try:
                                obj = json.loads(line.decode("utf-8"))
                                mbid = obj.get("id")
                                name = obj.get("name")
                                if not mbid or not name:
                                    continue
                                clean_name = _normalize_key(clean_artist_name(name))
                                artist_type = obj.get("type") or ""
                                country = obj.get("country") or ""
                                rels_json = json.dumps(obj)

                                batch.append((mbid, name, clean_name, artist_type, country, rels_json))
                                if len(batch) >= 10000:
                                    cur = conn.cursor()
                                    cur.executemany("""
                                        INSERT OR REPLACE INTO artists (
                                            mbid, name, clean_name, type, country, relations_json
                                        ) VALUES (?, ?, ?, ?, ?, ?)
                                    """, batch)
                                    conn.commit()
                                    batch = []
                            except Exception:
                                continue

                        if batch:
                            cur = conn.cursor()
                            cur.executemany("""
                                INSERT OR REPLACE INTO artists (
                                    mbid, name, clean_name, type, country, relations_json
                                ) VALUES (?, ?, ?, ?, ?, ?)
                            """, batch)
                            conn.commit()

            conn.close()

            # Swap building DB into final location
            if temp_db.exists():
                if self.db_path.exists():
                    self.db_path.unlink()
                temp_db.rename(self.db_path)

            # Mark version and complete
            self.latest_file.write_text(upstream_version, encoding="utf-8")

            status_state["status"] = "ready"
            status_state["progress"] = {
                "step": "Completed successfully",
                "percent": 100
            }
            status_state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._update_status_file(status_state)

        except Exception as e:
            print(f"[MusicBrainzDump] Build error: {e}")
            status_state["status"] = "error"
            status_state["error"] = str(e)
            status_state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._update_status_file(status_state)

    def lookup_studio_album(self, artist_name: str, song_name: str) -> Tuple[Optional[str], Optional[int]]:
        """Queries local disk SQLite database for studio album and release year."""
        if not self.is_dump_available():
            return None, None

        clean_art = _normalize_key(clean_artist_name(artist_name))
        clean_song = _normalize_key(song_name)
        if not clean_art or not clean_song:
            return None, None

        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                cur = conn.cursor()
                # 1. Exact clean match
                cur.execute("""
                    SELECT album_title, release_year, score
                    FROM recordings
                    WHERE clean_artist = ? AND clean_title = ?
                    ORDER BY score DESC, release_year ASC
                    LIMIT 1
                """, (clean_art, clean_song))
                row = cur.fetchone()
                if row:
                    album, yr, _ = row
                    return album, yr

                # 2. Substring / Prefix match on title
                if len(clean_song) >= 4:
                    cur.execute("""
                        SELECT album_title, release_year, score
                        FROM recordings
                        WHERE clean_artist = ? AND clean_title LIKE ?
                        ORDER BY score DESC, release_year ASC
                        LIMIT 1
                    """, (clean_art, f"{clean_song}%"))
                    row = cur.fetchone()
                    if row:
                        album, yr, _ = row
                        return album, yr
        except Exception as e:
            print(f"[MusicBrainzDump] Lookup error: {e}")

        return None, None

    def lookup_artist_mbid(self, artist_name: str) -> Optional[str]:
        """Queries local disk SQLite database for artist MBID."""
        if not self.is_dump_available():
            return None

        clean_art = _normalize_key(clean_artist_name(artist_name))
        if not clean_art:
            return None

        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT mbid FROM artists WHERE clean_name = ? LIMIT 1", (clean_art,))
                row = cur.fetchone()
                if row:
                    return row[0]
        except Exception as e:
            print(f"[MusicBrainzDump] Artist MBID lookup error: {e}")

        return None

    def lookup_artist_relations(self, mbid: str) -> Optional[Dict[str, Any]]:
        """Queries local disk SQLite database for artist relationships and member tenures."""
        if not self.is_dump_available() or not mbid:
            return None

        try:
            with sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True) as conn:
                cur = conn.cursor()
                cur.execute("SELECT relations_json FROM artists WHERE mbid = ? LIMIT 1", (mbid,))
                row = cur.fetchone()
                if row and row[0]:
                    return json.loads(row[0])
        except Exception as e:
            print(f"[MusicBrainzDump] Artist relations lookup error: {e}")

        return None
