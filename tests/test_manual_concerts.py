import json
from io import BytesIO
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.catalog.models import Artist, Venue, Song
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from apps.concerts.utils import parse_setlist_text

class ManualConcertTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='musicfan', password='password123')
        self.client = Client()
        self.client.force_login(self.user)

    def test_parse_setlist_text_utility(self):
        sample = """
        1. Intro Jam
        2. Midnight Rider (Acoustic)
        3. War Pigs (Black Sabbath cover)
        Set 2:
        4. Free Bird
        Encore:
        5. Whipping Post
        """
        tracks = parse_setlist_text(sample)
        self.assertEqual(len(tracks), 5)
        self.assertEqual(tracks[0]["title"], "Intro Jam")
        self.assertEqual(tracks[0]["set_name"], "Main Set")
        self.assertFalse(tracks[0]["is_encore"])

        self.assertEqual(tracks[1]["title"], "Midnight Rider")
        self.assertEqual(tracks[1]["info"], "Acoustic")

        self.assertEqual(tracks[2]["title"], "War Pigs")
        self.assertTrue(tracks[2]["is_cover"])
        self.assertEqual(tracks[2]["original_artist"], "Black Sabbath")

        self.assertEqual(tracks[3]["title"], "Free Bird")
        self.assertEqual(tracks[3]["set_name"], "Set 2")

        self.assertEqual(tracks[4]["title"], "Whipping Post")
        self.assertTrue(tracks[4]["is_encore"])
        self.assertEqual(tracks[4]["encore_number"], 1)

    def test_add_concert_without_setlist(self):
        payload = {
            "date": "2023-10-15",
            "primary_artist": "Local Garage Band",
            "supporting_artists": "The Openers, Acoustic Duo",
            "venue_name": "Joe's Basement",
            "city": "Frederick",
            "state": "MD",
            "notes": "Great intimate show",
            "setlist_text": "",
            "is_custom_offline": True
        }
        res = self.client.post(
            '/api/concerts/add/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")

        # Verify DB records
        concert = Concert.objects.get(id=data["concert_id"])
        self.assertEqual(concert.user, self.user)
        self.assertEqual(concert.primary_artist, "Local Garage Band")
        self.assertEqual(concert.source, "manual")
        self.assertTrue(concert.is_custom_offline)
        self.assertEqual(concert.venue.name, "Joe's Basement")
        self.assertEqual(concert.venue.city, "Frederick")
        self.assertEqual(concert.venue.state, "MD")

        # Check artists
        artists = list(concert.artists.all().order_by('billing_order'))
        self.assertEqual(len(artists), 3)
        self.assertEqual(artists[0].artist.name, "Local Garage Band")
        self.assertFalse(artists[0].has_setlist)
        self.assertEqual(artists[0].songs.count(), 0)
        self.assertEqual(artists[1].artist.name, "The Openers")
        self.assertEqual(artists[2].artist.name, "Acoustic Duo")

        # Verify dashboard renders with this concert
        dash_res = self.client.get('/dashboard/')
        self.assertEqual(dash_res.status_code, 200)
        content = dash_res.content.decode('utf-8')
        self.assertIn("Local Garage Band", content)
        self.assertIn("Joe's Basement", content)

    def test_add_concert_with_optional_setlist(self):
        setlist_str = """
        1. A Nightmare to Remember
        2. A Rite of Passage
        Encore:
        3. The Count of Tuscany (Cover - Someone)
        """
        payload = {
            "date": "2024-06-01",
            "primary_artist": "Dream Theater",
            "venue_name": "The Anthem",
            "city": "Washington",
            "state": "DC",
            "setlist_text": setlist_str,
            "is_custom_offline": True
        }
        res = self.client.post(
            '/api/concerts/add/',
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()

        concert = Concert.objects.get(id=data["concert_id"])
        ca = concert.artists.first()
        self.assertTrue(ca.has_setlist)
        self.assertEqual(ca.songs.count(), 3)

        songs = list(ca.songs.all().order_by('track_num'))
        self.assertEqual(songs[0].raw_song_name, "A Nightmare to Remember")
        self.assertEqual(songs[0].slot, "Opener")
        self.assertEqual(songs[1].raw_song_name, "A Rite of Passage")
        self.assertEqual(songs[2].raw_song_name, "The Count of Tuscany")
        self.assertTrue(songs[2].is_encore)
        self.assertTrue(songs[2].is_cover)
        self.assertEqual(songs[2].original_artist, "Someone")

        # Verify dashboard computes songs heard
        dash_res = self.client.get('/dashboard/')
        self.assertEqual(dash_res.status_code, 200)
        content = dash_res.content.decode('utf-8')
        self.assertIn("A Nightmare to Remember", content)
        self.assertIn("The Count of Tuscany", content)

    def test_add_concert_validation_errors(self):
        # Missing date
        res = self.client.post(
            '/api/concerts/add/',
            data=json.dumps({"primary_artist": "Rush", "venue_name": "DAR"}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Date is required", res.json()["error"])

        # Missing artist
        res = self.client.post(
            '/api/concerts/add/',
            data=json.dumps({"date": "2023-01-01", "venue_name": "DAR"}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Artist/Headliner is required", res.json()["error"])

        # Invalid date
        res = self.client.post(
            '/api/concerts/add/',
            data=json.dumps({"date": "not-a-date", "primary_artist": "Rush", "venue_name": "DAR"}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Invalid date format", res.json()["error"])

    def test_autocomplete_sorted_by_frequency(self):
        v1 = Venue.objects.create(name="9:30 Club", city="Washington", state="DC")
        v2 = Venue.objects.create(name="The Fillmore", city="Silver Spring", state="MD")

        a1 = Artist.objects.create(name="Rush", normalized_name="rush")
        a2 = Artist.objects.create(name="Dream Theater", normalized_name="dream theater")

        # Create 3 concerts at 9:30 Club for Rush
        for i in range(3):
            c = Concert.objects.create(
                user=self.user,
                raw_date=f"202{i}-05-01",
                year=2020 + i,
                venue=v1,
                primary_artist="Rush",
                raw_artists="Rush",
                source="manual"
            )
            ConcertArtist.objects.create(concert=c, artist=a1)

        # Create 1 concert at The Fillmore for Dream Theater
        c2 = Concert.objects.create(
            user=self.user,
            raw_date="2024-06-01",
            year=2024,
            venue=v2,
            primary_artist="Dream Theater",
            raw_artists="Dream Theater",
            source="manual"
        )
        ConcertArtist.objects.create(concert=c2, artist=a2)

        # Test Venue autocomplete sorting (9:30 Club with 3 shows should come before The Fillmore with 1 show)
        res = self.client.get('/api/autocomplete/?type=venue')
        self.assertEqual(res.status_code, 200)
        venues = res.json()["results"]
        self.assertGreaterEqual(len(venues), 2)
        self.assertEqual(venues[0]["name"], "9:30 Club")
        self.assertEqual(venues[0]["count"], 3)
        self.assertEqual(venues[1]["name"], "The Fillmore")
        self.assertEqual(venues[1]["count"], 1)

        # Test Artist autocomplete sorting (Rush with 3 shows should come before Dream Theater with 1 show)
        res_art = self.client.get('/api/autocomplete/?type=artist')
        self.assertEqual(res_art.status_code, 200)
        artists = res_art.json()["results"]
        self.assertGreaterEqual(len(artists), 2)
        self.assertEqual(artists[0]["name"], "Rush")
        self.assertEqual(artists[0]["count"], 3)
        self.assertEqual(artists[1]["name"], "Dream Theater")
        self.assertEqual(artists[1]["count"], 1)

    def test_csv_upload_preserves_manually_added_concerts(self):
        from unittest.mock import patch

        # 1. Add a manual concert
        manual_concert = Concert.objects.create(
            user=self.user,
            raw_date="05/10/2021",
            year=2021,
            primary_artist="My Local Friends Band",
            raw_artists="My Local Friends Band",
            raw_venue="Friend's Garage",
            source="manual",
            is_custom_offline=True
        )

        # 2. Upload a CSV with 1 concert
        csv_content = "Date,Bands,Venue,City,State\n08/20/2022,Iron Maiden,Capital One Arena,Washington,DC\n"
        csv_file = SimpleUploadedFile("concerts.csv", csv_content.encode("utf-8"), content_type="text/csv")
        with patch('apps.concerts.views.sync_worker.enqueue_sync') as mock_sync:
            upload_res = self.client.post('/api/upload-csv/', {'csv_file': csv_file})
            self.assertEqual(upload_res.status_code, 200)
            mock_sync.assert_called_once_with(self.user.id)

        # 3. Verify that the manual concert STILL exists and was NOT deleted!
        self.assertTrue(Concert.objects.filter(id=manual_concert.id).exists())
        # And the new CSV concert was added
        self.assertEqual(Concert.objects.filter(user=self.user, primary_artist="Iron Maiden").count(), 1)
        self.assertEqual(Concert.objects.filter(user=self.user).count(), 2)
