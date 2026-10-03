import json
from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.core.models import UserProfile
from apps.catalog.models import Venue, Artist
from apps.concerts.models import Concert, ConcertArtist, ConcertSong

class TicketmasterImportFlowTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tm_tester', password='password123')
        self.profile = self.user.profile
        self.profile.setlistfm_username = 'tm_tester'
        self.profile.save()
        self.client = Client()
        self.client.login(username='tm_tester', password='password123')

        # Create an existing concert with synthetic data to test deduplication
        self.venue = Venue.objects.create(name='Starlight Arena', city='Star City', state='CA', country='United States')
        self.artist = Artist.objects.create(name='Quantum Echo', normalized_name='quantum echo')
        self.existing_concert = Concert.objects.create(
            user=self.user,
            date='2026-10-24',
            raw_date='10/24/2026',
            year=2026,
            venue=self.venue,
            raw_venue='Starlight Arena',
            primary_artist=self.artist,
            raw_artists='Quantum Echo',
            source='manual'
        )

    def test_preview_endpoint_parsing_and_deduplication(self):
        paste_text = """
        OCT 24, 2026
        Quantum Echo - Nebula World Tour
        Starlight Arena - Star City, CA
        Order # 11-22334/CA1
        Past Event

        Fri, Aug 25, 2025 • 7:30 PM
        Cosmic Voyager: Odyssey 2025
        Solaris Amphitheater - Austin, TX
        Order # 55-66778/TX2
        """
        response = self.client.post(
            '/api/ticketmaster/preview/',
            data=json.dumps({'raw_text': paste_text}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['total_parsed'], 2)
        self.assertEqual(data['duplicates_count'], 1)

        events = data['events']
        # First event is duplicate of Quantum Echo on 2026-10-24
        self.assertTrue(events[0]['is_duplicate'])
        self.assertFalse(events[0]['selected'])
        self.assertEqual(events[0]['artist'], 'Quantum Echo')
        self.assertEqual(events[0]['order_number'], '11-22334/CA1')

        # Second event is new (Cosmic Voyager)
        self.assertFalse(events[1]['is_duplicate'])
        self.assertTrue(events[1]['selected'])
        self.assertEqual(events[1]['artist'], 'Cosmic Voyager')
        self.assertEqual(events[1]['venue'], 'Solaris Amphitheater')
        self.assertEqual(events[1]['city'], 'Austin')
        self.assertEqual(events[1]['state'], 'TX')

    def test_confirm_import_endpoint(self):
        events_to_confirm = [
            {
                'date': '2025-08-25',
                'artist': 'Cosmic Voyager',
                'venue': 'Solaris Amphitheater',
                'city': 'Austin',
                'state': 'TX',
                'country': 'United States',
                'tour_notes': 'Odyssey 2025',
                'order_number': '55-66778/TX2',
                'setlist_id': ''
            }
        ]
        response = self.client.post(
            '/api/ticketmaster/confirm/',
            data=json.dumps({'events': events_to_confirm}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['imported_count'], 1)

        # Verify in database
        cosmic_concert = Concert.objects.filter(user=self.user, primary_artist__name='Cosmic Voyager').first()
        self.assertIsNotNone(cosmic_concert)
        self.assertEqual(str(cosmic_concert.date), '2025-08-25')
        self.assertEqual(cosmic_concert.source, 'ticketmaster')
        self.assertTrue(cosmic_concert.is_custom_offline)
        self.assertIn('Odyssey 2025', cosmic_concert.notes)
        self.assertIn('Ticketmaster Order: 55-66778/TX2', cosmic_concert.notes)
