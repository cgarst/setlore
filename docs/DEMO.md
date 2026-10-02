# 🎸 Setlore Static Demo Generator

This directory documents the developer command-line tool for generating a static "demo" flavor of Setlore suitable for hosting on **GitHub Pages** (or any static hosting provider like Cloudflare Pages, Netlify, or Vercel) and refreshing UI showcase screenshots.

---

## ⚡ Quickstart: The Full Command

To run the complete build with the specified user and anniversary date emulation:

```bash
# Full Command (Target user Zathu + Anniversary date emulation 2026-08-10 + Desktop & Mobile screenshots):
python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10
```

*(You can also use the shorthand alias `python manage.py freeze` or simply `python manage.py freeze_demo` since both `Zathu` and `2026-08-10` are configured as defaults).*

---

## 📖 Overview & Architecture

Setlore allows you to freeze a fully interactive, static version of:
1. **The Landing Page** (`index.html`): Configured in the **Demo Flavor**.
   - Removes live server features (Sign In, User Registration, and Community Server Leaderboard).
   - Only provides actions to explore the interactive demo (**"Demo Setlore"**) or install the project (**"Install via GitHub"**).
   - Features an interactive **Screenshot Showcase** with a **Desktop / Mobile toggle** (automatically defaulted to your device's screen width) and tabbed modal previews.
2. **Selected User Public Profile** (`demo/index.html` & `demo.html`):
   - Standalone, self-contained concert dashboard populated with real data from a chosen user (**`Zathu`** by default).
   - Includes *On This Day* anniversary radar calculated against an emulated anniversary date (`2026-08-10`).
   - Includes interactive Plotly discography treemaps, album breakdown matrices, Leaflet dark-tile venue maps, artist drilldowns, and musician lineups.
   - Preserves client-side tab switching and modal deep-linking.
   - Includes a direct static CSV export of the demo user's concert history (`concerts.csv`).
3. **Static Privacy Policy** (`privacy/index.html` & `privacy.html`).
4. **Automated Screenshot Refresh**:
   - Uses Playwright against an ephemeral authenticated WSGI server thread.
   - Generates both **Desktop (1440×900)** and **Mobile (390×844)** versions for every screen.
   - Captures rich modals and views:
     - **Overview**: *On This Day* anniversary radar (`2026-08-10`) & attendance trends.
     - **Setlist Modal**: Haken at Cafe 611.
     - **Artist Modal**: Megadeth.
     - **Song Modal**: Demon of the Fall (Opeth).
     - **Albums Gallery**: 2000s Era filter selected.
     - **Album Modal**: Train of Thought (Dream Theater).
     - **Musicians**: Lineup tenure & supergroup matrices.
     - **Venue Map**: Geocoded venue map.
     - **Freshness**: Song rarity and debut tracking.
     - **Theme Palettes**: Theme selector dialog.
5. **Static Assets & GitHub Pages Config**:
   - Bundles `static/` (icons, banners, web manifest, and screenshot assets).
   - Automatically generates `.nojekyll` to disable Jekyll processing on GitHub Pages.

> [!NOTE]
> **Maintainability Guarantee (No Forking)**: The static pages share the exact same template files (`templates/home.html`, `templates/dashboard.html`, `templates/privacy.html`) as the live Django application via the `is_demo` context variable.

---

## 🛠️ CLI Usage & Flags

```bash
# Basic run (uses Zathu and 2026-08-10 defaults)
python manage.py freeze_demo

# Full explicit command
python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10 --output-dir docs
```

---

### ⚙️ Command Options & Flags Reference

| Flag | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-u` | `--user`, `--username` | `Zathu` *(or user with most shows)* | Username whose public profile and screenshots will be frozen as the demo. |
| | `--emulate-date` | `2026-08-10` | Emulate a specific date (`YYYY-MM-DD`) so the *On This Day* anniversary banner is populated. |
| `-o` | `--output-dir` | `docs` | Destination directory for generated static files. |
| `-r` | `--repo-url` | `https://github.com/cgarst/setlore` | GitHub repository URL used for "Install" and source links. |
| | `--skip-screenshots` | `False` | Skip Playwright UI screenshot generation to quickly build HTML/assets only. |
| | `--no-clean` | `False` | Do not wipe the output directory before generating files. |

---

### 💡 Example Commands

#### 1. Full Production Build (Zathu + 2026-08-10 + Desktop & Mobile Screenshots)
```bash
python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10
```

#### 2. Fast Build (Skip Screenshots)
```bash
python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10 --skip-screenshots
```

#### 3. Custom Output Directory (e.g. `dist/` or `demo_build/`)
```bash
python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10 -o dist
```

---

## 📸 Screenshots & Showcase Details

When running `freeze_demo` without `--skip-screenshots`, the command automatically produces both standard (desktop) and `_mobile` screenshots in `static/img/screenshots/`:

| Screen / Modal | Target / State | Desktop Filename | Mobile Filename |
| :--- | :--- | :--- | :--- |
| **Overview** | Anniversaries (`?emulate_date=2026-08-10`) | `overview.png` | `overview_mobile.png` |
| **Concerts / Setlist** | Modal: **Haken** at Cafe 611 | `concerts.png` | `concerts_mobile.png` |
| **Artist Modal** | Modal: **Megadeth** | `artists.png` | `artists_mobile.png` |
| **Song Modal** | Modal: **Demon of the Fall** (Opeth) | `songs.png` | `songs_mobile.png` |
| **Albums Gallery** | Filter: **2000s** Era | `albums.png` | `albums_mobile.png` |
| **Album Modal** | Modal: **Train of Thought** (Dream Theater) | `album_modal.png` | `album_modal_mobile.png` |
| **Musicians** | Musicians & Lineup Tenure | `musicians.png` | `musicians_mobile.png` |
| **Venue Map** | Interactive Venue Map | `map.png` | `map_mobile.png` |
| **Freshness** | Song Freshness & Rarities | `freshness.png` | `freshness_mobile.png` |
| **Themes** | Theme Chooser Dialog | `theme_palettes.png` | `theme_palettes_mobile.png` |

On the landing page (`index.html`), the **Screenshot Showcase** includes a **Desktop / Mobile toggle** that automatically adapts the view to the visitor's screen size or allows manual switching between full desktop layouts and phone mockups.

---

## 📂 Generated Build Structure

When generated with the default output directory (`docs/`), the resulting directory tree is:

```text
docs/
├── .nojekyll                 # Bypasses Jekyll processing on GitHub Pages
├── index.html                # Landing page in Demo flavor
├── demo.html                 # Direct root link to Demo Profile
├── concerts.csv              # Static CSV download of concerts
├── demo/
│   ├── index.html            # Subdirectory entrypoint for Demo Profile
│   └── concerts.csv          # Relative CSV export link for demo/
├── privacy.html              # Direct root link to Privacy Policy
├── privacy/
│   └── index.html            # Subdirectory entrypoint for Privacy Policy
└── static/
    ├── img/
    │   ├── screenshots/      # Desktop & Mobile screenshot assets
    │   ├── setlore_banner.png
    │   ├── setlore_icon.png
    │   └── setlore_icon_transparent.png
    └── manifest.json
```

---

## 🖥️ Local Previewing

You can preview the generated static site locally using Python's built-in HTTP server:

```bash
# Serve the generated 'docs' folder on port 8000
python -m http.server -d docs 8000
```

Then visit:
- **Landing Page**: [http://localhost:8000/](http://localhost:8000/)
- **Interactive Demo Profile**: [http://localhost:8000/demo/](http://localhost:8000/demo/) or [http://localhost:8000/demo.html](http://localhost:8000/demo.html)
- **Direct Tab Deep Linking**: [http://localhost:8000/demo/#map](http://localhost:8000/demo/#map), [http://localhost:8000/demo/#musicians](http://localhost:8000/demo/#musicians), [http://localhost:8000/demo/#albums](http://localhost:8000/demo/#albums)

---

## 🌐 Deploying to GitHub Pages

1. Run the generator to update the `docs/` folder:
   ```bash
   python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10
   ```
2. Commit and push the `docs/` directory to your GitHub repository:
   ```bash
   git add docs/ static/img/screenshots/
   git commit -m "chore: build static demo for GitHub Pages"
   git push origin main
   ```
3. In your GitHub repository:
   - Go to **Settings** &rarr; **Pages**.
   - Under **Build and deployment** &rarr; **Branch**:
     - Select **Branch**: `main` (or default branch).
     - Select **Folder**: `/docs`.
   - Click **Save**.
4. GitHub Pages will publish your static demo at `https://<username>.github.io/<repo-name>/`.

---

## 🗄️ Demo User Cache

The static demo is populated from a **real user's concert history**. Because this data can be large and is user-specific, the cache file (`data/cache/demo_user_cache.json`) is **gitignored** and must be generated locally before running `freeze_demo` in fresh environments.

### Generating the Cache

Export a user's full concert history into the cache file:

```bash
# Export 'Zathu' (or 'demouser') to the default cache path
python manage.py dump_demo_cache --user Zathu

# Export to a custom path
python manage.py dump_demo_cache --user Zathu --output /path/to/my_cache.json
```

---

### Seeding the Cache into a Database

Use `populate_demo_user` to load a cache file into any target database:

```bash
# Populate Zathu from cache
python manage.py populate_demo_user --target-user Zathu --superuser

# Set the user's password if newly created
python manage.py populate_demo_user --target-user Zathu --password mypassword123
```

---

### Typical Full Workflow

```bash
# 1. Run the freeze demo command with full options:
python manage.py freeze_demo --user Zathu --emulate-date 2026-08-10

# 2. Preview locally:
python -m http.server -d docs 8000

# 3. Commit and deploy:
git add docs/ static/img/screenshots/
git commit -m "chore: update static demo and screenshot showcase"
git push origin main
```
