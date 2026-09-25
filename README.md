# 🎸 concert-trakr

A powerful Python analytics engine and multi-user web dashboard for tracking, reconciling, and visualizing your live concert history.

`concert-trakr` synchronizes your personal concert attendance spreadsheet (or CSV) with [Setlist.fm](https://www.setlist.fm/), enriches your catalog with [MusicBrainz](https://musicbrainz.org/) discography data, analyzes musician lineup tenures across bands, maps venues geographically, and generates a rich, responsive interactive dashboard.

---

## ✨ Features

- **👥 Multi-User Self-Hosting**: Built-in user authentication, isolated attendance tracking, and individual Setlist.fm syncs.
- **🎛️ Django Admin Superpowers**: Full `/admin/` portal out-of-the-box to manage users, correct misattributed albums/songs, edit band lineups, and adjust venue coordinates.
- **📊 Comprehensive Concert Analytics**: Summarizes shows attended, unique artists seen, venues visited, songs heard (total and unique), attendance patterns by year, decade, month, and day of week.
- **🔄 Bidirectional Gap Analysis**:
  - Reconciles your personal spreadsheet with your Setlist.fm attended history using fuzzy matching ([RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)).
  - Flags attended shows missing `I was there` tags on Setlist.fm.
  - Identifies shows logged on Setlist.fm that are missing from your spreadsheet.
  - Automatically searches global Setlist.fm archives for missing setlists.
- **💿 MusicBrainz Album & Discography Enrichment**:
  - Automatically queries MusicBrainz to map live songs to studio albums, original release years, and release groups.
  - Smart album title normalization and filtering (strips remaster tags, ignores live/compilation noise).
  - Global shared catalog: cached albums and tracks are shared across users to eliminate redundant API queries.
- **👥 Musician Tenure & Lineup Tracker**:
  - Tracks individual band members and historical tenures across multiple bands/projects (e.g., Mike Portnoy across Dream Theater, The Winery Dogs, Transatlantic, etc.).
  - Calculates individual musician attendance counts based on the exact concert date and historical lineup dates.
- **🗺️ Venue Mapping & Geocoding**:
  - Geocodes venue and city information into map coordinates for interactive venue visualization.
- **⚡ Resilient SQLite Persistence**:
  - All data and API responses are persisted in a host-mounted SQLite database (`data/db.sqlite3`) with WAL concurrency.

---

## 🐳 Quickstart: Docker & Docker Compose (Recommended)

The easiest way to self-host `concert-trakr` is using **Docker Compose**:

### 1. Configure `.env`
Copy the example environment file:
```bash
cp .env.example .env
```
Edit `.env` to set your credentials:
```dotenv
DJANGO_SECRET_KEY=generate_a_random_secret_key
SETLISTFM_KEY=your_setlistfm_api_key_here
ADMIN_USERNAME=Zathu
ADMIN_PASSWORD=change_this_password
SETLISTFM_USER=Zathu
```

### 2. Launch Container
```bash
docker compose up -d
```

### 3. Open the App
- **Dashboard**: Visit [http://localhost:8000](http://localhost:8000) and sign in.
- **Admin Panel**: Visit [http://localhost:8000/admin](http://localhost:8000/admin) to manage users and catalog metadata.
- **Data Persistence**: All database state is stored in your host machine's `./data/db.sqlite3` file and persists across container rebuilds.

---

## 🛠️ Local Development Setup with `uv`

Dependencies and virtual environments are managed using [uv](https://github.com/astral-sh/uv):

### 1. Prerequisites
- **Python 3.12+**
- **uv** (`pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- A **Setlist.fm API Key** (obtainable free from [Setlist.fm API settings](https://www.setlist.fm/settings/api))

### 2. Install Dependencies
```bash
uv sync
```

### 3. Initialize Database, Seed User 1 & Collect Static Files
```bash
uv run python manage.py migrate
uv run python manage.py seed_user_one
uv run python manage.py collectstatic --noinput
```

### 4. Run the Development Server
```bash
uv run python manage.py runserver
```
Visit `http://localhost:8000` and log in with your configured admin credentials.

---

## 📋 Data Source & Format Specification

When supplying a CSV or Google Sheet, the parser expects header columns matching common concert spreadsheet conventions:

| Column | Required | Description / Example |
| :--- | :--- | :--- |
| **Date** | Yes | Date of show in `MM/DD/YYYY`, `MM/DD/YY`, `YYYY-MM-DD`, or `DD-MM-YYYY` format (e.g. `07/29/2003`). |
| **Band / Artist / Bands** | Yes | Comma/semicolon/slash-separated artist names (e.g. `Iron Maiden, Dio, Motorhead` or `Gigantour: Megadeth, Dream Theater`). |
| **Venue** | Recommended | Venue name (e.g. `Merriweather Post Pavilion`). |
| **City / State** | Optional | Location details (e.g. `Columbia, MD`). |
| **Notes / Tour** | Optional | Tour names or notes (e.g. `Give Me Ed... 'Til I'm Dead Tour`). |

> **Artist Normalization**: Common variations and tour titles (e.g. `Gigantour: ...`, `ProgPower USA`, `G3: ...`) are automatically split and normalized to canonical band names.

---

## 🧪 Running Tests

Run the full test suite (pipeline + Django models & auth):

```bash
uv run python manage.py test tests
```

---

## 💾 Caching Strategy & Rate Limiting

- **Setlist.fm**: Rate-limited to ~1.6 req/sec to safely stay under the 2 req/sec limit.
- **MusicBrainz**: Complies with the 1.0-second delay between live queries.
- **Single-Worker Queue**: Background syncs across all users are queued sequentially in `sync_worker.py` to prevent multi-user concurrency from triggering IP bans.
- **SQLite Storage**: API responses are cached in the `catalog.ApiCache` SQLite table.

---

## 📄 License

This project is licensed under the MIT License.
