import io
import csv
from datetime import date
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.catalog.models import Artist, Venue, Song
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from apps.concerts.utils import parse_setlist_text
from src.csv_parser import parse_csv_rows

class AutocompleteAndCSVTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testmusicfan', password='password123')
        self.client = Client()
        self.client.login(username='testmusicfan', password='password123')

        # Create artists with casing variations or duplicates
        self.art1 = Artist.objects.create(name='Dream Theater', normalized_name='dream theater')
        self.art2 = Artist.objects.create(name='Dream theater', normalized_name='dream theater')
        self.art3 = Artist.objects.create(name='DREAM THEATER', normalized_name='dream theater')
        self.art4 = Artist.objects.create(name='Dream Syndicate', normalized_name='dream syndicate')

        # Create venue
        self.venue = Venue.objects.create(name='Radio City Music Hall', city='New York', state='NY')

        # Create concert for user
        self.concert = Concert.objects.create(
            user=self.user,
            date=date(2024, 5, 10),
            raw_date='05-10-2024',
            year=2024,
            venue=self.venue,
            raw_venue=self.venue.name,
            primary_artist='Dream Theater',
            raw_artists='Dream Theater',
            source='manual'
        )
        self.ca = ConcertArtist.objects.create(
            concert=self.concert,
            artist=self.art1,
            billing_order=0,
            has_setlist=True
        )

        # Create songs for setlist
        song1 = Song.objects.create(artist=self.art1, title='Pull Me Under', clean_title='pull me under')
        song2 = Song.objects.create(artist=self.art1, title='Metropolis Pt. 1', clean_title='metropolis pt. 1')
        song3 = Song.objects.create(artist=self.art1, title='The Spirit Carries On', clean_title='the spirit carries on')

        ConcertSong.objects.create(
            concert_artist=self.ca,
            song=song1,
            raw_song_name='Pull Me Under',
            set_name='Main Set',
            track_num=1,
            total_tracks=3,
            slot='Opener',
            slot_category='opener'
        )
        ConcertSong.objects.create(
            concert_artist=self.ca,
            song=song2,
            raw_song_name='Metropolis Pt. 1',
            set_name='Main Set',
            track_num=2,
            total_tracks=3,
            slot='Mid-Set',
            slot_category='mid'
        )
        ConcertSong.objects.create(
            concert_artist=self.ca,
            song=song3,
            raw_song_name='The Spirit Carries On',
            set_name='Encore',
            is_encore=True,
            encore_number=1,
            track_num=3,
            total_tracks=3,
            slot='Show Closer',
            slot_category='closer'
        )

    def test_autocomplete_deduplication(self):
        # When typing "dream", "Dream Theater" should appear exactly once
        res = self.client.get('/api/autocomplete/?type=artist&q=dream')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        results = data.get('results', [])
        names = [r['name'].lower() for r in results]
        self.assertEqual(names.count('dream theater'), 1)
        self.assertEqual(names.count('dream syndicate'), 1)

    def test_parse_setlist_text(self):
        # Comma separated with inline encore
        text = "Pull Me Under, Metropolis Pt. 1, Encore: The Spirit Carries On"
        tracks = parse_setlist_text(text)
        self.assertEqual(len(tracks), 3)
        self.assertEqual(tracks[0]['title'], 'Pull Me Under')
        self.assertFalse(tracks[0]['is_encore'])
        self.assertEqual(tracks[1]['title'], 'Metropolis Pt. 1')
        self.assertFalse(tracks[1]['is_encore'])
        self.assertEqual(tracks[2]['title'], 'The Spirit Carries On')
        self.assertTrue(tracks[2]['is_encore'])

    def test_csv_export_and_import_parity(self):
        # Export CSV
        res = self.client.get('/api/concerts/export/')
        self.assertEqual(res.status_code, 200)
        csv_content = res.content.decode('utf-8')
        
        # Check exported headers and content
        self.assertIn('Date,Artist(s),Venue,Setlist', csv_content)
        self.assertIn('Dream Theater', csv_content)
        self.assertIn('Pull Me Under', csv_content)
        self.assertIn('The Spirit Carries On', csv_content)

        # Re-import the exported CSV
        csv_file = io.BytesIO(csv_content.encode('utf-8'))
        csv_file.name = 'reimport.csv'
        import_res = self.client.post('/api/upload-csv/', {'csv_file': csv_file})
        self.assertEqual(import_res.status_code, 200)

        # Verify database state after reimport
        imported_concert = Concert.objects.filter(user=self.user, primary_artist='Dream Theater').first()
        self.assertIsNotNone(imported_concert)
        
        imported_ca = imported_concert.artists.first()
        self.assertIsNotNone(imported_ca)
        self.assertTrue(imported_ca.has_setlist)
        
        songs = list(imported_ca.songs.all().order_by('track_num'))
        self.assertEqual(len(songs), 3)
        self.assertEqual(songs[0].raw_song_name, 'Pull Me Under')
        self.assertFalse(songs[0].is_encore)
        self.assertEqual(songs[1].raw_song_name, 'Metropolis Pt. 1')
        self.assertFalse(songs[1].raw_song_name == '')
        self.assertEqual(songs[2].raw_song_name, 'The Spirit Carries On')
        self.assertTrue(songs[2].is_encore)
