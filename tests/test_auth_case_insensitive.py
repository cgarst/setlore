from django.test import TestCase, Client
from django.contrib.auth import get_user_model, authenticate
from django.urls import reverse
from apps.core.forms import CaseInsensitiveUserCreationForm

User = get_user_model()


class CaseInsensitiveAuthTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='TestUser',
            email='test@example.com',
            password='SecretPassword123'
        )

    def test_authenticate_case_insensitive(self):
        # Exact match
        user1 = authenticate(username='TestUser', password='SecretPassword123')
        self.assertIsNotNone(user1)
        self.assertEqual(user1.pk, self.user.pk)

        # Lowercase
        user2 = authenticate(username='testuser', password='SecretPassword123')
        self.assertIsNotNone(user2)
        self.assertEqual(user2.pk, self.user.pk)

        # Uppercase
        user3 = authenticate(username='TESTUSER', password='SecretPassword123')
        self.assertIsNotNone(user3)
        self.assertEqual(user3.pk, self.user.pk)

        # Mixed casing
        user4 = authenticate(username='tEstUSeR', password='SecretPassword123')
        self.assertIsNotNone(user4)
        self.assertEqual(user4.pk, self.user.pk)

        # Wrong password
        user_bad = authenticate(username='testuser', password='WrongPassword')
        self.assertIsNone(user_bad)

    def test_login_form_case_insensitive(self):
        login_url = reverse('login')
        response = self.client.post(login_url, {
            'username': 'TESTUSER',
            'password': 'SecretPassword123'
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['user'].is_authenticated)
        self.assertEqual(response.context['user'].username, 'TestUser')

    def test_preserves_display_casing(self):
        # Register a new user with mixed casing like 'Zathu'
        register_url = reverse('register')
        response = self.client.post(register_url, {
            'username': 'Zathu',
            'setlistfm_username': 'Zathu',
            'password1': 'ZathuPass123!',
            'password2': 'ZathuPass123!',
        }, follow=True)
        self.assertEqual(response.status_code, 200)

        # Ensure user object in DB has exact casing 'Zathu'
        zathu_user = User.objects.get(username__iexact='zathu')
        self.assertEqual(zathu_user.username, 'Zathu')
        self.assertEqual(zathu_user.profile.setlistfm_username, 'Zathu')

        # Log out and log back in with all lowercase 'zathu'
        self.client.logout()
        login_url = reverse('login')
        login_res = self.client.post(login_url, {
            'username': 'zathu',
            'password': 'ZathuPass123!'
        }, follow=True)
        self.assertEqual(login_res.status_code, 200)
        self.assertEqual(login_res.context['user'].username, 'Zathu')

    def test_registration_form_case_insensitive_duplicate_check(self):
        # Attempt to register 'testuser' when 'TestUser' already exists
        form = CaseInsensitiveUserCreationForm(data={
            'username': 'testuser',
            'password1': 'NewPassword123!',
            'password2': 'NewPassword123!',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)
        self.assertTrue(any('already exists' in err for err in form.errors['username']))


