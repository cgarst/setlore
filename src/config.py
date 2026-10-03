import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", DATA_DIR / "cache" if DATA_DIR.exists() else BASE_DIR / "cache"))
# Legacy cache directory paths retained for historical migrations
SETLIST_CACHE_DIR = CACHE_DIR / "setlists"
USER_CACHE_DIR = CACHE_DIR / "user"
MB_CACHE_DIR = CACHE_DIR / "musicbrainz"
MB_DUMP_DIR = Path(os.getenv("MB_DUMP_DIR", DATA_DIR / "musicbrainz_dump"))

for d in [MB_CACHE_DIR, MB_DUMP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

SETLISTFM_API_KEY = os.getenv("SETLISTFM_KEY") or os.getenv("setlistfm_key", "").strip()
SETLISTFM_USER = os.getenv("SETLISTFM_USER") or os.getenv("setlistfm_user", "").strip()
CARTO_API_KEY = os.getenv("CARTO_API_KEY") or os.getenv("carto_api_key", "").strip()
BANDSINTOWN_APP_ID = os.getenv("BANDSINTOWN_APP_ID", "12345").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "admin@localhost").strip()
APP_URL = os.getenv("APP_URL") or (os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")[0].strip() if os.getenv("CSRF_TRUSTED_ORIGINS") else "")

DEFAULT_SHEET_URL = os.getenv("DEFAULT_SHEET_URL", "").strip()
DEFAULT_CSV_PATH = Path(os.getenv("DEFAULT_CSV_PATH", BASE_DIR / "concerts.csv"))

# Artists to exclude / ignore across global reporting and analytics by default (per-user exclusions override this)
IGNORED_ARTISTS = []
