import csv
import io
from datetime import date
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.catalog.models import Artist, Venue, Song
from apps.concerts.models import Concert, ConcertArtist, ConcertSong


class ConcertCSVExportTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='alex', password='password123')
        self.user.profile.is_public = True
        self.user.profile.save()

        # Create sample concert 1 with full setlist
        self.venue1 = Venue.objects.create(name='9:30 Club', city='Washington', state='DC')
        self.artist1 = Artist.objects.create(name='Opeth', normalized_name='opeth')
        self.concert1 = Concert.objects.create(
            user=self.user,
            date=date(2024, 10, 20),
            raw_date='10/20/2024',
            year=2024,
            venue=self.venue1,
            primary_artist=self.artist1,
            raw_artists='Opeth'
        )
        self.ca1 = ConcertArtist.objects.create(concert=self.concert1, artist=self.artist1, billing_order=0, has_setlist=True)
        self.song1 = Song.objects.create(artist=self.artist1, title='The Moor', clean_title='the moor')
        self.song2 = Song.objects.create(artist=self.artist1, title='Windowpane', clean_title='windowpane')
        ConcertSong.objects.create(concert_artist=self.ca1, song=self.song1, raw_song_name='The Moor', track_num=1, set_name='Main Set')
        ConcertSong.objects.create(concert_artist=self.ca1, song=self.song2, raw_song_name='Windowpane', track_num=2, set_name='Main Set')

        # Create sample concert 2 with multiple artists and setlists
        self.venue2 = Venue.objects.create(name='The Anthem', city='Washington', state='DC')
        self.artist2 = Artist.objects.create(name='Porcupine Tree', normalized_name='porcupine tree')
        self.artist3 = Artist.objects.create(name='King Crimson', normalized_name='king crimson')
        self.concert2 = Concert.objects.create(
            user=self.user,
            date=date(2022, 9, 10),
            raw_date='09/10/2022',
            year=2022,
            venue=self.venue2,
            primary_artist=self.artist2,
            raw_artists='Porcupine Tree, King Crimson'
        )
        self.ca2 = ConcertArtist.objects.create(concert=self.concert2, artist=self.artist2, billing_order=0, has_setlist=True)
        self.ca3 = ConcertArtist.objects.create(concert=self.concert2, artist=self.artist3, billing_order=1, has_setlist=True)
        self.song3 = Song.objects.create(artist=self.artist2, title='Blackest Eyes', clean_title='blackest eyes')
        self.song4 = Song.objects.create(artist=self.artist3, title='Starless', clean_title='starless')
        ConcertSong.objects.create(concert_artist=self.ca2, song=self.song3, raw_song_name='Blackest Eyes', track_num=1, set_name='Main Set')
        ConcertSong.objects.create(concert_artist=self.ca3, song=self.song4, raw_song_name='Starless', track_num=1, set_name='Main Set')

    def test_authenticated_export(self):
        self.client.force_login(self.user)
        res = self.client.get('/api/concerts/export/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment; filename="alex_concerts.csv"', res['Content-Disposition'])

        content = res.content.decode('utf-8')
        reader = list(csv.reader(io.StringIO(content)))

        # Header validation
        self.assertEqual(reader[0], ['Date', 'Artist(s)', 'Venue', 'Setlist'])

        # Data rows validation
        self.assertEqual(len(reader), 3)  # 1 header + 2 concerts

        # Oldest concert on top (Porcupine Tree - 2022)
        row1 = reader[1]
        self.assertEqual(row1[1], 'Porcupine Tree, King Crimson')
        self.assertEqual(row1[2], 'The Anthem')
        self.assertIn('Porcupine Tree: Blackest Eyes', row1[3])
        self.assertIn('King Crimson: Starless', row1[3])

        # Newest concert on bottom (Opeth - 2024)
        row2 = reader[2]
        self.assertEqual(row2[1], 'Opeth')
        self.assertEqual(row2[2], '9:30 Club')
        self.assertEqual(row2[3], 'The Moor, Windowpane')

    def test_unauthenticated_export_redirects_to_login(self):
        res = self.client.get('/api/concerts/export/')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/accounts/login/', res.get('Location'))

    def test_public_profile_export(self):
        # Anonymous user can export public profile
        res = self.client.get('/u/alex/export/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res['Content-Type'], 'text/csv; charset=utf-8')

        content = res.content.decode('utf-8')
        reader = list(csv.reader(io.StringIO(content)))
        self.assertEqual(reader[0], ['Date', 'Artist(s)', 'Venue', 'Setlist'])
        self.assertEqual(len(reader), 3)

    def test_private_profile_export_forbidden_for_anonymous(self):
        self.user.profile.is_public = False
        self.user.profile.save()

        res = self.client.get('/u/alex/export/')
        self.assertEqual(res.status_code, 400)

        # But owner can export even when profile is private
        self.client.force_login(self.user)
        res_owner = self.client.get('/u/alex/export/')
        self.assertEqual(res_owner.status_code, 200)

    def test_concerts_page_contains_export_button(self):
        self.client.force_login(self.user)
        res = self.client.get('/concerts/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('Export CSV', content)
        self.assertIn('/api/concerts/export/', content)
