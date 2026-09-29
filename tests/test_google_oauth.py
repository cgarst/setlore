import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from apps.core import oauth
from apps.core.models import UserProfile


class GoogleOAuthUnitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="existinguser", email="existing@example.com", password="password123")

    def test_unique_username_generator(self):
        username1 = oauth.generate_unique_username("rockstar@gmail.com")
        self.assertEqual(username1, "rockstar")

        User.objects.create_user(username="rockstar", email="other@example.com")
        username2 = oauth.generate_unique_username("rockstar@gmail.com")
        self.assertEqual(username2, "rockstar_2")

    def test_new_user_registration_with_google(self):
        user, created, action = oauth.authenticate_or_register_google_user(
            sub="google-sub-12345",
            email="newuser@example.com",
            name="New User"
        )
        self.assertTrue(created)
        self.assertEqual(action, "new_registration")
        self.assertEqual(user.email, "newuser@example.com")
        self.assertEqual(user.profile.google_id, "google-sub-12345")
        self.assertEqual(user.profile.google_email, "newuser@example.com")
        self.assertFalse(user.has_usable_password())

    def test_map_to_existing_account_by_email(self):
        # Existing user created in setUp with email existing@example.com
        user, created, action = oauth.authenticate_or_register_google_user(
            sub="google-sub-99999",
            email="EXISTING@EXAMPLE.COM",  # Test case insensitivity
            name="Existing User"
        )
        self.assertFalse(created)
        self.assertEqual(action, "mapped_by_email")
        self.assertEqual(user.pk, self.user.pk)
        self.assertEqual(user.profile.google_id, "google-sub-99999")
        self.assertEqual(user.profile.google_email, "existing@example.com")

    def test_existing_google_id_login(self):
        self.user.profile.google_id = "google-sub-already-linked"
        self.user.profile.google_email = "existing@example.com"
        self.user.profile.save()

        user, created, action = oauth.authenticate_or_register_google_user(
            sub="google-sub-already-linked",
            email="existing@example.com"
        )
        self.assertFalse(created)
        self.assertEqual(action, "existing_google_id")
        self.assertEqual(user.pk, self.user.pk)

    def test_link_google_account_to_authenticated_user(self):
        success, msg = oauth.link_google_account_to_user(
            self.user,
            sub="google-link-sub-555",
            email="connected@gmail.com"
        )
        self.assertTrue(success)
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile.google_id, "google-link-sub-555")
        self.assertEqual(self.user.profile.google_email, "connected@gmail.com")

    def test_prevent_linking_if_already_linked_to_other_user(self):
        other_user = User.objects.create_user(username="otheruser", email="other@example.com")
        other_user.profile.google_id = "shared-sub-id"
        other_user.profile.save()

        success, msg = oauth.link_google_account_to_user(
            self.user,
            sub="shared-sub-id",
            email="other@example.com"
        )
        self.assertFalse(success)
        self.assertIn("already connected to another user", msg)
        self.assertIsNone(self.user.profile.google_id)

    def test_unlink_google_account(self):
        self.user.profile.google_id = "sub-to-remove"
        self.user.profile.google_email = "remove@example.com"
        self.user.profile.save()

        success, msg = oauth.unlink_google_account_from_user(self.user)
        self.assertTrue(success)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile.google_id)
        self.assertEqual(self.user.profile.google_email, "")


@override_settings(GOOGLE_CLIENT_ID="mock-client-id", GOOGLE_CLIENT_SECRET="mock-client-secret")
class GoogleOAuthViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="password123")

    def test_google_login_redirects_to_google(self):
        response = self.client.get(reverse('google_login'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("https://accounts.google.com/o/oauth2/v2/auth"))
        self.assertIn("client_id=mock-client-id", response.url)
        self.assertIn("accounts%2Fgoogle%2Fcallback%2F", response.url)
        session = self.client.session
        self.assertIn('google_oauth_state', session)

    @override_settings(GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="")
    def test_google_login_unconfigured(self):
        response = self.client.get(reverse('google_login'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('login'))

    @patch('apps.core.oauth.fetch_google_user_info')
    @patch('apps.core.oauth.exchange_code_for_token')
    def test_google_callback_registers_new_user(self, mock_exchange, mock_userinfo):
        mock_exchange.return_value = {'access_token': 'mock-access-token'}
        mock_userinfo.return_value = {
            'sub': 'sub-google-new-987',
            'email': 'brandnewuser@gmail.com',
            'name': 'Brand New'
        }

        session = self.client.session
        session['google_oauth_state'] = 'test-state-123'
        session.save()

        response = self.client.get(reverse('google_callback') + "?code=auth-code-xyz&state=test-state-123")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))

        created_user = User.objects.filter(email='brandnewuser@gmail.com').first()
        self.assertIsNotNone(created_user)
        self.assertEqual(created_user.profile.google_id, 'sub-google-new-987')

    @patch('apps.core.oauth.fetch_google_user_info')
    @patch('apps.core.oauth.exchange_code_for_token')
    def test_google_callback_maps_existing_email(self, mock_exchange, mock_userinfo):
        mock_exchange.return_value = {'access_token': 'mock-access-token'}
        mock_userinfo.return_value = {
            'sub': 'sub-google-existing-user',
            'email': 'test@example.com',
            'name': 'Test User'
        }

        session = self.client.session
        session['google_oauth_state'] = 'test-state-abc'
        session.save()

        response = self.client.get(reverse('google_callback') + "?code=auth-code-abc&state=test-state-abc")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))

        self.user.refresh_from_db()
        self.assertEqual(self.user.profile.google_id, 'sub-google-existing-user')
        self.assertEqual(self.user.profile.google_email, 'test@example.com')

    def test_google_disconnect_ajax(self):
        self.user.profile.google_id = 'sub-to-disconnect'
        self.user.profile.google_email = 'test@example.com'
        self.user.profile.save()

        self.client.force_login(self.user)
        response = self.client.post(
            reverse('google_disconnect'),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')
        self.assertFalse(data['has_google_linked'])

        self.user.refresh_from_db()
        self.assertIsNone(self.user.profile.google_id)
