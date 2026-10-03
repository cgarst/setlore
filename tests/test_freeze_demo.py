import os
import shutil
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.catalog.models import Artist, Venue
from apps.concerts.models import Concert, ConcertArtist


class FreezeDemoCommandTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            username='demomusicfan',
            email='demofan@example.com',
            password='demopassword123'
        )
        self.user.profile.is_public = True
        self.user.profile.save()

        artist = Artist.objects.create(name='Rush', normalized_name='rush')
        venue = Venue.objects.create(name='Madison Square Garden', city='New York', state='NY')
        concert = Concert.objects.create(
            user=self.user,
            raw_date='06/15/2015',
            year=2015,
            venue=venue,
            primary_artist=artist,
            raw_artists='Rush'
        )
        ConcertArtist.objects.create(concert=concert, artist=artist, billing_order=0)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_freeze_demo_generates_expected_files(self):
        call_command(
            'freeze_demo',
            username='demomusicfan',
            output_dir=self.temp_dir,
            repo_url='https://github.com/cgarst/setlore',
            skip_screenshots=True,
            verbosity=0
        )

        out_path = Path(self.temp_dir)
        self.assertTrue((out_path / 'index.html').exists(), "index.html missing")
        self.assertTrue((out_path / 'demo' / 'index.html').exists(), "demo/index.html missing")
        self.assertTrue((out_path / 'demo.html').exists(), "demo.html missing")
        self.assertTrue((out_path / 'demo' / 'concerts.csv').exists(), "demo/concerts.csv missing")
        self.assertTrue((out_path / 'concerts.csv').exists(), "concerts.csv missing")
        self.assertTrue((out_path / 'privacy' / 'index.html').exists(), "privacy/index.html missing")
        self.assertTrue((out_path / 'privacy.html').exists(), "privacy.html missing")
        self.assertTrue((out_path / '.nojekyll').exists(), ".nojekyll missing")
        self.assertTrue((out_path / 'static').exists(), "static/ directory missing")

    def test_landing_page_demo_flavor_content(self):
        call_command(
            'freeze_demo',
            username='demomusicfan',
            output_dir=self.temp_dir,
            repo_url='https://github.com/cgarst/setlore',
            skip_screenshots=True,
            verbosity=0
        )

        landing_html = (Path(self.temp_dir) / 'index.html').read_text(encoding='utf-8')

        # Should have Demo and Install CTAs
        self.assertIn('Demo Setlore', landing_html)
        self.assertIn('Install via GitHub', landing_html)
        self.assertIn('https://github.com/cgarst/setlore', landing_html)

        # Should NOT link to login, register, open setlore, or have leaderboard
        self.assertNotIn('Sign In', landing_html)
        self.assertNotIn('Start Tracking Shows', landing_html)
        self.assertNotIn('Open Setlore', landing_html)
        self.assertNotIn("This Server's Community Leaderboard", landing_html)

        # Static assets must use relative paths
        self.assertNotIn('="/static/', landing_html)
        self.assertNotIn("='/static/", landing_html)
        self.assertIn('static/img/setlore_banner.png', landing_html)

    def test_demo_profile_content_and_relative_assets(self):
        call_command(
            'freeze_demo',
            username='demomusicfan',
            output_dir=self.temp_dir,
            skip_screenshots=True,
            verbosity=0
        )

        demo_sub_html = (Path(self.temp_dir) / 'demo' / 'index.html').read_text(encoding='utf-8')
        demo_root_html = (Path(self.temp_dir) / 'demo.html').read_text(encoding='utf-8')

        # Subfolder demo page checks
        self.assertIn("demomusicfan's Concert Collection", demo_sub_html)
        self.assertIn('Demo Mode (Read-Only)', demo_sub_html)
        self.assertIn('Rush', demo_sub_html)
        self.assertIn('Madison Square Garden', demo_sub_html)
        self.assertIn('href="concerts.csv"', demo_sub_html)
        self.assertIn('Install Setlore', demo_sub_html)
        self.assertNotIn('id="public-profile-friend-btn"', demo_sub_html)

        # Depth=1 relative static assets
        self.assertIn('../static/img/setlore_banner.png', demo_sub_html)
        self.assertNotIn('="/static/', demo_sub_html)

        # Root demo.html static assets
        self.assertIn('static/img/setlore_banner.png', demo_root_html)

    def test_csv_export_content(self):
        call_command(
            'freeze_demo',
            username='demomusicfan',
            output_dir=self.temp_dir,
            skip_screenshots=True,
            verbosity=0
        )

        csv_text = (Path(self.temp_dir) / 'demo' / 'concerts.csv').read_text(encoding='utf-8')
        self.assertIn('Date,Artist(s),Venue,Setlist', csv_text)
        self.assertIn('Rush', csv_text)
        self.assertIn('Madison Square Garden', csv_text)

    def test_nonexistent_user_raises_command_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command(
                'freeze_demo',
                username='nonexistent_user_9999',
                output_dir=self.temp_dir,
                skip_screenshots=True,
                verbosity=0
            )
        self.assertIn("User with username 'nonexistent_user_9999' was not found", str(ctx.exception))

    def test_freeze_alias_command(self):
        call_command(
            'freeze',
            username='demomusicfan',
            output_dir=self.temp_dir,
            skip_screenshots=True,
            verbosity=0
        )
        self.assertTrue((Path(self.temp_dir) / 'index.html').exists())
