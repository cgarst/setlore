import os
import json
import django
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.core.models import UserProfile
from apps.catalog.models import Artist, Album, Song, Venue
from apps.concerts.models import Concert, ConcertArtist, ConcertSong

class DjangoAppTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.client = Client()

    def test_user_profile_signal(self):
        self.assertTrue(hasattr(self.user, 'profile'))
        self.assertEqual(self.user.profile.sync_status, 'idle')

    def test_catalog_models(self):
        artist = Artist.objects.create(name='Rush', normalized_name='rush')
        album = Album.objects.create(artist=artist, title='Moving Pictures', clean_title='moving pictures', release_year=1981)
        song = Song.objects.create(artist=artist, album=album, title='Tom Sawyer', clean_title='tom sawyer', release_year=1981)
        venue = Venue.objects.create(name='Madison Square Garden', city='New York', state='NY')

        self.assertEqual(str(artist), 'Rush')
        self.assertIn('Moving Pictures', str(album))
        self.assertEqual(str(song), 'Rush - Tom Sawyer')
        self.assertIn('Madison Square Garden', str(venue))

    def test_concert_models(self):
        venue = Venue.objects.create(name='The Anthem', city='Washington', state='DC')
        artist = Artist.objects.create(name='Porcupine Tree', normalized_name='porcupine tree')
        concert = Concert.objects.create(
            user=self.user,
            raw_date='09/10/2022',
            year=2022,
            venue=venue,
            raw_artists='Porcupine Tree'
        )
        ca = ConcertArtist.objects.create(concert=concert, artist=artist)

        self.assertEqual(Concert.objects.filter(user=self.user).count(), 1)
        self.assertEqual(concert.artists.first().artist.name, 'Porcupine Tree')

    def test_health_check_endpoint(self):
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_dashboard_login_required(self):
        # Unauthenticated request to /overview/ redirects to login
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/accounts/login/', res.get('Location'))

        # Authenticated request renders dashboard
        self.client.force_login(self.user)
        auth_res = self.client.get('/overview/')
        self.assertEqual(auth_res.status_code, 200)

    def test_dashboard_renders_concert_cards_and_artists(self):
        venue = Venue.objects.create(name='9:30 Club', city='Washington', state='DC')
        artist = Artist.objects.create(name='Dream Theater', normalized_name='dream theater')
        concert = Concert.objects.create(
            user=self.user,
            raw_date='06/15/2019',
            year=2019,
            venue=venue,
            raw_artists='Dream Theater'
        )
        ConcertArtist.objects.create(concert=concert, artist=artist)

        self.client.force_login(self.user)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('concert-card', content)
        self.assertIn('artist-btn', content)
        self.assertIn('Dream Theater', content)
        self.assertIn('9:30 Club', content)

    def test_admin_login_page_renders(self):
        # Admin login page should render HTTP 200 without staticfile manifest errors
        res = self.client.get('/admin/login/?next=/admin/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Log in', res.content.decode('utf-8'))

    def test_admin_portal_accessible_by_staff(self):
        admin_user = User.objects.create_superuser(username='admin', password='adminpassword', email='admin@example.com')
        self.client.force_login(admin_user)
        res = self.client.get('/admin/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Setlore Administration', res.content.decode('utf-8'))

    def test_dashboard_renders_user_profile_dropdown(self):
        self.client.force_login(self.user)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('user-menu-btn', content)
        self.assertIn('user-dropdown-menu', content)
        self.assertIn('Change Password', content)
        self.assertIn('Log Out', content)

    def test_password_change_requires_login(self):
        res = self.client.get('/accounts/password_change/')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/accounts/login/', res.get('Location'))

    def test_password_change_flow(self):
        self.client.force_login(self.user)
        # Form page renders
        res = self.client.get('/accounts/password_change/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('Change Password', res.content.decode('utf-8'))

        # Post new password
        post_res = self.client.post('/accounts/password_change/', {
            'old_password': 'password123',
            'new_password1': 'BrandNewPass987!',
            'new_password2': 'BrandNewPass987!'
        })
        self.assertEqual(post_res.status_code, 302)
        self.assertEqual(post_res.get('Location'), '/accounts/password_change/done/')

        # Password change done page renders
        done_res = self.client.get('/accounts/password_change/done/')
        self.assertEqual(done_res.status_code, 200)
        self.assertIn('Password Changed!', done_res.content.decode('utf-8'))

        # Verify new password is set
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass987!'))

    def test_dashboard_tab_urls(self):
        self.client.force_login(self.user)
        tabs = [
            ('/overview/', 'overview'),
            ('/concerts/', 'concerts'),
            ('/songs/', 'songs'),
            ('/drilldown/', 'songs'),
            ('/artists/', 'songs'),
            ('/musicians/', 'musicians'),
            ('/map/', 'map'),
            ('/venues/', 'map'),
            ('/advanced/', 'albums'),
            ('/albums/', 'albums'),
            ('/freshness/', 'freshness'),
            ('/setlists/', 'freshness'),
            ('/gap/', 'gap'),
            ('/audit/', 'gap'),
        ]
        for url, expected_tab in tabs:
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, f"Expected 200 for {url}")
            self.assertEqual(res.context['initial_tab'], expected_tab, f"Expected {expected_tab} tab for {url}")

    def test_overview_charts_zoom_disabled(self):
        self.client.force_login(self.user)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        import json
        timeline_chart = json.loads(res.context['plotly_timeline'])
        top_artists_chart = json.loads(res.context['plotly_top_artists'])
        self.assertFalse(timeline_chart['layout']['dragmode'])
        self.assertTrue(timeline_chart['layout']['xaxis']['fixedrange'])
        self.assertTrue(timeline_chart['layout']['yaxis']['fixedrange'])
        self.assertTrue(timeline_chart['layout']['yaxis2']['fixedrange'])
        self.assertFalse(top_artists_chart['layout']['dragmode'])
        self.assertTrue(top_artists_chart['layout']['xaxis']['fixedrange'])
        self.assertTrue(top_artists_chart['layout']['yaxis']['fixedrange'])

    def test_update_profile_endpoint(self):
        import json
        self.client.force_login(self.user)
        # 1. Update setlistfm username
        res = self.client.post('/api/profile/update/', data=json.dumps({
            'setlistfm_username': '@rushfan42',
            'prompt_setlistfm': False
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['profile']['setlistfm_username'], 'rushfan42')
        self.assertFalse(data['profile']['prompt_setlistfm'])

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.setlistfm_username, 'rushfan42')
        self.assertFalse(self.user.profile.prompt_setlistfm)

    def test_dashboard_renders_profile_settings_modal(self):
        self.client.force_login(self.user)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('profile-modal', content)
        self.assertIn('setlist-prompt-modal', content)
        self.assertIn('Profile & Privacy Settings', content)
        self.assertIn("Don't ask me again", content)

    def test_user_profile_defaults_to_public(self):
        self.assertTrue(self.user.profile.is_public)

    def test_update_profile_privacy_setting(self):
        import json
        self.client.force_login(self.user)
        # Make profile private
        res = self.client.post('/api/profile/update/', data=json.dumps({
            'is_public': False
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data['profile']['is_public'])
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_public)

        # Make profile public again
        res2 = self.client.post('/api/profile/update/', data=json.dumps({
            'is_public': True
        }), content_type='application/json')
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json()['profile']['is_public'])
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_public)

    def test_public_profile_anonymous_access_when_public(self):
        venue = Venue.objects.create(name='The Fillmore', city='Silver Spring', state='MD')
        artist = Artist.objects.create(name='Haken', normalized_name='haken')
        concert = Concert.objects.create(
            user=self.user,
            raw_date='05/12/2023',
            year=2023,
            venue=venue,
            raw_artists='Haken'
        )
        ConcertArtist.objects.create(concert=concert, artist=artist)

        # Anonymous request to /u/<username>/
        res = self.client.get(f'/u/{self.user.username}/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('Public View (Read-Only)', content)
        self.assertIn('Haken', content)
        self.assertIn('Sign In', content)
        self.assertIn('Create Account', content)
        # Add concert buttons should not be present for anonymous viewers
        self.assertNotIn('id="mobile-tab-add-btn"', content)
        self.assertNotIn('<i class="fa-solid fa-plus text-xs"></i> Add Concert', content)

    def test_public_profile_anonymous_access_when_private(self):
        self.user.profile.is_public = False
        self.user.profile.save()

        res = self.client.get(f'/u/{self.user.username}/')
        self.assertEqual(res.status_code, 403)
        content = res.content.decode('utf-8')
        self.assertIn('This Profile is Private', content)
        self.assertIn('Log In', content)
        self.assertIn('Create Account', content)

    def test_public_profile_logged_in_other_user_access(self):
        other_user = User.objects.create_user(username='otheruser', password='password123')
        self.client.force_login(other_user)

        # 1. When target profile is public
        res = self.client.get(f'/u/{self.user.username}/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('Public View (Read-Only)', content)
        self.assertIn('Overview', content)
        self.assertIn('otheruser', content)

        # 2. When target profile is private
        self.user.profile.is_public = False
        self.user.profile.save()

        res_priv = self.client.get(f'/u/{self.user.username}/')
        self.assertEqual(res_priv.status_code, 403)
        priv_content = res_priv.content.decode('utf-8')
        self.assertIn('This Profile is Private', priv_content)
        self.assertIn('Overview', priv_content)
        self.assertIn('otheruser', priv_content)

    def test_public_profile_owner_access(self):
        self.client.force_login(self.user)

        # Owner viewing own public profile preview
        res = self.client.get(f'/u/{self.user.username}/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('Your Public Profile Preview', content)
        self.assertIn('Back to Your Overview', content)

        # Owner viewing own profile even when set to private
        self.user.profile.is_public = False
        self.user.profile.save()

        res_priv = self.client.get(f'/u/{self.user.username}/')
        self.assertEqual(res_priv.status_code, 200)

    def test_public_profile_not_found(self):
        res = self.client.get('/u/nonexistent_user_999/')
        self.assertEqual(res.status_code, 404)
        content = res.content.decode('utf-8')
        self.assertIn('User Not Found', content)

    def test_public_profile_tab_urls(self):
        tabs = ['overview', 'concerts', 'songs', 'musicians', 'map', 'albums', 'freshness', 'gap']
        for t in tabs:
            res = self.client.get(f'/u/{self.user.username}/{t}/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.context['initial_tab'], t)
            self.assertTrue(res.context['is_public_view'])
        # Verify legacy aliases
        for alias, expected in [('advanced', 'albums'), ('drilldown', 'songs'), ('artists', 'songs'), ('setlists', 'freshness')]:
            res_alias = self.client.get(f'/u/{self.user.username}/{alias}/')
            self.assertEqual(res_alias.status_code, 200)
            self.assertEqual(res_alias.context['initial_tab'], expected)

    def test_public_profile_i_was_there_button_and_attendance_toggle(self):
        # Setup target user's concert
        venue = Venue.objects.create(name='Merriweather Post Pavilion', city='Columbia', state='MD')
        artist = Artist.objects.create(name='Rush', normalized_name='rush')
        song = Song.objects.create(artist=artist, title='Tom Sawyer', clean_title='tom sawyer')
        
        target_concert = Concert.objects.create(
            user=self.user,
            raw_date='07/04/2015',
            year=2015,
            venue=venue,
            raw_artists='Rush'
        )
        ca = ConcertArtist.objects.create(concert=target_concert, artist=artist, setlistfm_id='sl_rush_123', has_setlist=True)
        from apps.concerts.models import ConcertSong
        cs = ConcertSong.objects.create(
            concert_artist=ca,
            song=song,
            raw_song_name='Tom Sawyer',
            set_name='Main Set',
            track_num=1,
            total_tracks=1,
            slot='Opener',
            slot_category='opener'
        )

        viewer_user = User.objects.create_user(username='vieweruser', password='password123')
        self.client.force_login(viewer_user)

        # 1. View target user's concerts page on public profile
        res = self.client.get(f'/u/{self.user.username}/concerts/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('I Was There', content)
        self.assertIn(f'attend-btn-concert_{target_concert.id}', content)
        self.assertIn('data-attended="false"', content)

        # 2. Toggle attendance ON (Log concert on viewer's profile)
        import json
        attend_res = self.client.post('/api/concerts/toggle-attendance/', data=json.dumps({
            'concert_id': target_concert.id
        }), content_type='application/json')
        self.assertEqual(attend_res.status_code, 200)
        attend_data = attend_res.json()
        self.assertEqual(attend_data['status'], 'success')
        self.assertEqual(attend_data['action'], 'added')
        self.assertTrue(attend_data['is_attended'])

        # Verify concert was created for viewer
        viewer_concerts = Concert.objects.filter(user=viewer_user)
        self.assertEqual(viewer_concerts.count(), 1)
        logged_c = viewer_concerts.first()
        self.assertEqual(logged_c.primary_artist, artist)
        self.assertEqual(logged_c.raw_date, '07/04/2015')
        self.assertEqual(logged_c.artists.count(), 1)
        self.assertEqual(logged_c.artists.first().songs.count(), 1)
        self.assertEqual(logged_c.artists.first().songs.first().raw_song_name, 'Tom Sawyer')

        # 3. View target user's concerts page again - button should now be active
        res_after = self.client.get(f'/u/{self.user.username}/concerts/')
        self.assertEqual(res_after.status_code, 200)
        content_after = res_after.content.decode('utf-8')
        self.assertIn('data-attended="true"', content_after)
        self.assertIn('active-attended', content_after)

        # 4. Toggle attendance OFF (De-select / remove from viewer's profile)
        de_select_res = self.client.post('/api/concerts/toggle-attendance/', data=json.dumps({
            'concert_id': target_concert.id
        }), content_type='application/json')
        self.assertEqual(de_select_res.status_code, 200)
        de_select_data = de_select_res.json()
        self.assertEqual(de_select_data['status'], 'success')
        self.assertEqual(de_select_data['action'], 'removed')
        self.assertFalse(de_select_data['is_attended'])

        # Verify concert was deleted from viewer's profile
        self.assertEqual(Concert.objects.filter(user=viewer_user).count(), 0)

        # 5. Target profile is private -> 403 Forbidden
        self.user.profile.is_public = False
        self.user.profile.save()
        priv_toggle_res = self.client.post('/api/concerts/toggle-attendance/', data=json.dumps({
            'concert_id': target_concert.id
        }), content_type='application/json')
        self.assertEqual(priv_toggle_res.status_code, 403)

    def test_privacy_policy_view(self):
        # Anonymous user access
        res = self.client.get('/privacy/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('Privacy Policy', content)
        self.assertIn('Google API Services', content)
        self.assertIn('Setlore', content)
        self.assertIn('Sign In', content)

        # Authenticated user access
        self.client.force_login(self.user)
        auth_res = self.client.get('/privacy/')
        self.assertEqual(auth_res.status_code, 200)
        auth_content = auth_res.content.decode('utf-8')
        self.assertIn('Back to Overview', auth_content)

        # Redirect check for /privacy (without trailing slash)
        redirect_res = self.client.get('/privacy', follow=True)
        self.assertEqual(redirect_res.status_code, 200)
        self.assertIn('Privacy Policy', redirect_res.content.decode('utf-8'))

    def test_save_setlist_flow(self):
        # 1. Create a concert with no songs
        artist = Artist.objects.create(name='Phish', normalized_name='phish')
        concert = Concert.objects.create(
            user=self.user,
            raw_date='07/15/2023',
            year=2023,
            raw_artists='Phish'
        )
        ca = ConcertArtist.objects.create(concert=concert, artist=artist, has_setlist=False)

        # Unauthenticated request rejected
        unauth_res = self.client.post('/api/concerts/save-setlist/', data=json.dumps({
            'concert_id': concert.id,
            'artist': 'Phish',
            'setlist_text': 'Free\nGhost\nEncore\nCharacter Zero'
        }), content_type='application/json')
        self.assertEqual(unauth_res.status_code, 302)

        # Authenticated user saves setlist
        self.client.force_login(self.user)
        save_res = self.client.post('/api/concerts/save-setlist/', data=json.dumps({
            'concert_id': concert.id,
            'artist': 'Phish',
            'setlist_text': 'Free\nGhost\nEncore\nCharacter Zero (Cover - Jimi Hendrix)'
        }), content_type='application/json')
        self.assertEqual(save_res.status_code, 200)
        data = save_res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['songs_count'], 3)

        # Check ConcertArtist and ConcertSong in DB
        ca.refresh_from_db()
        self.assertTrue(ca.has_setlist)
        self.assertEqual(ca.songs.count(), 3)

        songs = list(ca.songs.all().order_by('track_num'))
        self.assertEqual(songs[0].raw_song_name, 'Free')
        self.assertEqual(songs[0].slot, 'Opener')
        self.assertEqual(songs[1].raw_song_name, 'Ghost')
        self.assertEqual(songs[2].raw_song_name, 'Character Zero')
        self.assertTrue(songs[2].is_encore)
        self.assertEqual(songs[2].slot, 'Show Closer')
        self.assertTrue(songs[2].is_cover)
        self.assertEqual(songs[2].original_artist, 'Jimi Hendrix')

        # Invalid concert ID
        bad_res = self.client.post('/api/concerts/save-setlist/', data=json.dumps({
            'concert_id': 999999,
            'artist': 'Phish',
            'setlist_text': 'Free'
        }), content_type='application/json')
        self.assertEqual(bad_res.status_code, 404)

        # Empty setlist text
        empty_res = self.client.post('/api/concerts/save-setlist/', data=json.dumps({
            'concert_id': concert.id,
            'artist': 'Phish',
            'setlist_text': ''
        }), content_type='application/json')
        self.assertEqual(empty_res.status_code, 400)

    def test_dynamic_artist_casing_deduplication(self):
        from src.analytics import ConcertAnalytics

        csv_records = [
            {"id": "c1", "primary_artist": "Tesseract", "artists": ["Tesseract"], "year": 2023, "venue": "Rams Head Live"},
            {"id": "c2", "primary_artist": "Tesseract", "artists": ["Tesseract", "Intervals"], "year": 2025, "venue": "The Fillmore"},
        ]
        matched_setlists = [
            {
                "csv": csv_records[0],
                "artist": "TesseracT",
                "setlist": {
                    "artist": {"name": "TesseracT"},
                    "sets": {
                        "set": [
                            {"song": [{"name": "Concealing Fate"}, {"name": "Nocturne"}]}
                        ]
                    }
                }
            }
        ]

        analytics = ConcertAnalytics(matched_setlists=matched_setlists, all_csv_records=csv_records)
        metrics = analytics.compute_all_metrics()

        # Should only have 1 TesseracT in top_artists
        tesseract_entries = [a for a in metrics["top_artists"] if "tesseract" in a["artist"].lower()]
        self.assertEqual(len(tesseract_entries), 1)
        self.assertEqual(tesseract_entries[0]["artist"], "TesseracT")
        self.assertEqual(tesseract_entries[0]["concert_count"], 2)
        self.assertEqual(tesseract_entries[0]["total_songs_heard"], 2)

        # Concerts drilldown should also use canonical name
        drilldown = analytics.compute_concert_drilldown()
        for c in drilldown:
            for art in c["artists"]:
                if "tesseract" in art["artist"].lower():
                    self.assertEqual(art["artist"], "TesseracT")

    def test_jolly_artist_drilldown_case_insensitivity(self):
        from src.analytics import ConcertAnalytics

        csv_records = [
            {"id": "c1", "primary_artist": "JOLLY", "artists": ["JOLLY"], "year": 2014, "venue": "Arlenes Grocery"},
            {"id": "c2", "primary_artist": "Jolly", "artists": ["Jolly"], "year": 2018, "venue": "Gramercy Theatre"},
        ]
        matched_setlists = [
            {
                "csv": csv_records[0],
                "artist": "JOLLY",
                "setlist": {
                    "artist": {"name": "Jolly"},
                    "sets": {
                        "set": [
                            {"song": [{"name": "Joy"}, {"name": "Firewell"}]}
                        ]
                    }
                }
            }
        ]

        analytics = ConcertAnalytics(matched_setlists=matched_setlists, all_csv_records=csv_records)
        metrics = analytics.compute_all_metrics()

        # In artist_drilldown, JOLLY and Jolly must be merged under a single key 'Jolly'
        jolly_keys = [k for k in metrics["artist_drilldown"].keys() if k.lower() == "jolly"]
        self.assertEqual(len(jolly_keys), 1)
        self.assertEqual(jolly_keys[0], "Jolly")
        jolly_data = metrics["artist_drilldown"]["Jolly"]
        self.assertEqual(jolly_data["artist"], "Jolly")
        self.assertEqual(jolly_data["concert_count"], 2)
        self.assertEqual(jolly_data["total_plays"], 2)
        self.assertEqual(jolly_data["unique_songs"], 2)

    def test_initial_tab_html_rendering(self):
        self.client.force_login(self.user)

        # 1. /concerts/ should render tab-concerts without hidden and tab-overview with hidden
        res_concerts = self.client.get('/concerts/')
        self.assertEqual(res_concerts.status_code, 200)
        content_concerts = res_concerts.content.decode('utf-8')
        self.assertIn('id="tab-concerts" class="space-y-6"', content_concerts)
        self.assertIn('id="tab-overview" class="hidden space-y-6"', content_concerts)

        # 2. /albums/ should render tab-albums without hidden and tab-overview with hidden
        res_albums = self.client.get('/albums/')
        self.assertEqual(res_albums.status_code, 200)
        content_albums = res_albums.content.decode('utf-8')
        self.assertIn('id="tab-albums" class="space-y-6 pb-24 md:pb-8"', content_albums)
        self.assertIn('id="tab-overview" class="hidden space-y-6"', content_albums)

        # 3. /songs/ should render tab-songs without hidden and tab-overview with hidden
        res_songs = self.client.get('/songs/')
        self.assertEqual(res_songs.status_code, 200)
        content_songs = res_songs.content.decode('utf-8')
        self.assertIn('id="tab-songs" class="space-y-6"', content_songs)
        self.assertIn('id="tab-overview" class="hidden space-y-6"', content_songs)

        # 4. /freshness/ should render tab-freshness without hidden and tab-overview with hidden
        res_freshness = self.client.get('/freshness/')
        self.assertEqual(res_freshness.status_code, 200)
        content_freshness = res_freshness.content.decode('utf-8')
        self.assertIn('id="tab-freshness" class="space-y-6"', content_freshness)
        self.assertIn('id="tab-overview" class="hidden space-y-6"', content_freshness)

        # 5. /overview/ (or /) should render tab-overview without hidden and tab-concerts with hidden
        res_overview = self.client.get('/overview/')
        self.assertEqual(res_overview.status_code, 200)
        content_overview = res_overview.content.decode('utf-8')
        self.assertIn('id="tab-overview" class="space-y-6"', content_overview)
        self.assertIn('id="tab-concerts" class="hidden space-y-6"', content_overview)

    def test_overview_mobile_chart_boxes_and_modal(self):
        self.client.force_login(self.user)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        # Overview modal
        self.assertIn('id="overview-chart-modal"', content)
        self.assertIn('id="overview-modal-chart-host"', content)
        # Mobile small box triggers
        self.assertIn("openOverviewChartModal('timeline')", content)
        self.assertIn("openOverviewChartModal('top-artists')", content)
        self.assertIn("openOverviewChartModal('era')", content)
        self.assertIn("openOverviewChartModal('song-age')", content)
        self.assertIn("openOverviewChartModal('songs-table')", content)
        self.assertIn("openOverviewChartModal('venues-table')", content)
        # Desktop slots
        self.assertIn('id="chart-timeline-desktop-slot"', content)
        self.assertIn('id="chart-top-artists-desktop-slot"', content)
        self.assertIn('id="chart-era-desktop-slot"', content)
        self.assertIn('id="chart-song-age-desktop-slot"', content)
        self.assertIn('id="table-top-songs-desktop-slot"', content)
        self.assertIn('id="table-top-venues-desktop-slot"', content)
        # Mobile upcoming shows trigger
        self.assertIn('onclick="openUpcomingTableModal()"', content)

    def test_on_this_day_banner_display(self):
        from datetime import date
        today = date.today()
        # Create a concert occurring on today's month and day, but 3 years ago
        past_year = today.year - 3
        past_date_str = f"{past_year}-{today.month:02d}-{today.day:02d}"
        
        venue, _ = Venue.objects.get_or_create(name='9:30 Club', defaults={'city': 'Washington', 'state': 'DC'})
        artist, _ = Artist.objects.get_or_create(normalized_name='between the buried and me', defaults={'name': 'Between the Buried and Me'})
        concert = Concert.objects.create(
            user=self.user,
            date=f"{past_year}-{today.month:02d}-{today.day:02d}",
            raw_date=past_date_str,
            year=past_year,
            venue=venue,
            raw_artists='Between the Buried and Me, Animals as Leaders'
        )
        artist2, _ = Artist.objects.get_or_create(normalized_name='animals as leaders', defaults={'name': 'Animals as Leaders'})
        ConcertArtist.objects.create(concert=concert, artist=artist, billing_order=0)
        ConcertArtist.objects.create(concert=concert, artist=artist2, billing_order=1)

        # Also create a concert registered for TODAY (same month and day, but current year)
        # to ensure pre-registered shows occurring today are excluded
        today_artist, _ = Artist.objects.get_or_create(normalized_name='today headliner', defaults={'name': 'Today Headliner'})
        today_concert = Concert.objects.create(
            user=self.user,
            date=today,
            raw_date=today.strftime("%Y-%m-%d"),
            year=today.year,
            venue=venue,
            raw_artists='Today Headliner'
        )
        ConcertArtist.objects.create(concert=today_concert, artist=today_artist, billing_order=0)

        self.client.force_login(self.user)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('on_this_day', res.context)
        # Must only contain the past concert, NOT today's concert
        self.assertEqual(len(res.context['on_this_day']), 1)
        self.assertEqual(res.context['on_this_day'][0]['years_ago'], 3)
        self.assertEqual(res.context['on_this_day'][0]['primary_artist'], 'Between the Buried and Me')
        self.assertIn('Animals as Leaders', res.context['on_this_day'][0]['full_bill'])

        content = res.content.decode('utf-8')
        self.assertIn('id="on-this-day-strip"', content)
        # Extract strip HTML specifically to check strip contents
        strip_html = content.split('id="on-this-day-strip"')[1].split('<!-- Charts Row')[0]
        self.assertIn('Between the Buried and Me, Animals as Leaders', strip_html)
        self.assertNotIn('Today Headliner', strip_html)
        self.assertIn('(3y)', strip_html)

    def test_preregistered_concert_card_rendering_and_not_going_action(self):
        from datetime import date, timedelta
        today = date.today()
        future_date = today + timedelta(days=60)
        
        venue, _ = Venue.objects.get_or_create(name='Future Arena', defaults={'city': 'Boston', 'state': 'MA'})
        artist, _ = Artist.objects.get_or_create(normalized_name='iron maiden', defaults={'name': 'Iron Maiden'})
        
        # Create a pre-registered future concert
        future_concert = Concert.objects.create(
            user=self.user,
            date=future_date,
            raw_date=future_date.strftime("%Y-%m-%d"),
            year=future_date.year,
            venue=venue,
            raw_artists='Iron Maiden'
        )
        ConcertArtist.objects.create(concert=future_concert, artist=artist, billing_order=0)

        self.client.force_login(self.user)
        res = self.client.get('/concerts/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        # Future concert card must have planned styling, no onclick openConcertModal, and Not Going button
        self.assertIn(f'id="concert-card-concert_{future_concert.id}"', content)
        self.assertIn('Planned', content)
        self.assertIn('Not Going', content)
        self.assertIn('cursor-default', content)

        # Test Not Going action via delete endpoint
        del_res = self.client.post('/api/concerts/delete/', json.dumps({
            'concert_id': future_concert.id
        }), content_type='application/json')
        self.assertEqual(del_res.status_code, 200)
        self.assertFalse(Concert.objects.filter(id=future_concert.id).exists())

    def test_edit_artist_rename_offline(self):
        from apps.catalog.services import rename_or_merge_artist
        from apps.catalog.models import MusicianTenure

        artist, _ = Artist.get_or_create_artist('Typoed Band')
        album = Album.objects.create(artist=artist, title='Typo Album', clean_title='typo album')
        song = Song.objects.create(artist=artist, album=album, title='Typo Song', clean_title='typo song')
        venue = Venue.objects.create(name='Test Club', city='New York', state='NY')
        concert = Concert.objects.create(user=self.user, raw_date='2022-01-01', year=2022, venue=venue, raw_artists='Typoed Band')
        ca = ConcertArtist.objects.create(concert=concert, artist=artist)
        cs = ConcertSong.objects.create(concert_artist=ca, song=song, raw_song_name='Typo Song')
        tenure = MusicianTenure.objects.create(artist=artist, musician_name='John Doe', start_year=2020)

        result = rename_or_merge_artist(artist.id, 'Corrected Band')
        self.assertTrue(result['success'])
        self.assertEqual(result['action'], 'renamed')
        self.assertEqual(result['artist_name'], 'Corrected Band')

        self.assertFalse(Artist.objects.filter(name='Typoed Band').exists())
        new_artist = Artist.objects.filter(name='Corrected Band').first()
        self.assertIsNotNone(new_artist)
        self.assertEqual(Album.objects.filter(artist=new_artist).count(), 1)
        self.assertEqual(Song.objects.filter(artist=new_artist).count(), 1)
        self.assertEqual(ConcertArtist.objects.filter(artist=new_artist).count(), 1)
        self.assertEqual(MusicianTenure.objects.filter(artist=new_artist).count(), 1)
        concert.refresh_from_db()
        self.assertIn('Corrected Band', concert.raw_artists)

    def test_edit_artist_merge_existing(self):
        from apps.catalog.services import rename_or_merge_artist
        from apps.catalog.models import MusicianTenure

        target_artist, _ = Artist.get_or_create_artist('Target Artist')
        target_album = Album.objects.create(artist=target_artist, title='Greatest Hits', clean_title='greatest hits')
        target_song = Song.objects.create(artist=target_artist, album=target_album, title='Hit Song', clean_title='hit song')

        typo_artist, _ = Artist.get_or_create_artist('Typo Artist')
        # Same album title and same song title under typo artist (should merge without constraint error)
        typo_album = Album.objects.create(artist=typo_artist, title='Greatest Hits', clean_title='greatest hits')
        typo_song = Song.objects.create(artist=typo_artist, album=typo_album, title='Hit Song', clean_title='hit song')
        unique_typo_song = Song.objects.create(artist=typo_artist, title='Deep Cut', clean_title='deep cut')

        venue = Venue.objects.create(name='Merge Hall', city='Austin', state='TX')
        concert = Concert.objects.create(user=self.user, raw_date='2023-05-10', year=2023, venue=venue, raw_artists='Target Artist, Typo Artist')
        ca_target = ConcertArtist.objects.create(concert=concert, artist=target_artist, billing_order=0)
        ca_typo = ConcertArtist.objects.create(concert=concert, artist=typo_artist, billing_order=1)

        cs_target = ConcertSong.objects.create(concert_artist=ca_target, song=target_song, raw_song_name='Hit Song')
        cs_typo = ConcertSong.objects.create(concert_artist=ca_typo, song=unique_typo_song, raw_song_name='Deep Cut')

        result = rename_or_merge_artist(typo_artist.id, 'Target Artist')
        self.assertTrue(result['success'])
        self.assertEqual(result['action'], 'merged')
        self.assertEqual(result['artist_name'], 'Target Artist')

        self.assertFalse(Artist.objects.filter(id=typo_artist.id).exists())
        self.assertEqual(ConcertArtist.objects.filter(concert=concert).count(), 1)
        self.assertEqual(ca_target.songs.count(), 2)
        self.assertEqual(Song.objects.filter(artist=target_artist).count(), 2)
        self.assertEqual(Album.objects.filter(artist=target_artist).count(), 1)

    def test_edit_artist_api_endpoint(self):
        artist, _ = Artist.get_or_create_artist('Api Edit Typo')

        # Unauthenticated request rejected
        res = self.client.post('/api/artists/edit/', json.dumps({
            'artist_name': 'Api Edit Typo',
            'new_name': 'Api Edit Corrected'
        }), content_type='application/json')
        self.assertEqual(res.status_code, 302)

        # Authenticated request succeeds
        self.client.force_login(self.user)
        res = self.client.post('/api/artists/edit/', json.dumps({
            'artist_name': 'Api Edit Typo',
            'new_name': 'Api Edit Corrected'
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['artist_name'], 'Api Edit Corrected')
        self.assertFalse(Artist.objects.filter(name='Api Edit Typo').exists())
        self.assertTrue(Artist.objects.filter(name='Api Edit Corrected').exists())

    def test_solo_tracks_not_linked_to_musicbrainz_nor_pending(self):
        from src.album_enricher import is_solo_or_intro_track, AlbumEnricher
        from apps.concerts.views import get_dashboard_context
        from unittest.mock import patch

        # 1. Verify regex helper on diverse solos, intros, and real tracks
        self.assertTrue(is_solo_or_intro_track("Bass Solo"))
        self.assertTrue(is_solo_or_intro_track("Guitar Solo"))
        self.assertTrue(is_solo_or_intro_track("Drum Solo"))
        self.assertTrue(is_solo_or_intro_track("Keyboard Solo"))
        self.assertTrue(is_solo_or_intro_track("Piano Solo"))
        self.assertTrue(is_solo_or_intro_track("Vocal Solo"))
        self.assertTrue(is_solo_or_intro_track("Saxophone Solo"))
        self.assertTrue(is_solo_or_intro_track("Drum Duet"))
        self.assertTrue(is_solo_or_intro_track("Intro Tape"))
        self.assertTrue(is_solo_or_intro_track("Intermission"))
        self.assertTrue(is_solo_or_intro_track("Tuning"))

        # Genuine songs should NOT be classified as solos
        self.assertFalse(is_solo_or_intro_track("Demon of the Fall"))
        self.assertFalse(is_solo_or_intro_track("Pull Me Under"))
        self.assertFalse(is_solo_or_intro_track("Tom Sawyer"))
        self.assertFalse(is_solo_or_intro_track("Freebird"))

        # 2. Verify AlbumEnricher does NOT query MusicBrainz for solos
        enricher = AlbumEnricher()
        with patch.object(enricher, '_query_musicbrainz_studio_album') as mock_query:
            info = enricher.get_track_info("Test Artist", "Bass Solo")
            mock_query.assert_not_called()
            self.assertEqual(info["album"], "Non-Album / Singles")
            self.assertTrue(info["resolved"])

        # 3. Verify solos are excluded from unresolved MusicBrainz songs list in dashboard
        venue = Venue.objects.create(name='Solo Hall', city='Chicago', state='IL')
        artist = Artist.objects.create(name='Rush', normalized_name='rush')
        concert = Concert.objects.create(user=self.user, raw_date='2020-01-01', year=2020, venue=venue, raw_artists='Rush')
        ca = ConcertArtist.objects.create(concert=concert, artist=artist)

        drum_solo_song = Song.objects.create(artist=artist, title='Drum Solo', clean_title='drum solo')
        real_unresolved_song = Song.objects.create(artist=artist, title='Obscure Rare B-Side', clean_title='obscure rare b-side')

        ConcertSong.objects.create(concert_artist=ca, song=drum_solo_song, raw_song_name='Drum Solo', track_num=1)
        ConcertSong.objects.create(concert_artist=ca, song=real_unresolved_song, raw_song_name='Obscure Rare B-Side', track_num=2)

        from django.test import RequestFactory
        req = RequestFactory().get('/overview/')
        req.user = self.user
        ctx = get_dashboard_context(req, self.user)
        gap = ctx.get('gap', {})
        unresolved_songs = gap.get('unresolved_mb_songs', [])
        unresolved_titles = [s['song'] for s in unresolved_songs]

        self.assertNotIn('Drum Solo', unresolved_titles)
        self.assertIn('Obscure Rare B-Side', unresolved_titles)






