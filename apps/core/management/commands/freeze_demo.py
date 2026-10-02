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
            "--emulate-date",
            dest="emulate_date",
            default="2026-08-10",
            help="Emulate a specific date (YYYY-MM-DD) so 'On This Day' anniversaries are populated. (Default: 2026-08-10).",
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
        emulate_date = options.get("emulate_date", "2026-08-10") or "2026-08-10"
        no_clean = options.get("no_clean", False)
        skip_screenshots = options.get("skip_screenshots", False)

        base_dir = Path(settings.BASE_DIR)
        out_dir = Path(output_dir_name)
        if not out_dir.is_absolute():
            out_dir = base_dir / out_dir

        self.stdout.write(self.style.MIGRATE_HEADING("=== Setlore Static Demo Builder ==="))

        # 1. Resolve Target User (Defaults to Zathu, demouser, or active user)
        if username:
            target_user = User.objects.filter(username__iexact=username.strip()).first()
            if not target_user:
                raise CommandError(f"User with username '{username}' was not found in the database.")
        else:
            # Prefer 'Zathu' if present, otherwise 'demouser', otherwise active user with most concerts
            target_user = User.objects.filter(username__iexact="Zathu").first()
            if not target_user:
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
            res = re.sub(r'([\'"])/static/', r'\1' + prefix, html_content)
            res = re.sub(r'url\([\'"]?/static/', r'url(' + prefix, res)
            return res

        rf = RequestFactory()
        generated_files = []

        # 4. Generate User Public Profile Demo Page First (with emulated anniversary date)
        self.stdout.write(f"Generating public profile demo for @{target_user.username} (emulating {emulate_date})...")
        req_profile = rf.get(f"/u/{target_user.username}/?emulate_date={emulate_date}", HTTP_HOST="localhost")
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
            self.stdout.write(self.style.MIGRATE_HEADING(f"Refreshing screenshots via live server (logged in as @{target_user.username})..."))
            screenshots_src_dir = base_dir / "static" / "img" / "screenshots"
            screenshots_dst_dir = out_dir / "static" / "img" / "screenshots"
            screenshots_src_dir.mkdir(parents=True, exist_ok=True)
            screenshots_dst_dir.mkdir(parents=True, exist_ok=True)

            try:
                self._capture_demo_screenshots(
                    target_user.username,
                    screenshots_src_dir,
                    screenshots_dst_dir,
                    emulate_date=emulate_date,
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

    def _capture_demo_screenshots(self, username: str, src_dir: Path, dst_dir: Path, emulate_date: str = "2026-08-10"):
        """Spins up a temporary Django WSGI server, logs in as the target user via a
        force-login session cookie, and screenshots the authenticated dashboard pages
        in both Desktop (1440x900) and Mobile (390x844) viewports.

        Screens captured:
        - overview.png / overview_mobile.png: Overview dashboard with 'On This Day' anniversaries (emulating anniversary date)
        - concerts.png / concerts_mobile.png: Setlist modal for Haken at Cafe 611
        - artists.png / artists_mobile.png: Artist modal for Megadeth
        - songs.png / songs_mobile.png: Song modal for Demon of the Fall (Opeth)
        - albums.png / albums_mobile.png: Albums gallery with 2000s era selected
        - album_modal.png / album_modal_mobile.png: Album modal for Train of Thought (Dream Theater)
        - musicians.png / musicians_mobile.png: Musicians lineup tracker
        - map.png / map_mobile.png: Interactive venue map
        - freshness.png / freshness_mobile.png: Song freshness & rarities
        - theme_palettes.png: Theme selector modal
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

            # Find Haken at Cafe 611 concert ID dynamically if available
            haken_concert = target_user.concerts.filter(venue__name__icontains="Cafe 611", raw_artists__icontains="Haken").first()
            haken_concert_id = str(haken_concert.id) if haken_concert else "55"

            FORCE_DEFAULT_THEME_JS = """
                () => {
                    try { localStorage.removeItem('setlore_theme'); } catch(e) {}
                    document.documentElement.setAttribute('data-theme', 'Mystic Dream');
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

            viewports = [
                ("desktop", {"width": 1440, "height": 900}, False, False, ""),
                ("mobile", {"width": 390, "height": 844}, True, True, "_mobile"),
            ]

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-dev-shm-usage", "--no-sandbox"],
                )

                for vp_name, vp_dict, is_mob, has_tch, suffix in viewports:
                    self.stdout.write(f"  [playwright] Capturing {vp_name} screenshots (viewport={vp_dict['width']}x{vp_dict['height']})...")
                    context = browser.new_context(
                        viewport=vp_dict,
                        is_mobile=is_mob,
                        has_touch=has_tch,
                        device_scale_factor=2 if is_mob else 1,
                    )
                    context.add_cookies([{
                        "name": "sessionid",
                        "value": session_key,
                        "domain": "127.0.0.1",
                        "path": "/",
                        "httpOnly": True,
                        "secure": False,
                    }])

                    page = context.new_page()
                    page.goto(f"{base_url}/overview/?emulate_date={emulate_date}", wait_until="domcontentloaded", timeout=60000)
                    try:
                        page.wait_for_function("() => typeof switchTab === 'function'", timeout=30000)
                    except Exception:
                        page.wait_for_timeout(2000)
                    page.evaluate(FORCE_DEFAULT_THEME_JS)

                    def _show_tab(tab_name="overview", scroll_below_nav=True):
                        page.evaluate("if (typeof closeAllModals === 'function') closeAllModals();")
                        page.evaluate(f"if (typeof switchTab === 'function') switchTab('{tab_name}');")
                        page.wait_for_timeout(600)
                        page.evaluate(FORCE_DEFAULT_THEME_JS)
                        if scroll_below_nav and not is_mob:
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
                        else:
                            page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
                        page.wait_for_timeout(300)

                    # ── 1. overview.png (with On This Day anniversary banner) ──
                    _show_tab("overview", scroll_below_nav=False)
                    page.wait_for_timeout(500)
                    _save(page, f"overview{suffix}.png")
                    self.stdout.write(f"    + overview{suffix}.png")

                    # ── 2. concerts.png: Setlist modal for Haken at Cafe 611 ──
                    _show_tab("concerts", scroll_below_nav=True)
                    page.evaluate(f"""
                        () => {{
                            if (typeof openConcertModal === 'function') {{
                                openConcertModal('{haken_concert_id}', 1);
                            }}
                        }}
                    """)
                    page.wait_for_timeout(700)
                    _wait_for_album_art(
                        page,
                        selector="#concert-modal-body img.lazy-album-art, #concert-modal img",
                        min_loaded=1,
                        timeout_ms=5000,
                    )
                    _save(page, f"concerts{suffix}.png")
                    self.stdout.write(f"    + concerts{suffix}.png (Haken Cafe 611 modal)")
                    page.evaluate("if (typeof closeAllModals === 'function') closeAllModals();")

                    # ── 3. artists.png: Artist modal for Megadeth ─────────────
                    _show_tab("songs", scroll_below_nav=True)
                    page.evaluate("""
                        () => {
                            if (typeof openArtistSongModal === 'function') {
                                openArtistSongModal('Megadeth', '');
                            }
                        }
                    """)
                    page.wait_for_timeout(700)
                    _wait_for_album_art(page, selector="#artist-modal-body img, #artist-song-modal img", min_loaded=1, timeout_ms=5000)
                    _save(page, f"artists{suffix}.png")
                    self.stdout.write(f"    + artists{suffix}.png (Megadeth artist modal)")
                    page.evaluate("if (typeof closeAllModals === 'function') closeAllModals();")

                    # ── 4. songs.png: Song modal for Demon of the Fall (Opeth) ─
                    _show_tab("songs", scroll_below_nav=True)
                    page.evaluate("""
                        () => {
                            if (typeof openArtistSongModal === 'function') {
                                openArtistSongModal('Opeth', 'Demon of the Fall');
                            }
                        }
                    """)
                    page.wait_for_timeout(700)
                    _wait_for_album_art(page, selector="#artist-modal-body img, #artist-song-modal img", min_loaded=1, timeout_ms=5000)
                    _save(page, f"songs{suffix}.png")
                    self.stdout.write(f"    + songs{suffix}.png (Demon of the Fall song modal)")
                    page.evaluate("if (typeof closeAllModals === 'function') closeAllModals();")

                    # ── 5. albums.png: Albums gallery with 2000s era selected ──
                    _show_tab("albums", scroll_below_nav=True)
                    page.evaluate("""
                        () => {
                            if (typeof setAlbumGalleryDecade === 'function') {
                                setAlbumGalleryDecade('2000s');
                            }
                        }
                    """)
                    page.wait_for_timeout(600)
                    _wait_for_album_art(page, selector=".album-cover-img", min_loaded=3, timeout_ms=8000)
                    _save(page, f"albums{suffix}.png")
                    self.stdout.write(f"    + albums{suffix}.png (2000s era)")

                    # ── 6. album_modal.png: Album modal for Train of Thought (Dream Theater) ─
                    page.evaluate("""
                        () => {
                            if (typeof openAlbumModalByName === 'function') {
                                openAlbumModalByName('Dream Theater', 'Train of Thought');
                            }
                        }
                    """)
                    page.wait_for_timeout(700)
                    _wait_for_album_art(page, selector="#album-modal-body img, #album-modal img", min_loaded=1, timeout_ms=5000)
                    _save(page, f"album_modal{suffix}.png")
                    self.stdout.write(f"    + album_modal{suffix}.png (Train of Thought album modal)")
                    page.evaluate("if (typeof closeAllModals === 'function') closeAllModals();")

                    # ── 7. musicians.png ──────────────────────────────────────
                    _show_tab("musicians", scroll_below_nav=True)
                    page.wait_for_timeout(500)
                    _save(page, f"musicians{suffix}.png")
                    self.stdout.write(f"    + musicians{suffix}.png")

                    # ── 8. map.png ────────────────────────────────────────────
                    _show_tab("map", scroll_below_nav=True)
                    page.wait_for_timeout(1500)
                    _save(page, f"map{suffix}.png")
                    self.stdout.write(f"    + map{suffix}.png")

                    # ── 9. freshness.png ──────────────────────────────────────
                    _show_tab("freshness", scroll_below_nav=True)
                    page.wait_for_timeout(500)
                    _save(page, f"freshness{suffix}.png")
                    self.stdout.write(f"    + freshness{suffix}.png")

                    # ── 10. theme_palettes.png ────────────────────────────────
                    _show_tab("overview", scroll_below_nav=False)
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
                    if not is_mob:
                        modal_card = page.query_selector("#theme-modal > div")
                        target = src_dir / "theme_palettes.png"
                        if modal_card:
                            modal_card.screenshot(path=str(target))
                            shutil.copy2(target, dst_dir / "theme_palettes.png")
                        else:
                            _save(page, "theme_palettes.png")
                    else:
                        _save(page, "theme_palettes_mobile.png")
                    self.stdout.write(f"    + theme_palettes{suffix}.png")

                    context.close()

                browser.close()

        finally:
            httpd.shutdown()
            self.stdout.write("  [server] Stopped.")
