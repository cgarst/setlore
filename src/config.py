import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
SETLIST_CACHE_DIR = CACHE_DIR / "setlists"
USER_CACHE_DIR = CACHE_DIR / "user"
MB_CACHE_DIR = CACHE_DIR / "musicbrainz"

for d in [SETLIST_CACHE_DIR, USER_CACHE_DIR, MB_CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

SETLISTFM_API_KEY = os.getenv("SETLISTFM_KEY") or os.getenv("setlistfm_key", "").strip()
SETLISTFM_USER = os.getenv("SETLISTFM_USER") or os.getenv("setlistfm_user", "").strip() or "Zathu"
CARTO_API_KEY = os.getenv("carto_api_key", "cb1_2exf_1_3b2a43a7cdd980ffef0b756c").strip()
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "admin@localhost").strip()

DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/17uJNNqBOu_F4B_9u1NHCJW820VC9Hcfi9yf1eiXpDyA/edit?gid=0#gid=0"
DEFAULT_CSV_PATH = BASE_DIR / "Concerts - Attended.csv"

# Artists to exclude / ignore across all reporting and analytics
IGNORED_ARTISTS = [
    "Master Sword",
]
