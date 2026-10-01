# 🎸 Setlore Static Demo Generator

This directory documents the developer command-line tool for generating a static "demo" flavor of Setlore suitable for hosting on **GitHub Pages** (or any static hosting provider like Cloudflare Pages, Netlify, or Vercel).

---

## 📖 Overview & Architecture

Setlore allows you to freeze a fully interactive, static version of:
1. **The Landing Page** (`index.html`): Configured in the **Demo Flavor**.
   - Removes live server features (Sign In, User Registration, and Community Server Leaderboard).
   - Only provides actions to explore the interactive demo (**"Demo Setlore"**) or install the project (**"Install via GitHub"**).
2. **Selected User Public Profile** (`demo/index.html` & `demo.html`):
   - Standalone, self-contained concert dashboard populated with real data from a chosen user.
   - Includes interactive Plotly discography treemaps, album breakdown matrices, Leaflet dark-tile venue maps, artist drilldowns, and musician lineups.
   - Preserves client-side tab switching via hash routing (`#overview`, `#concerts`, `#drilldown`, `#musicians`, `#map`, `#advanced`, `#setlists`, `#gap`).
   - Includes a direct static CSV export of the demo user's concert history (`concerts.csv`).
3. **Static Privacy Policy** (`privacy/index.html` & `privacy.html`).
4. **Static Assets & GitHub Pages Config**:
   - Bundles `static/` (icons, banners, web manifest).
   - Automatically generates `.nojekyll` to disable Jekyll processing on GitHub Pages.

> [!NOTE]
> **Maintainability Guarantee (No Forking)**: The static pages share the exact same template files (`templates/home.html`, `templates/dashboard.html`, `templates/privacy.html`) as the live Django application via the `is_demo` context variable.

---

## 🛠️ CLI Usage

The generator is invoked exclusively by developers through the Django management command:

```bash
# Basic usage: Auto-selects the active user with the most concerts and builds to 'docs/'
python manage.py freeze_demo
```

*(You can also use the shorter alias `python manage.py freeze`)*.

---

### ⚙️ Command Options & Flags

| Flag | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-u` | `--user`, `--username` | *(Auto)* | Username to freeze as the public demo. Defaults to the active user with the most concerts logged. |
| `-o` | `--output-dir` | `docs` | Destination directory for generated static files. |
| `-r` | `--repo-url` | `https://github.com/cgarst/setlore` | GitHub repository URL used for "Install" and source links. |
| | `--no-clean` | `False` | Do not wipe the output directory before generating files. |

---

### 💡 Examples

#### 1. Freeze a specific user's public profile
```bash
python manage.py freeze_demo --user Zathu
```

#### 2. Output to a custom directory (e.g. `dist/` or `demo_build/`)
```bash
python manage.py freeze_demo -u Zathu -o dist
```

#### 3. Custom repository link
```bash
python manage.py freeze_demo --repo-url https://github.com/my-org/my-concert-fork
```

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
- **Direct Tab Deep Linking**: [http://localhost:8000/demo/#map](http://localhost:8000/demo/#map), [http://localhost:8000/demo/#musicians](http://localhost:8000/demo/#musicians), [http://localhost:8000/demo/#drilldown](http://localhost:8000/demo/#drilldown)

---

## 🌐 Deploying to GitHub Pages

1. Run the generator to update the `docs/` folder:
   ```bash
   python manage.py freeze_demo
   ```
2. Commit and push the `docs/` directory to your GitHub repository:
   ```bash
   git add docs/
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

The static demo is populated from a **real user's concert history**. Because this data can be large and is user-specific, the cache file (`data/cache/demo_user_cache.json`) is **gitignored** and must be generated locally before running `freeze_demo`.

> [!NOTE]
> The cache is a portable, ID-collision-safe JSON snapshot. It can be safely loaded into any database — blank or production mirror — without conflicting with existing records.

### Generating the Cache

Export a user's full concert history into the cache file:

```bash
# Export 'demouser' (the default) to the default cache path
python manage.py dump_demo_cache

# Export a specific user
python manage.py dump_demo_cache --user Zathu

# Export to a custom path
python manage.py dump_demo_cache --user Zathu --output /path/to/my_cache.json

# Export from a non-default database alias
python manage.py dump_demo_cache --user demouser --database replica
```

#### `dump_demo_cache` Options

| Flag | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-u` | `--user`, `--username` | `demouser` | Username whose data to export. |
| `-o` | `--output` | `data/cache/demo_user_cache.json` | Output path for the JSON cache file. |
| | `--database` | `default` | Source Django database alias to read from. |

---

### Seeding the Cache into a Database

Use `populate_demo_user` to load a cache file into any target database. This is useful for:

- **Blank-slate testing**: Start with a fresh DB and instantly have a fully populated demo user.
- **Production mirror testing**: Load demo data into a staging DB alongside real users without collisions.
- **CI/CD seeding**: Populate a test environment with known fixture data.

```bash
# Populate demouser from the default cache (creates user if missing)
python manage.py populate_demo_user

# Populate into a different username
python manage.py populate_demo_user --target-user testdemo

# Load from a custom cache file
python manage.py populate_demo_user --input /path/to/my_cache.json

# Populate and grant staff/superuser access
python manage.py populate_demo_user --superuser

# Set the user's password (used if user is newly created)
python manage.py populate_demo_user --password mypassword123

# Overwrite existing concert data for the target user
python manage.py populate_demo_user --overwrite

# Populate into a non-default database
python manage.py populate_demo_user --database staging
```

#### `populate_demo_user` Options

| Flag | Long Option | Default | Description |
| :--- | :--- | :--- | :--- |
| `-i` | `--input` | `data/cache/demo_user_cache.json` | Path to the cache file to load. |
| | `--target-user` | *(username from cache)* | Target username to populate into. |
| | `--database` | `default` | Target Django database alias. |
| | `--overwrite` | `False` | Replace existing concerts for the target user. |
| | `--superuser` | `False` | Grant superuser + staff permissions to the user. |
| | `--password` | `demopassword123` | Password if the user is newly created. |

---

### Typical Workflow

```bash
# 1. Export your local demouser's data to the cache
python manage.py dump_demo_cache --user demouser

# 2. (Optional) Verify the export
cat data/cache/demo_user_cache.json | python -m json.tool | head -30

# 3. Generate the static demo using that user's data
python manage.py freeze_demo --user demouser
```

To quickly spin up a populated test environment from scratch:

```bash
# On a fresh database (after migrations):
python manage.py populate_demo_user --password demopassword123

# Then generate the static demo:
python manage.py freeze_demo --user demouser
```

