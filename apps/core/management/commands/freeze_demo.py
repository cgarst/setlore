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
        "Generates a static 'demo' build of Setlore (landing page + public profile of a selected user) "
        "tailored for static hosting such as GitHub Pages."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "-u",
            "--user",
            "--username",
            dest="username",
            default=None,
            help="Username of the user whose public profile will be frozen as the demo. (Defaults to user with most concerts).",
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
            default="https://github.com/cgarst/concert-trakr",
            help="URL to the GitHub repository for installation and source links.",
        )
        parser.add_argument(
            "--no-clean",
            action="store_true",
            help="Do not clean the output directory before generating files.",
        )

    def handle(self, *args, **options):
        username = options.get("username")
        output_dir_name = options.get("output_dir", "docs")
        repo_url = options.get("repo_url", "https://github.com/cgarst/concert-trakr")
        no_clean = options.get("no_clean", False)

        base_dir = Path(settings.BASE_DIR)
        out_dir = Path(output_dir_name)
        if not out_dir.is_absolute():
            out_dir = base_dir / out_dir

        self.stdout.write(self.style.MIGRATE_HEADING("=== Setlore Static Demo Builder ==="))

        # 1. Resolve Target User
        if username:
            target_user = User.objects.filter(username__iexact=username.strip()).first()
            if not target_user:
                raise CommandError(f"User with username '{username}' was not found in the database.")
        else:
            # Pick active user with the most concerts, or superuser, or first user
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
                    "No users found in database. Please seed initial user data first (e.g., 'python manage.py seed_user_one')."
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
            # Replace /static/ or "/static/ with prefix
            # Handles href="/static/...", src="/static/...", url('/static/...')
            pattern = r'([\'"/])/static/'
            
            def repl(match):
                delimiter = match.group(1)
                if delimiter == '/':
                    return '/' + prefix
                return delimiter + prefix

            # Standard attribute replacements
            res = re.sub(r'href=([\'"])/static/', r'href=\1' + prefix, html_content)
            res = re.sub(r'src=([\'"])/static/', r'src=\1' + prefix, res)
            res = re.sub(r'url\([\'"]?/static/', r'url(' + prefix, res)
            return res

        rf = RequestFactory()
        generated_files = []

        # 4. Generate Static Landing Page (Demo Flavor)
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

        # 5. Generate User Public Profile Demo Page
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

        # 6. Generate Static Concerts CSV Export
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

        # 7. Generate Static Privacy Policy
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

        # 8. Summary Table
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
