import csv
import io
import os
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser, User
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.concerts.views import get_dashboard_context


class Command(BaseCommand):
    help = (
        "Generates a static 'demo' build of Setlore (landing page + public profile of demouser) "
        "tailored for static hosting such as GitHub Pages, and refreshes UI screenshots."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "-u",
            "--user",
            "--username",
            dest="username",
            default=None,
            help="Username of the user whose public profile will be frozen as the demo. (Defaults to 'demouser' or user with most concerts).",
        )
        parser.add_argument(
            "-o",
            "--output-dir",
            dest="output_dir",
            default="docs",
            help="Destination directory for static files (default: docs).",
        )
        parser.add_argument(
            "-r",
            "--repo-url",
            dest="repo_url",
            default="https://github.com/cgarst/setlore",
            help="URL to the GitHub repository for installation and source links.",
        )
        parser.add_argument(
            "--no-clean",
            action="store_true",
            help="Do not clean the output directory before generating files.",
        )
        parser.add_argument(
            "--skip-screenshots",
            "--no-screenshots",
            action="store_true",
            dest="skip_screenshots",
            help="Skip regenerating browser screenshots of demo pages.",
        )

    def handle(self, *args, **options):
        username = options.get("username")
        output_dir_name = options.get("output_dir", "docs")
        repo_url = options.get("repo_url", "https://github.com/cgarst/setlore")
        no_clean = options.get("no_clean", False)
        skip_screenshots = options.get("skip_screenshots", False)

        base_dir = Path(settings.BASE_DIR)
        out_dir = Path(output_dir_name)
        if not out_dir.is_absolute():
            out_dir = base_dir / out_dir

        self.stdout.write(self.style.MIGRATE_HEADING("=== Setlore Static Demo Builder ==="))

        # 1. Resolve Target User (Defaults to demouser)
        if username:
            target_user = User.objects.filter(username__iexact=username.strip()).first()
            if not target_user:
                raise CommandError(f"User with username '{username}' was not found in the database.")
        else:
            # Prefer 'demouser' if present, otherwise active user with most concerts
            target_user = User.objects.filter(username__iexact="demouser").first()
            if not target_user:
                target_user = (
                    User.objects.filter(is_active=True)
                    .annotate(concert_count=Count("concerts", distinct=True))
                    .filter(concert_count__gt=0)
                    .order_by("-concert_count", "id")
                    .first()
                )
            if not target_user:
                target_user = User.objects.filter(is_superuser=True).first() or User.objects.first()

            if not target_user:
                raise CommandError(
                    "No users found in database. Please seed initial user data first (e.g., 'python manage.py seed_user_one' or 'python manage.py populate_demo_user')."
                )

        concert_count = target_user.concerts.count()
        self.stdout.write(
            f"Selected Demo User: {self.style.SUCCESS(target_user.username)} ({concert_count} concerts logged)"
        )
        self.stdout.write(f"Target Output Directory: {self.style.SUCCESS(str(out_dir))}")
        self.stdout.write(f"Repository URL: {self.style.SUCCESS(repo_url)}")

        # 2. Prepare Output Directory
        if out_dir.exists() and not no_clean:
            self.stdout.write(f"Cleaning existing directory: {out_dir}")
            for item in out_dir.iterdir():
                if item.name == ".git":
                    continue  # preserve git repository metadata if publishing from branch
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "demo").mkdir(exist_ok=True)
        (out_dir / "privacy").mkdir(exist_ok=True)

        # 3. Copy Static Assets
        static_source = base_dir / "static"
        static_target = out_dir / "static"
        if static_source.exists():
            if static_target.exists():
                shutil.rmtree(static_target)
            shutil.copytree(static_source, static_target)
            self.stdout.write(self.style.SUCCESS(f"Copied static assets from {static_source} -> {static_target}"))

        # Create .nojekyll for GitHub Pages compatibility
        (out_dir / ".nojekyll").touch()

        # Helper for rewriting absolute static URLs to relative paths
        def make_relative_static(html_content: str, depth: int = 0) -> str:
            prefix = ("../" * depth) + "static/"
            pattern = r'([\'"/])/static/'
            
            def repl(match):
                delimiter = match.group(1)
                if delimiter == '/':
                    return '/' + prefix
                return delimiter + prefix

            res = re.sub(r'href=([\'"])/static/', r'href=\1' + prefix, html_content)
            res = re.sub(r'src=([\'"])/static/', r'src=\1' + prefix, res)
            res = re.sub(r'url\([\'"]?/static/', r'url(' + prefix, res)
            return res

        rf = RequestFactory()
        generated_files = []

        # 4. Generate User Public Profile Demo Page First (Needed for Screenshots)
        self.stdout.write(f"Generating public profile demo for @{target_user.username}...")
        req_profile = rf.get(f"/u/{target_user.username}/", HTTP_HOST="localhost")
        req_profile.user = AnonymousUser()

        demo_context = get_dashboard_context(req_profile, target_user, is_public_view=True)
        demo_context.update({
            "is_demo": True,
            "is_public_view": True,
            "is_owner": False,
            "repo_url": repo_url,
            "home_url": "../index.html",
            "demo_url": "index.html",
            "static_csv_url": "concerts.csv",
            "share_url": repo_url,
        })

        # Render for demo/index.html (depth=1)
        profile_html_sub = render_to_string("dashboard.html", demo_context, request=req_profile)
        profile_html_sub = make_relative_static(profile_html_sub, depth=1)

        demo_index_file = out_dir / "demo" / "index.html"
        demo_index_file.write_text(profile_html_sub, encoding="utf-8")
        generated_files.append((demo_index_file, len(profile_html_sub)))

        # Render for root demo.html (depth=0)
        demo_context_root = dict(demo_context)
        demo_context_root["home_url"] = "index.html"
        demo_context_root["demo_url"] = "demo.html"
        demo_context_root["static_csv_url"] = "demo/concerts.csv"

        profile_html_root = render_to_string("dashboard.html", demo_context_root, request=req_profile)
        profile_html_root = make_relative_static(profile_html_root, depth=0)

        demo_root_file = out_dir / "demo.html"
        demo_root_file.write_text(profile_html_root, encoding="utf-8")
        generated_files.append((demo_root_file, len(profile_html_root)))

        # 5. Capture Fresh UI Screenshots from Authenticated Live Server
        if not skip_screenshots:
            self.stdout.write(self.style.MIGRATE_HEADING("Refreshing screenshots via live server (logged in as demouser)..."))
            screenshots_src_dir = base_dir / "static" / "img" / "screenshots"
            screenshots_dst_dir = out_dir / "static" / "img" / "screenshots"
            screenshots_src_dir.mkdir(parents=True, exist_ok=True)
            screenshots_dst_dir.mkdir(parents=True, exist_ok=True)

            try:
                self._capture_demo_screenshots(
                    target_user.username,
                    screenshots_src_dir,
                    screenshots_dst_dir,
                )
            except Exception as exc:
                import traceback
                self.stdout.write(self.style.WARNING(f"Screenshot generation notice: {exc}"))
                self.stdout.write(self.style.WARNING(traceback.format_exc()))

        else:
            self.stdout.write(self.style.WARNING("Skipping screenshot generation as requested."))

        # 6. Generate Static Landing Page (Demo Flavor)
        self.stdout.write("Generating landing page (Demo flavor)...")
        req_home = rf.get("/", HTTP_HOST="localhost")
        req_home.user = AnonymousUser()

        home_context = {
            "is_demo": True,
            "repo_url": repo_url,
            "demo_url": "demo/",
            "home_url": "index.html",
            "top_users": [],
            "registration_enabled": False,
            "google_oauth_enabled": False,
        }
        home_html = render_to_string("home.html", home_context, request=req_home)
        home_html = make_relative_static(home_html, depth=0)

        home_file = out_dir / "index.html"
        home_file.write_text(home_html, encoding="utf-8")
        generated_files.append((home_file, len(home_html)))

        # 7. Generate Static Concerts CSV Export
        self.stdout.write("Generating static concerts CSV export...")
        concerts_drilldown = demo_context.get("stats", {}).get("concerts_drilldown", [])
        chronological_concerts = list(reversed(concerts_drilldown))

        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        csv_writer.writerow(["Date", "Artist(s)", "Venue", "Setlist"])

        for c in chronological_concerts:
            date_str = c.get("date", "") or ""
            artist_str = c.get("primary_artist", "") or ""
            sup = c.get("supporting_artists", "")
            if sup:
                artist_str = f"{artist_str} (with {sup})"
            v_name = c.get("venue", "") or ""
            v_city = c.get("city", "") or ""
            venue_str = f"{v_name} - {v_city}" if (v_name and v_city) else (v_name or v_city)

            songs = c.get("songs", [])
            setlist_str = "; ".join(s.get("song", "") for s in songs if s.get("song"))
            csv_writer.writerow([date_str, artist_str, venue_str, setlist_str])

        csv_content = csv_buffer.getvalue()
        demo_csv_file = out_dir / "demo" / "concerts.csv"
        demo_csv_file.write_text(csv_content, encoding="utf-8")
        generated_files.append((demo_csv_file, len(csv_content.encode("utf-8"))))

        root_csv_file = out_dir / "concerts.csv"
        root_csv_file.write_text(csv_content, encoding="utf-8")
        generated_files.append((root_csv_file, len(csv_content.encode("utf-8"))))

        # 8. Generate Static Privacy Policy
        self.stdout.write("Generating static privacy page...")
        req_privacy = rf.get("/privacy/", HTTP_HOST="localhost")
        req_privacy.user = AnonymousUser()

        privacy_context = {
            "is_demo": True,
            "repo_url": repo_url,
            "home_url": "../index.html",
        }
        privacy_html_sub = render_to_string("privacy.html", privacy_context, request=req_privacy)
        privacy_html_sub = make_relative_static(privacy_html_sub, depth=1)

        privacy_sub_file = out_dir / "privacy" / "index.html"
        privacy_sub_file.write_text(privacy_html_sub, encoding="utf-8")
        generated_files.append((privacy_sub_file, len(privacy_html_sub)))

        privacy_context_root = dict(privacy_context)
        privacy_context_root["home_url"] = "index.html"
        privacy_html_root = render_to_string("privacy.html", privacy_context_root, request=req_privacy)
        privacy_html_root = make_relative_static(privacy_html_root, depth=0)

        privacy_root_file = out_dir / "privacy.html"
        privacy_root_file.write_text(privacy_html_root, encoding="utf-8")
        generated_files.append((privacy_root_file, len(privacy_html_root)))

        # 9. Summary Table
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Build Summary ==="))
        for f_path, f_size in generated_files:
            rel_path = f_path.relative_to(out_dir)
            size_kb = f_size / 1024.0
            if size_kb >= 1024:
                size_str = f"{size_kb / 1024.0:.2f} MB"
            else:
                size_str = f"{size_kb:.1f} KB"
            self.stdout.write(f"  + {str(rel_path):<30} [{size_str:>9}]")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSuccessfully generated static demo in '{out_dir}'!\n"
                f"To preview locally:\n"
                f"  python -m http.server -d {output_dir_name} 8000\n"
                f"  Then open: http://localhost:8000\n"
            )
        )

    def _capture_demo_screenshots(self, username: str, src_dir: Path, dst_dir: Path):
        """Spins up a temporary Django WSGI server, logs in as the target user via a
        force-login session cookie, and screenshots the authenticated dashboard pages.

        Special handling per page:
        - concerts.png : expands the first concert card, then waits for album art thumbnails.
        - songs.png    : selects the top-seen artist, then waits for song album art.
        - theme_palettes.png : opens the theme chooser modal.
        All screenshots are forced to the "Default" theme via JS regardless of any saved
        localStorage preference.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.stdout.write(
                self.style.WARNING("Playwright is not installed. Skipping automatic screenshot capture.")
            )
            return

        import socket
        import threading

        from django.contrib.sessions.backends.db import SessionStore
        from django.contrib.auth import SESSION_KEY, BACKEND_SESSION_KEY, HASH_SESSION_KEY
        from django.contrib.auth.models import User

        # ── 1. Find a free port ────────────────────────────────────────────────
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        base_url = f"http://127.0.0.1:{port}"

        # ── 2. Start a WSGI server thread ──────────────────────────────────────
        from wsgiref.simple_server import make_server, WSGIServer, WSGIRequestHandler
        from django.core.handlers.wsgi import WSGIHandler
        import logging

        class _QuietHandler(WSGIRequestHandler):
            def log_message(self, fmt, *args):
                pass  # suppress per-request stdout noise

        class _ReuseServer(WSGIServer):
            allow_reuse_address = True

        httpd = make_server("127.0.0.1", port, WSGIHandler(), server_class=_ReuseServer, handler_class=_QuietHandler)
        server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        server_thread.start()
        self.stdout.write(f"  [server] Listening on {base_url}")

        try:
            # ── 3. Authenticate via test client session cookie ────────────────
            from django.test import Client
            test_client = Client()
            target_user = User.objects.get(username=username)
            test_client.force_login(target_user)
            session_key = test_client.cookies['sessionid'].value
            self.stdout.write(f"  [auth] Created authenticated session for @{username}")

            FORCE_DEFAULT_THEME_JS = """
                () => {
                    try { localStorage.removeItem('setlore_theme'); } catch(e) {}
                    document.documentElement.setAttribute('data-theme', 'Default');
                }
            """

            def _wait_for_album_art(page, selector="img.lazy-album-art", min_loaded=1, timeout_ms=8000):
                try:
                    page.wait_for_function(
                        f"""
                        () => {{
                            const imgs = Array.from(document.querySelectorAll('{selector}'));
                            if (imgs.length === 0) return true;
                            return imgs.filter(img =>
                                img.dataset.loaded === 'true' || !img.classList.contains('opacity-0')
                            ).length >= {min_loaded};
                        }}
                        """,
                        timeout=timeout_ms,
                    )
                except Exception:
                    pass

            def _save(page, filename):
                target = src_dir / filename
                page.screenshot(path=str(target))
                shutil.copy2(target, dst_dir / filename)

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                )
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                context.add_cookies([{
                    "name": "sessionid",
                    "value": session_key,
                    "domain": "127.0.0.1",
                    "path": "/",
                    "httpOnly": True,
                    "secure": False,
                }])

                def _new_page(tab_name="overview", scroll_below_nav=True):
                    page = context.new_page()
                    page.goto(f"{base_url}/overview/", wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_function("() => typeof switchTab === 'function'", timeout=30000)
                    except Exception:
                        page.wait_for_timeout(2000)
                    page.evaluate(FORCE_DEFAULT_THEME_JS)
                    if tab_name != "overview":
                        page.evaluate(f"switchTab('{tab_name}')")
                        page.wait_for_timeout(600)
                    page.wait_for_timeout(300)
                    if scroll_below_nav:
                        page.evaluate("""
                            () => {
                                const nav = document.getElementById('desktop-nav-bar');
                                if (nav) {
                                    const rect = nav.getBoundingClientRect();
                                    const topOffset = window.pageYOffset + rect.bottom + 12;
                                    window.scrollTo({ top: topOffset, behavior: 'instant' });
                                }
                            }
                        """)
                        page.wait_for_timeout(300)
                    return page

                # ── overview.png (full view with nav bar) ──────────────────────
                page = _new_page("overview", scroll_below_nav=False)
                try:
                    page.wait_for_timeout(400)
                    _save(page, "overview.png")
                    self.stdout.write("  [screenshot] Captured overview.png")
                finally:
                    page.close()

                # ── concerts.png: expand first card + wait for album art ──────
                page = _new_page("concerts", scroll_below_nav=True)
                try:
                    first_card_id = page.evaluate("""
                        () => {
                            const details = document.querySelector('[id^="concert-details-"]');
                            if (details) return details.id.replace('concert-details-', '');
                            const card = document.querySelector('.concert-card[id^="concert-card-"]');
                            if (card) return card.id.replace('concert-card-', '');
                            return null;
                        }
                    """)
                    if first_card_id:
                        page.evaluate(f"toggleConcertDetails('{first_card_id}')")
                        page.wait_for_timeout(600)
                        page.evaluate(f"""
                            (() => {{
                                const el = document.getElementById('concert-details-{first_card_id}');
                                if (el && typeof initLazyAlbumThumbnails === 'function') {{
                                    initLazyAlbumThumbnails(el);
                                }}
                            }})()
                        """)
                        _wait_for_album_art(
                            page,
                            selector=f"#concert-details-{first_card_id} img.lazy-album-art",
                            min_loaded=1,
                            timeout_ms=8000,
                        )
                    _save(page, "concerts.png")
                    self.stdout.write(f"  [screenshot] Captured concerts.png (card {first_card_id} expanded)")
                finally:
                    page.close()

                # ── songs.png: select Primus artist + wait for album art ──────
                page = _new_page("songs", scroll_below_nav=True)
                try:
                    selected_artist = page.evaluate("""
                        () => {
                            const btns = Array.from(document.querySelectorAll('#artist-button-grid .artist-btn, .artist-btn'));
                            const primusBtn = btns.find(b => (b.dataset.name || b.textContent).toLowerCase().includes('primus'));
                            const targetBtn = primusBtn || btns[1] || btns[0];
                            if (!targetBtn) return null;
                            const span = targetBtn.querySelector('span:first-child');
                            return span ? span.textContent.trim() : (targetBtn.dataset.name || targetBtn.textContent.trim());
                        }
                    """)
                    if selected_artist:
                        page.evaluate(f"selectArtist({repr(selected_artist)})")
                        page.wait_for_timeout(800)
                        _wait_for_album_art(page, selector="img.lazy-album-art", min_loaded=1, timeout_ms=8000)
                    _save(page, "songs.png")
                    self.stdout.write(f"  [screenshot] Captured songs.png (artist '{selected_artist}' selected)")
                finally:
                    page.close()

                # ── albums.png ────────────────────────────────────────────────
                page = _new_page("albums", scroll_below_nav=True)
                try:
                    _wait_for_album_art(page, selector=".album-cover-img", min_loaded=3, timeout_ms=8000)
                    _save(page, "albums.png")
                    self.stdout.write("  [screenshot] Captured albums.png")
                finally:
                    page.close()

                # ── musicians.png ─────────────────────────────────────────────
                page = _new_page("musicians", scroll_below_nav=True)
                try:
                    _save(page, "musicians.png")
                    self.stdout.write("  [screenshot] Captured musicians.png")
                finally:
                    page.close()

                # ── map.png ───────────────────────────────────────────────────
                page = _new_page("map", scroll_below_nav=True)
                try:
                    page.wait_for_timeout(1500)
                    _save(page, "map.png")
                    self.stdout.write("  [screenshot] Captured map.png")
                finally:
                    page.close()

                # ── freshness.png ─────────────────────────────────────────────
                page = _new_page("freshness", scroll_below_nav=True)
                try:
                    _save(page, "freshness.png")
                    self.stdout.write("  [screenshot] Captured freshness.png")
                finally:
                    page.close()

                # ── theme_palettes.png: open theme modal and crop to dialog ───
                page = _new_page("overview", scroll_below_nav=False)
                try:
                    page.evaluate("""
                        () => {
                            if (typeof openThemeModal === 'function') {
                                openThemeModal();
                            } else {
                                const btn = document.getElementById('theme-menu-btn') || document.querySelector('[onclick*="openThemeModal"]');
                                if (btn) btn.click();
                            }
                        }
                    """)
                    page.wait_for_timeout(600)
                    modal_card = page.query_selector("#theme-modal > div")
                    target = src_dir / "theme_palettes.png"
                    if modal_card:
                        modal_card.screenshot(path=str(target))
                        shutil.copy2(target, dst_dir / "theme_palettes.png")
                    else:
                        _save(page, "theme_palettes.png")
                    self.stdout.write("  [screenshot] Captured theme_palettes.png (cropped to modal)")
                finally:
                    page.close()

                browser.close()

        finally:
            httpd.shutdown()
            self.stdout.write("  [server] Stopped.")
