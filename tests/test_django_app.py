import os
import json
import django
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.core.models import UserProfile
from apps.catalog.models import Artist, Album, Song, Venue
from apps.concerts.models import Concert, ConcertArtist

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
            primary_artist='Porcupine Tree',
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
            primary_artist='Dream Theater',
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
            ('/drilldown/', 'drilldown'),
            ('/artists/', 'drilldown'),
            ('/musicians/', 'musicians'),
            ('/map/', 'map'),
            ('/venues/', 'map'),
            ('/advanced/', 'advanced'),
            ('/albums/', 'advanced'),
            ('/setlists/', 'setlists'),
            ('/freshness/', 'setlists'),
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
            primary_artist='Haken',
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
        self.assertIn('Back to Overview', content)

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
        tabs = ['overview', 'concerts', 'drilldown', 'musicians', 'map', 'advanced', 'setlists', 'gap']
        for t in tabs:
            res = self.client.get(f'/u/{self.user.username}/{t}/')
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.context['initial_tab'], t)
            self.assertTrue(res.context['is_public_view'])

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
            primary_artist='Rush',
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
        self.assertEqual(logged_c.primary_artist, 'Rush')
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
            primary_artist='Phish',
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




