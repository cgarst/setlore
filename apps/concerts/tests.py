import io
import csv
import json
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

    def test_musician_tenure_lineup_and_drilldown_occurrences(self):
        from apps.catalog.models import MusicianTenure
        from src.analytics import ConcertAnalytics

        MusicianTenure.objects.create(
            musician_name='Mike Portnoy',
            artist=self.art1,
            role='Drums',
            instrument='Drums',
            start_year=1985,
            end_year=2010
        )
        MusicianTenure.objects.create(
            musician_name='Mike Mangini',
            artist=self.art1,
            role='Drums',
            instrument='Drums',
            start_year=2010,
            end_year=2023
        )

        csv_records = [
            {
                "id": "c1",
                "display_date": "2008-05-10",
                "raw_date": "05/10/2008",
                "year": 2008,
                "venue": "Hammerstein Ballroom",
                "primary_artist": "Dream Theater",
                "artists": ["Dream Theater"]
            },
            {
                "id": "c2",
                "display_date": "2017-10-15",
                "raw_date": "10/15/2017",
                "year": 2017,
                "venue": "Beacon Theatre",
                "primary_artist": "Dream Theater",
                "artists": ["Dream Theater"]
            }
        ]

        matched_setlists = [
            {
                "csv": csv_records[0],
                "artist": "Dream Theater",
                "setlist": {
                    "url": "https://www.setlist.fm/1",
                    "artist": {"name": "Dream Theater"},
                    "sets": {
                        "set": [
                            {"song": [{"name": "Panic Attack"}]}
                        ]
                    }
                }
            },
            {
                "csv": csv_records[1],
                "artist": "Dream Theater",
                "setlist": {
                    "url": "https://www.setlist.fm/2",
                    "artist": {"name": "Dream Theater"},
                    "sets": {
                        "set": [
                            {"song": [{"name": "The Gift of Music"}]}
                        ]
                    }
                }
            }
        ]

        analytics = ConcertAnalytics(matched_setlists, csv_records)
        metrics = analytics.compute_all_metrics()
        drilldown = analytics.compute_concert_drilldown()

        # Concert 1 (2008): Mike Portnoy should be in musicians, Mike Mangini should not
        c1 = next(c for c in drilldown if c["id"] == "c1")
        c1_musicians = [m["musician"] for m in c1["artists"][0]["musicians"]]
        self.assertIn("Mike Portnoy", c1_musicians)
        self.assertNotIn("Mike Mangini", c1_musicians)

        # Concert 2 (2017): Mike Mangini should be in musicians, Mike Portnoy should not
        c2 = next(c for c in drilldown if c["id"] == "c2")
        c2_musicians = [m["musician"] for m in c2["artists"][0]["musicians"]]
        self.assertIn("Mike Mangini", c2_musicians)
        self.assertNotIn("Mike Portnoy", c2_musicians)

        # In artist drilldown occurrences:
        # Panic Attack (played in 2008) should have Mike Portnoy in musicians and not Mike Mangini
        panic_occs = metrics["artist_drilldown"]["Dream Theater"]["songs"][0]["occurrences"]
        panic_occ_2008 = next(o for o in panic_occs if o["year"] == 2008)
        self.assertIn("Mike Portnoy", panic_occ_2008["musicians"])
        self.assertNotIn("Mike Mangini", panic_occ_2008["musicians"])

        # The Gift of Music (played in 2017) should have Mike Mangini in musicians and not Mike Portnoy
        gift_occs = next(s["occurrences"] for s in metrics["artist_drilldown"]["Dream Theater"]["songs"] if s["song"] == "The Gift of Music")
        gift_occ_2017 = next(o for o in gift_occs if o["year"] == 2017)
        self.assertIn("Mike Mangini", gift_occ_2017["musicians"])
        self.assertNotIn("Mike Portnoy", gift_occ_2017["musicians"])

    def test_toggle_concert_favorite(self):
        self.assertFalse(self.concert.is_favorite)
        response = self.client.post(
            '/api/concerts/toggle-favorite/',
            data='{"concert_id": %d}' % self.concert.id,
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['is_favorite'])
        self.concert.refresh_from_db()
        self.assertTrue(self.concert.is_favorite)

        # Toggle back off
        response = self.client.post(
            '/api/concerts/toggle-favorite/',
            data='{"concert_id": %d}' % self.concert.id,
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['is_favorite'])
        self.concert.refresh_from_db()
        self.assertFalse(self.concert.is_favorite)

    def test_toggle_concert_artist_favorite(self):
        self.assertFalse(self.ca.is_favorite)
        # Toggle on by ca_id
        response = self.client.post(
            '/api/concerts/toggle-artist-favorite/',
            data=json.dumps({"ca_id": self.ca.id}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['is_favorite'])
        self.assertTrue(data['has_favorite_artist'])
        self.ca.refresh_from_db()
        self.assertTrue(self.ca.is_favorite)

        # Toggle back off by concert_id and artist name
        response = self.client.post(
            '/api/concerts/toggle-artist-favorite/',
            data=json.dumps({"concert_id": self.concert.id, "artist": self.art1.name}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['is_favorite'])
        self.assertFalse(data['has_favorite_artist'])
        self.ca.refresh_from_db()
        self.assertFalse(self.ca.is_favorite)

    def test_cancel_sync(self):
        self.user.profile.sync_status = 'syncing'
        self.user.profile.sync_progress = 'Processing...'
        self.user.profile.save()

        response = self.client.post('/api/sync/cancel/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.sync_status, 'idle')
        self.assertEqual(self.user.profile.sync_progress, 'Sync cancelled')

    def test_public_profile_private_requires_mutual_friendship(self):
        from apps.core.models import Friendship
        other_user = User.objects.create_user(username='privateuser', password='password123')
        other_user.profile.is_public = False
        other_user.profile.save()

        # Anonymous client attempting to view private profile
        anon_client = Client()
        response = anon_client.get(f'/u/{other_user.username}/')
        self.assertEqual(response.status_code, 403)

        # Logged-in user who has not sent request
        response = self.client.get(f'/u/{other_user.username}/')
        self.assertEqual(response.status_code, 403)

        # Logged-in user sends friend request (pending) -> still 403 because private profile requires mutual acceptance
        Friendship.objects.create(user=self.user, friend=other_user)
        response = self.client.get(f'/u/{other_user.username}/')
        self.assertEqual(response.status_code, 403)

        # Private user confirms / friends back (mutual friendship) -> 200
        Friendship.objects.create(user=other_user, friend=self.user)
        response = self.client.get(f'/u/{other_user.username}/')
        self.assertEqual(response.status_code, 200)

    def test_friend_request_pending_sent_lists(self):
        from apps.core.models import Friendship
        private_user = User.objects.create_user(username='private_buddy', password='password123')
        private_user.profile.is_public = False
        private_user.profile.save()

        public_user = User.objects.create_user(username='public_buddy', password='password123')
        public_user.profile.is_public = True
        public_user.profile.save()

        # User friends both
        self.client.post('/api/friends/toggle/', data=json.dumps({"friend_id": private_user.id}), content_type='application/json')
        self.client.post('/api/friends/toggle/', data=json.dumps({"friend_id": public_user.id}), content_type='application/json')

        # Check dashboard friends tab
        res = self.client.get('/friends/')
        self.assertEqual(res.status_code, 200)
        pending_sent = res.context['pending_sent_list']
        mutual_friends = res.context['friends_list']

        self.assertEqual(len(mutual_friends), 0)
        self.assertEqual(len(pending_sent), 2)
        pending_usernames = [p['username'] for p in pending_sent]
        self.assertIn('private_buddy', pending_usernames)
        self.assertIn('public_buddy', pending_usernames)

        # Public user is viewable even when request is pending
        res_pub = self.client.get(f'/u/{public_user.username}/')
        self.assertEqual(res_pub.status_code, 200)

        # Private user is NOT viewable while request is pending
        res_priv = self.client.get(f'/u/{private_user.username}/')
        self.assertEqual(res_priv.status_code, 403)

        # Private user logs in and accepts the request
        priv_client = Client()
        priv_client.force_login(private_user)

        # Check private user's friends tab -> has incoming request from self.user
        res_priv_tab = priv_client.get('/friends/')
        self.assertEqual(len(res_priv_tab.context['friended_by_list']), 1)
        self.assertEqual(res_priv_tab.context['friended_by_list'][0]['username'], self.user.username)

        # Accept the request -> automatically establishes mutual two-way friendship
        accept_res = priv_client.post('/api/friends/toggle/', data=json.dumps({"friend_id": self.user.id, "action": "accept"}), content_type='application/json')
        self.assertEqual(accept_res.status_code, 200)
        self.assertTrue(accept_res.json()['is_mutual'])

        # Now both users have each other in their friends_list
        res_user = self.client.get('/friends/')
        self.assertEqual(len(res_user.context['friends_list']), 1)
        self.assertEqual(res_user.context['friends_list'][0]['username'], private_user.username)

        res_priv_user = priv_client.get('/friends/')
        self.assertEqual(len(res_priv_user.context['friends_list']), 1)
        self.assertEqual(res_priv_user.context['friends_list'][0]['username'], self.user.username)

        # Private user's profile is now viewable by self.user
        res_priv_view = self.client.get(f'/u/{private_user.username}/')
        self.assertEqual(res_priv_view.status_code, 200)

    def test_musicbrainz_dump_online_fallback_toggle(self):
        # Staff user toggle
        self.user.is_staff = True
        self.user.save()

        response = self.client.post(
            '/api/admin/musicbrainz-dump/set-online-fallback/',
            data=json.dumps({"enabled": False}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['online_fallback'])

        response = self.client.post(
            '/api/admin/musicbrainz-dump/set-online-fallback/',
            data=json.dumps({"enabled": True}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['online_fallback'])


class UpcomingShowsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='progfan', password='password123')
        self.client = Client()
        self.client.login(username='progfan', password='password123')

        self.art_rush = Artist.objects.create(name='Rush', normalized_name='rush')
        self.art_yes = Artist.objects.create(name='Yes', normalized_name='yes')
        self.art_genesis = Artist.objects.create(name='Genesis', normalized_name='genesis')

        self.venue = Venue.objects.create(name='Madison Square Garden', city='New York', state='NY')

        # Create concert for Rush (2x) and Yes (1x)
        c1 = Concert.objects.create(
            user=self.user,
            date=date(2023, 6, 1),
            raw_date='06-01-2023',
            year=2023,
            venue=self.venue,
            primary_artist='Rush',
            raw_artists='Rush'
        )
        ConcertArtist.objects.create(concert=c1, artist=self.art_rush, billing_order=0)

        c2 = Concert.objects.create(
            user=self.user,
            date=date(2024, 8, 15),
            raw_date='08-15-2024',
            year=2024,
            venue=self.venue,
            primary_artist='Rush',
            raw_artists='Rush, Yes'
        )
        ConcertArtist.objects.create(concert=c2, artist=self.art_rush, billing_order=0)
        ConcertArtist.objects.create(concert=c2, artist=self.art_yes, billing_order=1)

    def test_user_seen_artists_summary(self):
        from src.upcoming_events import get_user_seen_artists_summary
        summary = get_user_seen_artists_summary(self.user)
        self.assertEqual(len(summary), 2)
        rush_item = next(a for a in summary if a['name'] == 'Rush')
        yes_item = next(a for a in summary if a['name'] == 'Yes')
        self.assertEqual(rush_item['count'], 2)
        self.assertEqual(yes_item['count'], 1)
        self.assertFalse(rush_item['is_hidden'])
        self.assertFalse(yes_item['is_hidden'])

    def test_toggle_hidden_artist_api(self):
        # Hide Rush
        res = self.client.post('/api/upcoming/toggle-hidden/', data=json.dumps({
            'artist': 'Rush',
            'is_hidden': True
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['is_hidden'])

        self.user.profile.refresh_from_db()
        self.assertIn('Rush', self.user.profile.hidden_upcoming_artists)

        # Toggle Rush back to visible
        res2 = self.client.post('/api/upcoming/toggle-hidden/', data=json.dumps({
            'artist': 'Rush'
        }), content_type='application/json')
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertFalse(data2['is_hidden'])

        self.user.profile.refresh_from_db()
        self.assertNotIn('Rush', self.user.profile.hidden_upcoming_artists)

    def test_save_upcoming_settings_bulk_api(self):
        res = self.client.post('/api/upcoming/settings/', data=json.dumps({
            'hidden_artists': ['Rush', 'Yes']
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['hidden_count'], 2)

        self.user.profile.refresh_from_db()
        self.assertEqual(sorted(self.user.profile.hidden_upcoming_artists), ['Rush', 'Yes'])

    def test_api_upcoming_shows_view(self):
        res = self.client.get('/api/upcoming/shows/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertIn('seen_artists', data)
        self.assertIn('upcoming_shows', data)
        self.assertEqual(data['total_artists'], 2)

    def test_profile_update_hidden_upcoming_artists(self):
        res = self.client.post('/api/profile/update/', data=json.dumps({
            'hidden_upcoming_artists': ['Rush']
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['profile']['hidden_upcoming_artists'], ['Rush'])

    def test_onboarding_step4_profile_update_location(self):
        res = self.client.post('/api/profile/update/', data=json.dumps({
            'upcoming_location': 'Washington, DC',
            'upcoming_radius_miles': 100
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['profile']['upcoming_location'], 'Washington, DC')
        self.assertEqual(data['profile']['upcoming_radius_miles'], 100)

    def test_overview_tab_contains_upcoming_shows_and_modal(self):
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('upcoming_shows', res.context)
        self.assertIn('upcoming_seen_artists', res.context)
        content = res.content.decode('utf-8')
        self.assertIn('id="upcoming-config-modal"', content)
        self.assertIn('Upcoming Shows', content)
        self.assertIn('id="upcoming-shows-grid"', content)
        self.assertIn('id="upcoming-overview-toolbar"', content)
        self.assertIn('id="upcoming-shows-setup-card"', content)

    def test_event_parser_and_relative_date(self):
        from src.upcoming_events import parse_event_item, format_relative_date
        from datetime import datetime, timezone, timedelta

        future_dt = datetime.now(timezone.utc) + timedelta(days=10)
        raw_event = {
            'id': '101',
            'datetime': future_dt.isoformat(),
            'venue': {
                'name': 'The Anthem',
                'city': 'Washington',
                'region': 'DC',
                'country': 'United States'
            },
            'url': 'https://bandsintown.com/e/101',
            'offers': [{'url': 'https://tickets.example.com/101'}],
            'lineup': ['Rush', 'Crown Lands']
        }
        parsed = parse_event_item(raw_event, 'Rush', times_seen=2)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed['artist'], 'Rush')
        self.assertEqual(parsed['venue'], 'The Anthem')
        self.assertEqual(parsed['ticket_url'], 'https://tickets.example.com/101')
        self.assertEqual(parsed['other_lineup'], ['Crown Lands'])
        self.assertIn('In 10 days', parsed['rel_badge'])

    def test_save_upcoming_settings_location_and_range(self):
        res = self.client.post('/api/upcoming/settings/', data=json.dumps({
            'location': 'Washington, DC',
            'radius_miles': 50,
            'hidden_artists': ['Yes']
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['location'], 'Washington, DC')
        self.assertEqual(data['radius_miles'], 50)
        self.assertEqual(data['hidden_artists'], ['Yes'])

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.upcoming_location, 'Washington, DC')
        self.assertEqual(self.user.profile.upcoming_radius_miles, 50)
        self.assertEqual(self.user.profile.hidden_upcoming_artists, ['Yes'])

    def test_haversine_distance_and_range_filtering(self):
        from src.upcoming_events import haversine_distance_miles, parse_event_item
        from datetime import datetime, timezone, timedelta

        # DC to Baltimore (~35 miles)
        dc_lat, dc_lon = 38.8951, -77.0364
        balt_lat, balt_lon = 39.2904, -76.6122
        dist = haversine_distance_miles(dc_lat, dc_lon, balt_lat, balt_lon)
        self.assertAlmostEqual(dist, 35.0, delta=10.0)

        future_dt = datetime.now(timezone.utc) + timedelta(days=5)
        event_near = {
            'id': 'near_1',
            'datetime': future_dt.isoformat(),
            'venue': {
                'name': 'CFG Bank Arena',
                'city': 'Baltimore',
                'region': 'MD',
                'country': 'United States',
                'latitude': balt_lat,
                'longitude': balt_lon,
            }
        }
        parsed_near = parse_event_item(event_near, 'Rush', times_seen=1, user_lat=dc_lat, user_lon=dc_lon)
        self.assertIsNotNone(parsed_near['distance_miles'])
        self.assertIn('mi away', parsed_near['distance_display'])

    def test_time_format_toggle_and_parsing(self):
        from src.upcoming_events import parse_event_item
        # Default 12h
        self.assertEqual(self.user.profile.time_format, '12')

        # Update profile to 24h
        res = self.client.post('/api/profile/update/', data=json.dumps({
            'time_format': '24'
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['profile']['time_format'], '24')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.time_format, '24')

        # Test parsing with 12h vs 24h
        sample_event = {
            'id': 'ev_time_1',
            'datetime': '2026-10-20T20:30:00Z',
            'venue': {'name': '9:30 Club', 'city': 'Washington', 'region': 'DC'}
        }
        parsed_12 = parse_event_item(sample_event, 'Rush', time_format='12')
        self.assertEqual(parsed_12['time_display'], '8:30 PM')
        self.assertEqual(parsed_12['time_12'], '8:30 PM')
        self.assertEqual(parsed_12['time_24'], '20:30')

        parsed_24 = parse_event_item(sample_event, 'Rush', time_format='24')
        self.assertEqual(parsed_24['time_display'], '20:30')

    def test_track_upcoming_show_api_and_metrics_exclusion(self):
        from apps.concerts.models import Concert, ConcertArtist
        from src.analytics import ConcertAnalytics
        from src.musician_tracker import analyze_musicians_live
        from src.venue_mapper import generate_venue_map_data
        from src.upcoming_events import get_upcoming_shows_for_user
        from apps.core.context_processors import core_context
        from unittest.mock import MagicMock
        from datetime import date, timedelta

        today = date.today()
        future_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")

        # Initial state: 2 past shows exist from setUp
        self.assertEqual(Concert.objects.filter(user=self.user).count(), 2)

        # 1. Track upcoming show via API
        res = self.client.post('/api/upcoming/track/', data=json.dumps({
            'artist': 'Rush',
            'date': future_date,
            'venue': 'Madison Square Garden',
            'city': 'New York',
            'state': 'NY',
            'country': 'United States',
            'lineup': ['Rush', 'Primus'],
            'event_url': 'https://example.com/event'
        }), content_type='application/json')

        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['is_tracked'])
        self.assertEqual(data['action'], 'tracked')
        self.assertEqual(Concert.objects.filter(user=self.user).count(), 3)

        # Check pre-added Concert in DB
        pre_added_concert = Concert.objects.get(id=data['concert_id'])
        self.assertEqual(pre_added_concert.primary_artist, 'Rush')
        self.assertEqual(pre_added_concert.source, 'setlistfm')
        self.assertFalse(pre_added_concert.is_custom_offline)
        self.assertEqual(pre_added_concert.date.strftime("%Y-%m-%d"), future_date)
        self.assertEqual(pre_added_concert.artists.count(), 2)

        # 2. Verify pre-added future show does NOT affect user_concerts_count in context processor
        fake_req = MagicMock()
        fake_req.user = self.user
        fake_req.session = {}
        ctx = core_context(fake_req)
        self.assertEqual(ctx['user_concerts_count'], 2)  # Only the 2 past shows count

        # 3. Verify analytics compute_all_metrics excludes future shows
        csv_records = [
            {
                "id": "c_past",
                "date": "2020-05-01",
                "date_obj": date(2020, 5, 1),
                "primary_artist": "Rush",
                "artists": ["Rush"],
                "venue": "Merriweather Post Pavilion",
                "city": "Columbia",
                "state": "MD",
                "country": "United States",
                "is_custom_offline": False,
            },
            {
                "id": "c_future",
                "date": future_date,
                "date_obj": today + timedelta(days=30),
                "primary_artist": "Rush",
                "artists": ["Rush", "Primus"],
                "venue": "Madison Square Garden",
                "city": "New York",
                "state": "NY",
                "country": "United States",
                "is_custom_offline": False,
            }
        ]
        matched_sl = [
            {
                "csv": csv_records[0],
                "artist": "Rush",
                "setlist": {
                    "url": "https://setlist.fm/1",
                    "artist": {"name": "Rush"},
                    "sets": {"set": [{"song": [{"name": "Tom Sawyer"}]}]}
                }
            }
        ]
        analytics = ConcertAnalytics(matched_sl, csv_records)
        metrics = analytics.compute_all_metrics()
        self.assertEqual(metrics["total_concerts"], 1)  # Future show excluded
        self.assertEqual(metrics["total_unique_artists"], 1)  # Primus not counted yet
        self.assertEqual(metrics["total_venues"], 1)  # MSG not counted yet

        # 4. Verify analyze_musicians_live excludes future shows
        musicians_data = analyze_musicians_live(csv_records)
        # Only past concert included
        for m in musicians_data.get("top_musicians", []):
            self.assertEqual(m["count"], 1)

        # 5. Verify generate_venue_map_data excludes future shows
        map_data = generate_venue_map_data(csv_records, matched_sl)
        self.assertEqual(map_data["total_venues"], 1)

        # 6. Untrack upcoming show via API toggle
        res_untrack = self.client.post('/api/upcoming/track/', data=json.dumps({
            'artist': 'Rush',
            'date': future_date,
            'venue': 'Madison Square Garden'
        }), content_type='application/json')
        self.assertEqual(res_untrack.status_code, 200)
        untrack_data = res_untrack.json()
        self.assertTrue(untrack_data['success'])
        self.assertFalse(untrack_data['is_tracked'])
        self.assertEqual(untrack_data['action'], 'untracked')
        self.assertEqual(Concert.objects.filter(user=self.user).count(), 2)








