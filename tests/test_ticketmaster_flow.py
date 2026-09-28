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

        # Create an existing concert to test deduplication
        self.venue = Venue.objects.create(name='Capital One Arena', city='Washington', state='DC', country='United States')
        self.artist = Artist.objects.create(name='Iron Maiden', normalized_name='iron maiden')
        self.existing_concert = Concert.objects.create(
            user=self.user,
            date='2024-10-24',
            raw_date='10/24/2024',
            year=2024,
            venue=self.venue,
            raw_venue='Capital One Arena',
            primary_artist='Iron Maiden',
            raw_artists='Iron Maiden',
            source='manual'
        )

    def test_preview_endpoint_parsing_and_deduplication(self):
        paste_text = """
        OCT 24, 2024
        Iron Maiden - The Future Past Tour
        Capital One Arena - Washington, DC
        Order # 12-34567/VA1
        Past Event

        Fri, Aug 25, 2023 • 7:30 PM
        Ghost: RE-IMPERATOUR U.S.A. 2023
        Jiffy Lube Live - Bristow, VA
        Order # 98-76543/VA2
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
        # First event is duplicate of Iron Maiden on 2024-10-24
        self.assertTrue(events[0]['is_duplicate'])
        self.assertFalse(events[0]['selected'])
        self.assertEqual(events[0]['artist'], 'Iron Maiden')
        self.assertEqual(events[0]['order_number'], '12-34567/VA1')

        # Second event is new (Ghost)
        self.assertFalse(events[1]['is_duplicate'])
        self.assertTrue(events[1]['selected'])
        self.assertEqual(events[1]['artist'], 'Ghost')
        self.assertEqual(events[1]['venue'], 'Jiffy Lube Live')
        self.assertEqual(events[1]['city'], 'Bristow')
        self.assertEqual(events[1]['state'], 'VA')

    def test_confirm_import_endpoint(self):
        events_to_confirm = [
            {
                'date': '2023-08-25',
                'artist': 'Ghost',
                'venue': 'Jiffy Lube Live',
                'city': 'Bristow',
                'state': 'VA',
                'country': 'United States',
                'tour_notes': 'RE-IMPERATOUR U.S.A. 2023',
                'order_number': '98-76543/VA2',
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
        ghost_concert = Concert.objects.filter(user=self.user, primary_artist='Ghost').first()
        self.assertIsNotNone(ghost_concert)
        self.assertEqual(str(ghost_concert.date), '2023-08-25')
        self.assertEqual(ghost_concert.source, 'ticketmaster')
        self.assertTrue(ghost_concert.is_custom_offline)
        self.assertIn('RE-IMPERATOUR', ghost_concert.notes)
        self.assertIn('Ticketmaster Order: 98-76543/VA2', ghost_concert.notes)
