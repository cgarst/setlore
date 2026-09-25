import os
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
        # Unauthenticated request redirects to login
        res = self.client.get('/')
        self.assertEqual(res.status_code, 302)
        self.assertIn('/accounts/login/', res.get('Location'))

        # Authenticated request renders dashboard
        self.client.force_login(self.user)
        auth_res = self.client.get('/')
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
        res = self.client.get('/')
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
        self.assertIn('Django administration', res.content.decode('utf-8'))

    def test_dashboard_renders_user_profile_dropdown(self):
        self.client.force_login(self.user)
        res = self.client.get('/')
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


