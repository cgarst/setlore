import json
from pathlib import Path
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.management import call_command
from apps.core.models import UserProfile
from apps.catalog.models import Artist, Venue, Song, Album, MusicianTenure
from apps.concerts.models import Concert, ConcertArtist, ConcertSong
from apps.concerts.services.demo_cache import export_user_data_to_cache, populate_user_data_from_cache


class DemoCachePopulationTests(TestCase):
    def setUp(self):
        # Create a source user with complete concert, venue, and song data
        self.source_user = User.objects.create_user(
            username="demomaster",
            email="demo@example.com",
            password="password123"
        )
        self.source_user.profile.theme = "Dark Side"
        self.source_user.profile.setlistfm_username = "progmaster"
        self.source_user.profile.is_public = True
        self.source_user.profile.save()

        self.venue = Venue.objects.create(
            name="Merriweather Post Pavilion",
            city="Columbia",
            state="MD",
            country="United States",
            latitude=39.209,
            longitude=-76.862,
            geocode_source="canonical_lookup"
        )

        self.artist = Artist.objects.create(
            name="Rush",
            normalized_name="rush",
            id="53b106e7-0cc2-4270-ac5f-7baaa6dba4e2"
        )

        MusicianTenure.objects.create(
            artist=self.artist,
            musician_name="Geddy Lee",
            role="Lead Vocals, Bass, Keyboards",
            instrument="Bass",
            start_year=1968,
            end_year=2018
        )

        self.album = Album.objects.create(
            artist=self.artist,
            title="Moving Pictures",
            clean_title="moving pictures",
            release_year=1981,
            album_type="album"
        )

        self.song1 = Song.objects.create(
            artist=self.artist,
            album=self.album,
            title="Tom Sawyer",
            clean_title="tom sawyer",
            release_year=1981
        )
        self.song2 = Song.objects.create(
            artist=self.artist,
            album=self.album,
            title="YYZ",
            clean_title="yyz",
            release_year=1981
        )

        self.concert = Concert.objects.create(
            user=self.source_user,
            date="2015-06-25",
            raw_date="06/25/2015",
            year=2015,
            venue=self.venue,
            primary_artist=self.artist,
            raw_artists="Rush",
            source="setlistfm",
            is_favorite=True
        )

        self.ca = ConcertArtist.objects.create(
            concert=self.concert,
            artist=self.artist,
            billing_order=0,
            setlistfm_id="rush_r40_sl",
            setlist_url="https://setlist.fm/rush123",
            has_setlist=True,
            is_favorite=True
        )

        ConcertSong.objects.create(
            concert_artist=self.ca,
            song=self.song1,
            raw_song_name="Tom Sawyer",
            set_name="Set 1",
            track_num=1,
            total_tracks=2,
            slot="Opener"
        )
        ConcertSong.objects.create(
            concert_artist=self.ca,
            song=self.song2,
            raw_song_name="YYZ",
            set_name="Set 1",
            track_num=2,
            total_tracks=2,
            slot="Show Closer"
        )

    def test_export_user_data_to_cache_structure(self):
        """Verify export generates a complete, portable dictionary structure."""
        cache_data = export_user_data_to_cache(username="demomaster")

        self.assertEqual(cache_data["source_username"], "demomaster")
        self.assertEqual(cache_data["total_concerts"], 1)
        self.assertEqual(cache_data["user"]["theme"], "Dark Side")
        self.assertEqual(cache_data["user"]["setlistfm_username"], "progmaster")

        # Verify catalog structure
        catalog = cache_data["catalog"]
        self.assertTrue(any(v["name"] == "Merriweather Post Pavilion" for v in catalog["venues"]))
        self.assertTrue(any(a["name"] == "Rush" for a in catalog["artists"]))
        self.assertTrue(any(mt["musician_name"] == "Geddy Lee" for mt in catalog["musician_tenures"]))
        self.assertTrue(any(alb["title"] == "Moving Pictures" for alb in catalog["albums"]))

        # Verify concert structure
        concerts = cache_data["concerts"]
        self.assertEqual(len(concerts), 1)
        c = concerts[0]
        self.assertEqual(c["primary_artist"], "Rush")
        self.assertEqual(c["venue_name"], "Merriweather Post Pavilion")
        self.assertEqual(len(c["artists"]), 1)
        self.assertEqual(len(c["artists"][0]["songs"]), 2)

    def test_populate_into_new_user_without_id_collision(self):
        """Ensure cache can be seeded into a completely new user without primary key conflicts."""
        cache_data = export_user_data_to_cache(username="demomaster")

        # Create other users to advance sequence
        for i in range(5):
            u_dummy = User.objects.create_user(username=f"dummy_{i}", password="pw")
            dummy_artist, _ = Artist.get_or_create_artist(f"Band {i}")
            Concert.objects.create(
                user=u_dummy,
                raw_date="01/01/2020",
                primary_artist=dummy_artist
            )

        res = populate_user_data_from_cache(cache_input=cache_data, target_username="seeded_target_user")
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["concerts_created"], 1)

        seeded_user = User.objects.get(username="seeded_target_user")
        self.assertEqual(seeded_user.profile.theme, "Dark Side")
        self.assertEqual(seeded_user.profile.setlistfm_username, "progmaster")

        user_concerts = Concert.objects.filter(user=seeded_user)
        self.assertEqual(user_concerts.count(), 1)
        c = user_concerts.first()
        self.assertEqual(c.primary_artist.name, "Rush")
        self.assertEqual(c.venue.name, "Merriweather Post Pavilion")

        ca = c.artists.first()
        self.assertEqual(ca.artist.name, "Rush")
        self.assertEqual(ca.songs.count(), 2)

    def test_management_commands_dump_and_populate(self):
        """Test dump_demo_cache and populate_demo_user management commands."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_file = Path(tmpdir) / "test_demo_cache.json"

            # 1. Run dump command
            call_command("dump_demo_cache", user="demomaster", output=str(dump_file))
            self.assertTrue(dump_file.exists())

            # 2. Run populate command into a new user
            call_command("populate_demo_user", cache=str(dump_file), user="cli_seeded_user")
            seeded = User.objects.filter(username="cli_seeded_user").first()
            self.assertIsNotNone(seeded)
            self.assertEqual(seeded.concerts.count(), 1)
            self.assertEqual(seeded.profile.theme, "Dark Side")
