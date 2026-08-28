# 🎸 concert-trakr

A powerful Python analytics engine and interactive dashboard for tracking, reconciling, and visualizing your live concert history.

`concert-trakr` synchronizes your personal concert attendance spreadsheet (or CSV) with [Setlist.fm](https://www.setlist.fm/), enriches your catalog with [MusicBrainz](https://musicbrainz.org/) discography data, analyzes musician lineup tenures across bands, maps venues geographically, and generates a rich, responsive HTML dashboard.

---

## ✨ Features

- **📊 Comprehensive Concert Analytics**: Summarizes shows attended, unique artists seen, venues visited, songs heard (total and unique), attendance patterns by year, decade, month, and day of week.
- **🔄 Bidirectional Gap Analysis**:
  - Reconciles your personal spreadsheet with your Setlist.fm attended history using fuzzy matching ([RapidFuzz](https://github.com/rapidfuzz/RapidFuzz)).
  - Flags attended shows missing `I was there` tags on Setlist.fm.
  - Identifies shows logged on Setlist.fm that are missing from your spreadsheet.
  - Automatically searches global Setlist.fm archives for missing setlists.
- **💿 MusicBrainz Album & Discography Enrichment**:
  - Automatically queries MusicBrainz to map live songs to studio albums, original release years, and release groups.
  - Smart album title normalization and filtering (strips remaster tags, ignores live/compilation noise).
  - Categorizes songs into studio albums vs. non-album singles/covers.
- **👥 Musician Tenure & Lineup Tracker**:
  - Tracks individual band members and historical tenures across multiple bands/projects (e.g., Mike Portnoy across Dream Theater, The Winery Dogs, Transatlantic, etc.).
  - Calculates individual musician attendance counts based on the exact concert date and historical lineup dates.
- **🗺️ Venue Mapping & Geocoding**:
  - Geocodes venue and city information into map coordinates for interactive venue visualization.
- **🎨 Interactive Dashboard Report**:
  - Produces a self-contained, interactive HTML report (`concert_report.html`) complete with searchable concert drilldowns, album breakdown matrices, coverage indicators, and venue mapping.
- **⚡ Resilient Multi-Tier Caching**:
  - Caches Setlist.fm user history, venue/artist setlists, and MusicBrainz metadata locally in JSON to minimize network calls and respect API rate limits.

---

## 📁 Project Structure

```text
concerts/
├── cache/                     # Local JSON API cache
│   ├── musicbrainz/           # MusicBrainz query and recording cache
│   ├── setlists/              # Cached show setlists from Setlist.fm
│   └── user/                  # Cached user attendance data
├── src/
│   ├── __init__.py
│   ├── album_enricher.py      # MusicBrainz API client, rate limiter & album mapping
│   ├── analytics.py          # Metric calculations, aggregations & concert drilldown
│   ├── config.py             # App configuration, directories, and environment variables
│   ├── csv_parser.py         # Parser for Google Sheets URLs and local CSV files
│   ├── gap_analysis.py       # Reconciles spreadsheet entries against Setlist.fm records
│   ├── musician_tracker.py   # Musician tenure database & cross-band attendance counter
│   ├── reporter.py           # Jinja2 HTML report generator
│   ├── setlist_api.py        # Setlist.fm REST API client with local caching
│   ├── templates/
│   │   └── dashboard.html    # Jinja2 dashboard UI template
│   └── venue_mapper.py       # Venue geocoding and map layer generator
├── tests/
│   └── test_pipeline.py      # Unit tests for date parsing, cleanup, and gap analysis
├── .env                      # Environment variables (API keys, user settings)
├── concert_report.html       # Default rendered HTML output
├── main.py                   # Main CLI entrypoint
└── requirements.txt          # Python dependencies
```

---

## 🛠️ Installation & Setup

### 1. Prerequisites
- **Python 3.9+**
- A **Setlist.fm API Key** (obtainable free from [Setlist.fm API settings](https://www.setlist.fm/settings/api))

### 2. Clone the Repository
```bash
git clone https://github.com/your-username/concert-trakr.git
cd concert-trakr
```

### 3. Create a Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

### 5. Configure Environment Variables
Create a `.env` file in the root directory (or edit the existing `.env`):

```dotenv
# Your Setlist.fm API Key
setlistfm_key=your_setlistfm_api_key_here

# Your default Setlist.fm username
setlistfm_user=YourUsername

# (Optional) CARTO API Key for advanced map layers
carto_api_key=cb1_2exf_1_3b2a43a7cdd980ffef0b756c
```

---

## 🚀 Usage

### Run Default Analysis
By default, the script reads from the configured Google Sheet URL / default CSV, fetches Setlist.fm data for the user configured in `.env`, runs gap analysis, enriches album data, and exports `concert_report.html`:

```bash
python main.py
```

### Command-Line Options

| Argument | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `--source` | `str` | *Configured Sheet URL* | Path to a local CSV file or public Google Sheets URL. |
| `--user` | `str` | `SETLISTFM_USER` / `.env` | Setlist.fm username to reconcile against. |
| `--output` | `str` | `concert_report.html` | Destination path for the generated HTML report. |
| `--no-cache` | `flag` | `False` | Force fresh API fetch bypassing all local caches. |
| `--refresh-setlists` | `flag` | `False` | Force a fresh fetch of user-attended setlists from Setlist.fm. |
| `--refresh-unresolved` | `flag` | `False` | Re-query MusicBrainz only for songs marked as Non-Album / Singles. |
| `--refresh-all` | `flag` | `False` | Force re-query of all songs in catalog across MusicBrainz and Setlist.fm. |

### Examples

**Using a local CSV file and custom output path:**
```bash
python main.py --source "/path/to/my_concerts.csv" --output "my_dashboard.html"
```

**Analyzing a different Setlist.fm user:**
```bash
python main.py --user "Zathu"
```

**Refreshing unresolved MusicBrainz album tracks:**
```bash
python main.py --refresh-unresolved
```

**Bypassing all cached API responses:**
```bash
python main.py --no-cache
```

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

Run the test suite using Python's built-in `unittest` runner:

```bash
python -m unittest discover tests
```

Or run the pipeline tests directly:
```bash
python -m unittest tests/test_pipeline.py
```

---

## 💾 Caching Strategy & Rate Limiting

- **Setlist.fm**: Cached in `cache/setlists/` and `cache/user/` to prevent hitting the 2 req/sec rate limit.
- **MusicBrainz**: Cached in `cache/musicbrainz/` with a compliant 1.0-second delay between live API queries.
- To safely purge or invalidate caches, use the respective `--refresh-*` flags or remove individual JSON files in the `cache/` directory.

---

## 📄 License

This project is licensed under the MIT License.
