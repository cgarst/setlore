import sqlite3
import json
import shutil
from pathlib import Path
from django.test import TestCase, Client
from django.contrib.auth.models import User
from src.musicbrainz_dump import MusicBrainzDumpManager, _normalize_key
from src.album_enricher import AlbumEnricher

class MusicBrainzDumpTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='regularuser', password='password123')
        self.staff_user = User.objects.create_superuser(username='adminuser', password='password123', email='admin@example.com')
        self.client = Client()

        self.manager = MusicBrainzDumpManager.get_instance()
        self.manager._current_task_thread = None
        self.manager._cancel_requested = False
        self.test_dump_dir = Path('/tmp/test_mb_dump')
        self.test_dump_dir.mkdir(parents=True, exist_ok=True)
        self.manager.dump_dir = self.test_dump_dir
        self.manager.db_path = self.test_dump_dir / 'mb_dump.db'
        self.manager.latest_file = self.test_dump_dir / 'LATEST'
        self.manager.status_file = self.test_dump_dir / 'status.json'

    def tearDown(self):
        self.manager._cancel_requested = True
        self.manager._current_task_thread = None
        conn = getattr(self.manager._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self.manager._local.conn = None
            self.manager._local.db_path = None
        self.manager.get_latest_upstream_version = MusicBrainzDumpManager.get_latest_upstream_version.__get__(self.manager, MusicBrainzDumpManager)
        if self.test_dump_dir.exists():
            shutil.rmtree(self.test_dump_dir, ignore_errors=True)

    def test_status_empty_dump(self):
        status = self.manager.get_status()
        self.assertEqual(status['status'], 'not_downloaded')
        self.assertFalse(status['is_available'])
        self.assertEqual(status['record_count'], 0)

    def test_lookup_and_enricher_integration(self):
        # Create a mock SQLite dump database
        conn = sqlite3.connect(str(self.manager.db_path))
        self.manager._init_db(conn)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO recordings (
                artist_name, clean_artist, song_title, clean_title,
                album_title, release_year, primary_type, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'Dream Theater',
            _normalize_key('Dream Theater'),
            'Pull Me Under',
            _normalize_key('Pull Me Under'),
            'Images and Words',
            1992,
            'Recording',
            100
        ))
        conn.commit()
        conn.close()

        self.manager.latest_file.write_text('20260930-001001', encoding='utf-8')

        # Verify manager recognizes dump is available
        self.assertTrue(self.manager.is_dump_available())
        album, year = self.manager.lookup_studio_album('Dream Theater', 'Pull Me Under')
        self.assertEqual(album, 'Images and Words')
        self.assertEqual(year, 1992)

        # Test AlbumEnricher integration using disk dump
        enricher = AlbumEnricher()
        res = enricher.get_track_info('Dream Theater', 'Pull Me Under')
        self.assertEqual(res['album'], 'Images and Words')
        self.assertEqual(res['release_year'], 1992)

    def test_delete_dump(self):
        self.manager.latest_file.write_text('20260930-001001', encoding='utf-8')
        self.manager.db_path.touch()
        self.assertTrue(self.manager.latest_file.exists())

        self.manager.delete_dump()
        self.assertFalse(self.manager.is_dump_available())
        self.assertFalse(self.manager.db_path.exists())

    def test_admin_view_permissions(self):
        # Anonymous -> Redirect to login
        res = self.client.get('/admin/musicbrainz/')
        self.assertEqual(res.status_code, 302)

        # Regular user -> 302 redirect / denied
        self.client.login(username='regularuser', password='password123')
        res = self.client.get('/admin/musicbrainz/')
        self.assertEqual(res.status_code, 302)

        # Staff user -> 200 OK
        self.client.login(username='adminuser', password='password123')
        res = self.client.get('/admin/musicbrainz/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('MusicBrainz JSON Dump Manager', res.content.decode('utf-8'))

    def test_api_endpoints_staff_access(self):
        self.client.login(username='adminuser', password='password123')

        # Status
        res = self.client.get('/api/admin/musicbrainz-dump/status/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn('status', data)

        # Test Lookup
        res = self.client.post(
            '/api/admin/musicbrainz-dump/test-lookup/',
            data=json.dumps({'artist': 'Dream Theater', 'song': 'Pull Me Under'}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')

        # Delete
        res = self.client.post('/api/admin/musicbrainz-dump/delete/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')

    def test_mode_toggle_preserved_during_progress(self):
        # Initial status write (e.g. starting download)
        self.manager._update_status_file({
            'status': 'downloading',
            'progress': {'step': 'Downloading release.tar.xz...', 'percent': 10}
        })
        self.assertEqual(self.manager.get_mode(), 'auto')

        # User toggles mode to off while indexing/downloading
        self.manager.set_mode('off')
        self.assertEqual(self.manager.get_mode(), 'off')

        # Background worker writes an updated progress status
        self.manager._update_status_file({
            'status': 'extracting',
            'progress': {'step': 'Indexing releases...', 'percent': 50}
        })

        # Mode should still remain 'off', not flipped back to 'auto'
        self.assertEqual(self.manager.get_mode(), 'off')
        status = self.manager.get_status()
        self.assertEqual(status['mode'], 'off')

    def test_schedule_management_and_skipping(self):
        # Default schedule is off
        sched = self.manager.get_schedule()
        self.assertEqual(sched['interval'], 'off')
        self.assertFalse(sched['auto_delete_raw'])

        # Set weekly schedule with auto_delete_raw
        self.manager.set_schedule('weekly', auto_delete_raw=True)
        sched = self.manager.get_schedule()
        self.assertEqual(sched['interval'], 'weekly')
        self.assertTrue(sched['auto_delete_raw'])

        # Check API endpoint for setting schedule
        self.client.login(username='adminuser', password='password123')
        res = self.client.post(
            '/api/admin/musicbrainz-dump/set-schedule/',
            data=json.dumps({'interval': 'daily', 'auto_delete_raw': False}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['schedule'], 'daily')
        self.assertFalse(data['auto_delete_raw'])

        # Mock local dump matching upstream version
        conn = sqlite3.connect(str(self.manager.db_path))
        self.manager._init_db(conn)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO recordings (
                artist_name, clean_artist, song_title, clean_title,
                album_title, release_year, primary_type, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ('Artist', 'artist', 'Song', 'song', 'Album', 2020, 'Recording', 100))
        conn.commit()
        conn.close()

        self.manager.latest_file.write_text('20261001-000000', encoding='utf-8')
        self.manager.get_latest_upstream_version = lambda: '20261001-000000'

        # Check that check_and_run_scheduled_update skips when already current
        success, msg = self.manager.check_and_run_scheduled_update(force=False)
        self.assertFalse(success)
        self.assertIn('already current', msg)


class AlbumGetOrCreateTests(TestCase):
    def setUp(self):
        from apps.catalog.models import Artist, Album, Song
        self.artist = Artist.objects.create(name='Test Band', normalized_name='test band')

    def test_create_offline_album(self):
        from apps.catalog.models import Album
        album, created = Album.get_or_create_album(
            artist=self.artist,
            title='First Album',
            release_year=1990
        )
        self.assertTrue(created)
        self.assertTrue(album.id.startswith('offline:album:'))
        self.assertEqual(album.title, 'First Album')
        self.assertEqual(album.release_year, 1990)

    def test_upgrade_offline_album_to_mbid(self):
        from apps.catalog.models import Album, Song
        mbid = 'a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d'
        offline_alb, created = Album.get_or_create_album(
            artist=self.artist,
            title='Second Album',
            release_year=1995
        )
        self.assertTrue(created)
        self.assertTrue(offline_alb.id.startswith('offline:album:'))

        # Create a song linked to this offline album
        song = Song.objects.create(artist=self.artist, title='Track 1', clean_title='track 1', album=offline_alb)

        # Re-resolve with MBID
        upgraded_alb, upgraded_created = Album.get_or_create_album(
            artist=self.artist,
            title='Second Album',
            mbid=mbid,
            release_year=1995
        )
        self.assertFalse(upgraded_created)
        self.assertEqual(upgraded_alb.id, mbid)
        song.refresh_from_db()
        self.assertEqual(song.album_id, mbid)

    def test_merge_offline_album_into_existing_mbid_album(self):
        from apps.catalog.models import Album, Song
        mbid = 'b2c3d4e5-f6a7-8b9c-0d1e-2f3a4b5c6d7e'
        existing_mbid_alb = Album.objects.create(
            id=mbid,
            artist=self.artist,
            title='Third Album (Deluxe)',
            clean_title='third album (deluxe)',
            release_year=2000
        )

        offline_alb = Album.objects.create(
            artist=self.artist,
            title='Third Album',
            clean_title='third album',
            release_year=2000
        )
        song = Song.objects.create(artist=self.artist, title='Hit Song', clean_title='hit song', album=offline_alb)

        # Resolving 'Third Album' with the existing MBID should merge offline_alb into existing_mbid_alb
        resolved_alb, created = Album.get_or_create_album(
            artist=self.artist,
            title='Third Album',
            mbid=mbid
        )
        self.assertFalse(created)
        self.assertEqual(resolved_alb.id, mbid)
        self.assertFalse(Album.objects.filter(id=offline_alb.id).exists())
        song.refresh_from_db()
        self.assertEqual(song.album_id, mbid)


