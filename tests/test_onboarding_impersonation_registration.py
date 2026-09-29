from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from apps.core.models import SiteSetting, Friendship
from apps.concerts.models import Concert
from apps.catalog.models import Venue

User = get_user_model()


class OnboardingAndImpersonationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            username='admin_boss',
            email='admin@example.com',
            password='AdminPassword123!'
        )
        self.regular_user = User.objects.create_user(
            username='concert_fan',
            email='fan@example.com',
            password='FanPassword123!'
        )
        self.second_user = User.objects.create_user(
            username='rocker_jane',
            email='jane@example.com',
            password='JanePassword123!'
        )

    def test_onboarding_flag_displays_for_zero_concerts_until_concert_logged(self):
        # Regular user logs in with 0 concerts
        self.client.login(username='concert_fan', password='FanPassword123!')
        res = self.client.get(reverse('dashboard'))
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.context['should_show_onboarding'])
        self.assertEqual(res.context['user_concerts_count'], 0)

        # Create 1 concert for this user
        venue = Venue.objects.create(name='9:30 Club', city='Washington', state='DC')
        Concert.objects.create(
            user=self.regular_user,
            date='2024-05-10',
            raw_date='10-05-2024',
            year=2024,
            venue=venue,
            primary_artist='Opeth',
            raw_artists='Opeth'
        )

        # Reload dashboard
        res_after = self.client.get(reverse('dashboard'))
        self.assertEqual(res_after.status_code, 200)
        self.assertFalse(res_after.context['should_show_onboarding'])
        self.assertEqual(res_after.context['user_concerts_count'], 1)

    def test_onboarding_wizard_hidden_on_public_profiles_and_landing(self):
        # Anonymous user visiting landing page
        res = self.client.get(reverse('home'))
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('onboarding-modal', res.content.decode('utf-8'))
        self.assertNotIn('openOnboardingModal()', res.content.decode('utf-8'))

        # Anonymous user visiting public profile
        res_pub_anon = self.client.get(f"/u/{self.regular_user.username}/")
        self.assertEqual(res_pub_anon.status_code, 200)
        self.assertNotIn('onboarding-modal', res_pub_anon.content.decode('utf-8'))
        self.assertNotIn('openOnboardingModal()', res_pub_anon.content.decode('utf-8'))

        # Authenticated user with 0 concerts visiting someone else's public profile
        self.client.login(username='concert_fan', password='FanPassword123!')
        res_pub_auth = self.client.get(f"/u/{self.second_user.username}/")
        self.assertEqual(res_pub_auth.status_code, 200)
        self.assertNotIn('onboarding-modal', res_pub_auth.content.decode('utf-8'))
        self.assertNotIn('openOnboardingModal()', res_pub_auth.content.decode('utf-8'))

        # Authenticated user with 0 concerts on their OWN dashboard MUST see the onboarding modal
        res_dash = self.client.get(reverse('dashboard'))
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn('onboarding-modal', res_dash.content.decode('utf-8'))
        self.assertIn('openOnboardingModal()', res_dash.content.decode('utf-8'))

    def test_registration_does_not_capture_setlistfm_username(self):
        # Ensure registration does NOT capture setlistfm_username
        res = self.client.post(reverse('register'), {
            'username': 'new_fan',
            'email': 'newfan@example.com',
            'setlistfm_username': 'SomeSetlistUser',  # Attempting to pass it
            'password1': 'NewFanPass123!',
            'password2': 'NewFanPass123!',
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        new_user = User.objects.get(username='new_fan')
        self.assertEqual(new_user.profile.setlistfm_username, '')

    def test_admin_impersonate_and_exit_flow(self):
        # Log in as admin
        self.client.login(username='admin_boss', password='AdminPassword123!')

        # Impersonate concert_fan
        res = self.client.post(reverse('impersonate_user'), {
            'user_id': self.regular_user.id
        }, follow=True)
        self.assertEqual(res.status_code, 200)

        # Check session and current logged in user
        self.assertEqual(self.client.session.get('impersonator_id'), self.admin_user.id)
        self.assertEqual(res.context['user'].id, self.regular_user.id)
        self.assertEqual(res.context['user'].username, 'concert_fan')
        self.assertTrue(res.context['is_impersonating'])
        self.assertEqual(res.context['impersonator'].id, self.admin_user.id)

        # Now exit impersonation
        res_exit = self.client.post(reverse('stop_impersonating'), follow=True)
        self.assertEqual(res_exit.status_code, 200)
        self.assertIsNone(self.client.session.get('impersonator_id'))
        self.assertEqual(res_exit.context['user'].id, self.admin_user.id)
        self.assertFalse(res_exit.context['is_impersonating'])

    def test_non_admin_cannot_impersonate(self):
        self.client.login(username='concert_fan', password='FanPassword123!')
        res = self.client.post(reverse('impersonate_user'), {
            'user_id': self.second_user.id
        }, follow=True)
        self.assertEqual(res.status_code, 200)
        # Should stay logged in as concert_fan
        self.assertEqual(res.context['user'].username, 'concert_fan')
        self.assertFalse(res.context['is_impersonating'])

    def test_admin_toggle_registration_enabled_disabled(self):
        # Check initial state: enabled
        self.assertTrue(SiteSetting.is_registration_enabled())

        # Admin logs in and toggles registration off
        self.client.login(username='admin_boss', password='AdminPassword123!')
        toggle_res = self.client.post(reverse('api_toggle_registration'), {
            'enabled': False
        }, content_type='application/json')
        self.assertEqual(toggle_res.status_code, 200)
        self.assertFalse(toggle_res.json()['registration_enabled'])
        self.assertFalse(SiteSetting.is_registration_enabled())

        # Log out
        self.client.logout()

        # Try to view registration page
        reg_get = self.client.get(reverse('register'))
        self.assertEqual(reg_get.status_code, 200)
        self.assertFalse(reg_get.context['registration_enabled'])
        self.assertContains(reg_get, "Registration Closed")

        # Try to register a new user
        reg_post = self.client.post(reverse('register'), {
            'username': 'blocked_user',
            'email': 'blocked@example.com',
            'password1': 'BlockedPass123!',
            'password2': 'BlockedPass123!',
        })
        self.assertEqual(reg_post.status_code, 200)
        self.assertFalse(User.objects.filter(username='blocked_user').exists())

        # Re-enable registration
        self.client.login(username='admin_boss', password='AdminPassword123!')
        toggle_res2 = self.client.post(reverse('api_toggle_registration'), {
            'enabled': True
        }, content_type='application/json')
        self.assertEqual(toggle_res2.status_code, 200)
        self.assertTrue(SiteSetting.is_registration_enabled())

    def test_friends_api_toggle_and_list(self):
        self.client.login(username='concert_fan', password='FanPassword123!')

        # Add rocker_jane as friend
        res_add = self.client.post(reverse('api_toggle_friend'), {
            'friend_id': self.second_user.id
        }, content_type='application/json')
        self.assertEqual(res_add.status_code, 200)
        self.assertTrue(res_add.json()['is_friend'])
        self.assertEqual(res_add.json()['friends_count'], 1)
        self.assertTrue(Friendship.objects.filter(user=self.regular_user, friend=self.second_user).exists())

        # Query list
        res_list = self.client.get(reverse('api_list_friends'))
        self.assertEqual(res_list.status_code, 200)
        data = res_list.json()
        self.assertEqual(data['total_friends'], 1)
        self.assertEqual(data['friends'][0]['username'], 'rocker_jane')

        # Toggle again to remove friend
        res_remove = self.client.post(reverse('api_toggle_friend'), {
            'friend_id': self.second_user.id
        }, content_type='application/json')
        self.assertEqual(res_remove.status_code, 200)
        self.assertFalse(res_remove.json()['is_friend'])
        self.assertEqual(res_remove.json()['friends_count'], 0)
        self.assertFalse(Friendship.objects.filter(user=self.regular_user, friend=self.second_user).exists())
