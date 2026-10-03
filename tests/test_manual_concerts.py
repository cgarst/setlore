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
        self.assertEqual(concert.primary_artist.name, "Local Garage Band")
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
        dash_res = self.client.get('/overview/')
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
        dash_res = self.client.get('/overview/')
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
                primary_artist=a1,
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
            primary_artist=a2,
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
        a_local, _ = Artist.get_or_create_artist("My Local Friends Band")
        manual_concert = Concert.objects.create(
            user=self.user,
            raw_date="05/10/2021",
            year=2021,
            primary_artist=a_local,
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
        self.assertEqual(Concert.objects.filter(user=self.user, primary_artist__name="Iron Maiden").count(), 1)
        self.assertEqual(Concert.objects.filter(user=self.user).count(), 2)

    def test_edit_concert(self):
        v = Venue.objects.create(name="Original Venue", city="Baltimore", state="MD")
        a = Artist.objects.create(name="The Protomen", normalized_name="the protomen")
        c = Concert.objects.create(
            user=self.user,
            raw_date="10/10/2023",
            year=2023,
            venue=v,
            primary_artist=a,
            raw_artists="The Protomen",
            source="manual"
        )
        ca = ConcertArtist.objects.create(concert=c, artist=a, billing_order=0)

        payload = {
            "concert_id": c.id,
            "date": "2023-11-12",
            "primary_artist": "The Protomen Act II",
            "supporting_artists": "Bit Brigade, Mega Ran",
            "venue_name": "Ottobar",
            "city": "Baltimore",
            "state": "MD",
            "notes": "VIP Meet & Greet",
            "setlist_text": "1. Light Up the Night\n2. The Father of Death\nEncore:\n3. Due Vendetta"
        }

        res = self.client.post('/api/concerts/edit/', data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")

        c.refresh_from_db()
        self.assertEqual(c.primary_artist.name, "The Protomen Act II")
        self.assertEqual(c.venue.name, "Ottobar")
        self.assertEqual(c.notes, "VIP Meet & Greet")
        self.assertEqual(c.raw_date, "11/12/2023")

        # Check artists
        artists = list(c.artists.all().order_by('billing_order'))
        self.assertEqual(len(artists), 3)
        self.assertEqual(artists[0].artist.name, "The Protomen Act II")
        self.assertEqual(artists[1].artist.name, "Bit Brigade")
        self.assertEqual(artists[2].artist.name, "Mega Ran")

        # Check setlist songs
        songs = list(artists[0].songs.all().order_by('track_num'))
        self.assertEqual(len(songs), 3)
        self.assertEqual(songs[0].raw_song_name, "Light Up the Night")
        self.assertEqual(songs[1].raw_song_name, "The Father of Death")
        self.assertEqual(songs[2].raw_song_name, "Due Vendetta")
        self.assertTrue(songs[2].is_encore)

    def test_delete_concert(self):
        v = Venue.objects.create(name="Soundstage", city="Baltimore", state="MD")
        a = Artist.objects.create(name="Haken", normalized_name="haken")
        c = Concert.objects.create(
            user=self.user,
            raw_date="02/15/2024",
            year=2024,
            venue=v,
            primary_artist=a,
            raw_artists="Haken",
            source="manual"
        )
        ConcertArtist.objects.create(concert=c, artist=a)

        res = self.client.post('/api/concerts/delete/', data=json.dumps({"concert_id": c.id}), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Concert.objects.filter(id=c.id).exists())

    def test_convert_concert_to_local(self):
        v = Venue.objects.create(name="Merriweather", city="Columbia", state="MD")
        a = Artist.objects.create(name="Coheed and Cambria", normalized_name="coheed and cambria")
        c = Concert.objects.create(
            user=self.user,
            raw_date="08/01/2023",
            year=2023,
            venue=v,
            primary_artist=a,
            raw_artists="Coheed and Cambria",
            source="setlistfm",
            is_custom_offline=False
        )
        ca = ConcertArtist.objects.create(
            concert=c,
            artist=a,
            setlistfm_id="sl_12345",
            setlist_url="https://setlist.fm/test"
        )

        res = self.client.post('/api/concerts/convert-local/', data=json.dumps({"concert_id": c.id}), content_type='application/json')
        self.assertEqual(res.status_code, 200)

        c.refresh_from_db()
        ca.refresh_from_db()
        self.assertTrue(c.is_custom_offline)
        self.assertEqual(c.source, "manual")
        self.assertEqual(ca.setlistfm_id, "")
        self.assertEqual(ca.setlist_url, "")

    def test_coattendance_non_setlistfm_user_auto_detach(self):
        # User 1 is a setlistfm user who has a concert
        user1 = User.objects.create_user(username='setlistuser', password='password123')
        user1.profile.setlistfm_username = 'setlistpro'
        user1.profile.save()

        v = Venue.objects.create(name="The Anthem", city="Washington", state="DC")
        a = Artist.objects.create(name="Porcupine Tree", normalized_name="porcupine tree")
        c1 = Concert.objects.create(
            user=user1,
            raw_date="09/10/2022",
            year=2022,
            venue=v,
            primary_artist=a,
            raw_artists="Porcupine Tree",
            source="setlistfm",
            is_custom_offline=False
        )
        ConcertArtist.objects.create(
            concert=c1,
            artist=a,
            setlistfm_id="pt_777",
            setlist_url="https://setlist.fm/pt777"
        )

        # self.user is NOT a setlistfm user
        self.user.profile.setlistfm_username = ""
        self.user.profile.save()

        res = self.client.post(
            '/api/concerts/toggle-attendance/',
            data=json.dumps({"concert_id": c1.id}),
            content_type='application/json'
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["action"], "added")

        user_concert = Concert.objects.filter(user=self.user, primary_artist__name="Porcupine Tree").first()
        self.assertIsNotNone(user_concert)
        self.assertEqual(user_concert.source, "manual")
        self.assertTrue(user_concert.is_custom_offline)
        ca = user_concert.artists.first()
        self.assertEqual(ca.setlistfm_id, "")
        self.assertEqual(ca.setlist_url, "")

    def test_add_concert_with_setlistfm_user_syncs_single_concert(self):
        from unittest.mock import patch
        self.user.profile.setlistfm_username = "progfan99"
        self.user.profile.save()

        mock_setlist_payload = {
            "id": "mock_sl_123",
            "eventDate": "15-10-2023",
            "url": "https://www.setlist.fm/setlist/dream-theater/2023/anthem-mock_sl_123.html",
            "artist": {"name": "Dream Theater"},
            "venue": {
                "name": "The Anthem",
                "city": {"name": "Washington", "state": "DC", "country": {"name": "United States"}, "coords": {"lat": 38.88, "long": -77.02}}
            },
            "sets": {
                "set": [
                    {
                        "name": "Main Set",
                        "song": [
                            {"name": "The Alien"},
                            {"name": "Awaken the Master"}
                        ]
                    }
                ]
            }
        }

        with patch('src.setlist_api.SetlistFMClient.get_user_attended', return_value=[mock_setlist_payload]):
            payload = {
                "date": "2023-10-15",
                "primary_artist": "Dream Theater",
                "supporting_artists": "",
                "venue_name": "The Anthem",
                "city": "Washington",
                "state": "DC",
                "notes": "",
                "setlist_text": "",
                "is_custom_offline": False
            }
            res = self.client.post(
                '/api/concerts/add/',
                data=json.dumps(payload),
                content_type='application/json'
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "success")
            self.assertTrue(data.get("synced"))

            concert = Concert.objects.get(id=data["concert_id"])
            ca = concert.artists.first()
            self.assertEqual(ca.setlistfm_id, "mock_sl_123")
            self.assertTrue(ca.has_setlist)
            self.assertEqual(ca.songs.count(), 2)

    def test_sync_single_concert_api(self):
        from unittest.mock import patch
        self.user.profile.setlistfm_username = "progfan99"
        self.user.profile.save()

        v = Venue.objects.create(name="9:30 Club", city="Washington", state="DC")
        a = Artist.objects.create(name="Haken", normalized_name="haken")
        c = Concert.objects.create(
            user=self.user,
            raw_date="05/12/2022",
            date="2022-05-12",
            year=2022,
            venue=v,
            primary_artist=a,
            raw_artists="Haken",
            source="manual"
        )
        ca = ConcertArtist.objects.create(
            concert=c,
            artist=a,
            billing_order=0
        )

        mock_sl = {
            "id": "haken_sl_999",
            "eventDate": "12-05-2022",
            "url": "https://www.setlist.fm/setlist/haken/2022/930-club-haken_sl_999.html",
            "artist": {"name": "Haken"},
            "venue": {"name": "9:30 Club", "city": {"name": "Washington", "state": "DC", "coords": {"lat": 38.91, "long": -77.02}}},
            "sets": {"set": [{"song": [{"name": "Prosthetic"}, {"name": "Invasion"}]}]}
        }

        with patch('src.gap_analysis.find_global_setlist_match', return_value=mock_sl):
            res = self.client.post(
                '/api/concerts/sync-single/',
                data=json.dumps({"concert_id": c.id}),
                content_type='application/json'
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["matched"], 1)
            self.assertEqual(data["songs_added"], 2)

            ca.refresh_from_db()
            self.assertEqual(ca.setlistfm_id, "haken_sl_999")
            self.assertEqual(ca.songs.count(), 2)
