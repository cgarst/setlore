import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.core.models import UserProfile

class GuitarThemesTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='petruccifan', password='password123', email='john@example.com')
        self.profile = self.user.profile
        self.client = Client()

    def test_theme_choices_and_default(self):
        """Ensure UserProfile default theme is 'Default' and all 7 guitar colors are valid choices."""
        expected_themes = [
            'Default',
            'Blue Pearl',
            'Cerulean Paradise',
            'Dark Side',
            'Ember Glow',
            'Mystic Dream',
            'Purple Nebula',
            'Red Pearl Burst'
        ]
        available_choices = [c[0] for c in UserProfile.THEME_CHOICES]
        for t in expected_themes:
            self.assertIn(t, available_choices)
        self.assertEqual(self.profile.theme, 'Default')

    def test_set_theme_api_authenticated(self):
        """Ensure /api/theme/ persists theme to user profile when authenticated."""
        self.client.login(username='petruccifan', password='password123')
        
        for theme_name in ['Mystic Dream', 'Purple Nebula', 'Cerulean Paradise', 'Blue Pearl', 'Dark Side', 'Ember Glow', 'Red Pearl Burst']:
            res = self.client.post('/api/theme/', data=json.dumps({'theme': theme_name}), content_type='application/json')
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data.get('status'), 'ok')
            self.assertEqual(data.get('theme'), theme_name)
            
            self.profile.refresh_from_db()
            self.assertEqual(self.profile.theme, theme_name)

    def test_set_theme_api_anonymous(self):
        """Ensure /api/theme/ returns 200 for anonymous users without database persistence."""
        res = self.client.post('/api/theme/', data=json.dumps({'theme': 'Ember Glow'}), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('status'), 'ok')
        self.assertEqual(data.get('theme'), 'Ember Glow')

    def test_profile_update_api_theme(self):
        """Ensure /api/profile/update/ can update theme alongside other profile settings."""
        self.client.login(username='petruccifan', password='password123')
        res = self.client.post('/api/profile/update/', data=json.dumps({
            'theme': 'Dark Side',
            'setlistfm_username': 'jpetrucci'
        }), content_type='application/json')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get('profile', {}).get('theme'), 'Dark Side')
        self.assertEqual(data.get('profile', {}).get('setlistfm_username'), 'jpetrucci')

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.theme, 'Dark Side')

    def test_dashboard_renders_theme_elements(self):
        """Ensure dashboard template contains theme custom properties, theme selectors, and logo wrapper."""
        self.client.login(username='petruccifan', password='password123')
        res = self.client.get('/dashboard/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        # Check theme definitions
        self.assertIn('[data-theme="Blue Pearl"]', content)
        self.assertIn('[data-theme="Cerulean Paradise"]', content)
        self.assertIn('[data-theme="Dark Side"]', content)
        self.assertIn('[data-theme="Ember Glow"]', content)
        self.assertIn('[data-theme="Mystic Dream"]', content)
        self.assertIn('[data-theme="Purple Nebula"]', content)
        self.assertIn('[data-theme="Red Pearl Burst"]', content)

        # Check logo tint overlay & wrapper
        self.assertIn('logo-wrapper', content)
        self.assertIn('logo-tint-overlay', content)
        self.assertIn('logo-img', content)

        # Check theme menus
        self.assertIn('id="theme-menu-btn"', content)
        self.assertIn('id="theme-dropdown-menu"', content)
        self.assertIn('id="modal-theme-grid"', content)
        self.assertIn('id="mobile-sheet-theme-grid"', content)
