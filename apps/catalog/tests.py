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
        self.test_dump_dir = Path('/tmp/test_mb_dump')
        self.test_dump_dir.mkdir(parents=True, exist_ok=True)
        self.manager.dump_dir = self.test_dump_dir
        self.manager.db_path = self.test_dump_dir / 'mb_dump.db'
        self.manager.latest_file = self.test_dump_dir / 'LATEST'
        self.manager.status_file = self.test_dump_dir / 'status.json'

    def tearDown(self):
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

