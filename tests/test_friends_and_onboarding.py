from django.test import TestCase, Client
from django.contrib.auth.models import User
from apps.core.models import Friendship, UserProfile
from apps.catalog.models import Venue, Artist
from apps.concerts.models import Concert

class FriendsAndOnboardingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user1 = User.objects.create_user(username='alice', password='password123')
        self.user2 = User.objects.create_user(username='bob', password='password123')
        self.user3 = User.objects.create_user(username='charlie', password='password123')

        # Create sample concert for user1
        venue = Venue.objects.create(name='9:30 Club', city='Washington', state='DC')
        artist = Artist.objects.create(name='Foo Fighters', normalized_name='foo fighters')
        Concert.objects.create(
            user=self.user1,
            raw_date='10/24/2023',
            year=2023,
            venue=venue,
            primary_artist='Foo Fighters'
        )

    def test_onboarding_content_matches_modal_guidance(self):
        self.client.force_login(self.user1)
        res = self.client.get('/overview/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')

        # Ticketmaster step in onboarding matches modal
        self.assertIn('ticketmaster.com/user/orders/past-events', content)
        self.assertIn('Select &amp; Copy', content)
        self.assertIn('Paste Past Events Text', content)
        self.assertNotIn('order emails or purchase confirmation pages', content)

        # CSV step in onboarding matches modal
        self.assertIn('Expected CSV Format &amp; Column Order', content)
        self.assertIn('Col 1: Date', content)
        self.assertIn('Col 2: Artist(s)', content)
        self.assertIn('Col 3: Venue', content)
        self.assertIn('Multiple artists', content)

    def test_friends_page_rendered_on_dashboard(self):
        self.client.force_login(self.user1)
        res = self.client.get('/friends/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context['initial_tab'], 'friends')
        content = res.content.decode('utf-8')

        # Friends tab exists for dashboard owner
        self.assertIn('id="tab-friends"', content)
        self.assertIn('Friends &amp; Community', content)
        self.assertIn('view their profiles', content)
        self.assertNotIn('view their private profiles with shared access', content)
        self.assertIn('id="tab-friends-btn"', content)

    def test_friends_page_not_shown_on_public_profile(self):
        self.client.force_login(self.user2)
        # Attempt to access friends tab on user1's public profile
        res = self.client.get('/u/alice/friends/')
        self.assertEqual(res.status_code, 200)
        # Initial tab is forced back to overview
        self.assertEqual(res.context['initial_tab'], 'overview')
        content = res.content.decode('utf-8')

        # tab-friends is not rendered in public view
        self.assertNotIn('id="tab-friends"', content)
        self.assertNotIn('id="tab-friends-btn"', content)

    def test_private_profile_blocked_for_strangers(self):
        # Make alice private
        self.user1.profile.is_public = False
        self.user1.profile.save()

        # Unauthenticated visitor
        anon_res = self.client.get('/u/alice/')
        self.assertEqual(anon_res.status_code, 403)
        self.assertIn('This Profile is Private', anon_res.content.decode('utf-8'))

        # Logged-in non-friend visitor
        self.client.force_login(self.user2)
        stranger_res = self.client.get('/u/alice/')
        self.assertEqual(stranger_res.status_code, 403)
        self.assertIn('This Profile is Private', stranger_res.content.decode('utf-8'))

    def test_private_profile_accessible_by_friend(self):
        # Make alice private
        self.user1.profile.is_public = False
        self.user1.profile.save()

        # Mutual friendship between Alice and Bob
        Friendship.objects.create(user=self.user1, friend=self.user2)
        Friendship.objects.create(user=self.user2, friend=self.user1)

        # Bob logs in and visits Alice's private profile
        self.client.force_login(self.user2)
        friend_res = self.client.get('/u/alice/')
        self.assertEqual(friend_res.status_code, 200)
        content = friend_res.content.decode('utf-8')
        self.assertIn("alice's Concert Collection", content)
        self.assertIn('Private Profile (Friend Access)', content)

    def test_add_friend_button_on_public_profile(self):
        # Alice is public
        self.user1.profile.is_public = True
        self.user1.profile.save()

        # Bob views Alice's profile (not friends yet)
        self.client.force_login(self.user2)
        res = self.client.get('/u/alice/')
        self.assertEqual(res.status_code, 200)
        content = res.content.decode('utf-8')
        self.assertIn('id="public-profile-friend-btn"', content)
        self.assertIn('Add Friend', content)

        # Bob toggles friend (sends friend request)
        post_res = self.client.post('/api/friends/toggle/', {
            'friend_id': self.user1.id
        })
        self.assertEqual(post_res.status_code, 200)
        self.assertTrue(post_res.json()['is_friend'])

        # Now Bob views Alice's profile again -> should show "Request Sent" (awaiting mutual confirmation)
        res_after = self.client.get('/u/alice/')
        self.assertEqual(res_after.status_code, 200)
        content_after = res_after.content.decode('utf-8')
        self.assertIn('Request Sent', content_after)

    def test_co_attended_concerts_calculation_and_rendering(self):
        # Alice and Bob both attended Foo Fighters at 9:30 Club on 10/24/2023
        venue = Venue.objects.get(name='9:30 Club')
        Concert.objects.create(
            user=self.user2,
            raw_date='10/24/2023',
            year=2023,
            venue=venue,
            primary_artist='Foo Fighters'
        )

        # Charlie only attended Iron Maiden
        venue2 = Venue.objects.create(name='Capital One Arena', city='Washington', state='DC')
        Concert.objects.create(
            user=self.user3,
            raw_date='11/12/2024',
            year=2024,
            venue=venue2,
            primary_artist='Iron Maiden'
        )

        # Mutual friendship between Alice and Bob
        Friendship.objects.create(user=self.user1, friend=self.user2)
        Friendship.objects.create(user=self.user2, friend=self.user1)

        # Alice checks her friends page with 1 friend
        self.client.force_login(self.user1)
        res = self.client.get('/friends/')
        self.assertEqual(res.status_code, 200)

        # Check friends_list context
        friends_by_name = {f['username']: f for f in res.context['friends_list']}
        self.assertEqual(friends_by_name['bob']['co_attended_count'], 1)
        self.assertEqual(len(friends_by_name['bob']['co_attended_concerts']), 1)
        self.assertEqual(friends_by_name['bob']['co_attended_concerts'][0]['artist'], 'Foo Fighters')

        # Check HTML rendering with 1 friend: should render "1 Friend"
        content = res.content.decode('utf-8')
        self.assertIn('1 Friend', content)
        self.assertNotIn('1 Friends', content)
        self.assertIn('Co-Attended Show', content)
        self.assertIn('friend-co-', content)
        self.assertIn('Foo Fighters', content)

        # Now make Charlie a mutual friend too -> should render "2 Friends"
        Friendship.objects.create(user=self.user1, friend=self.user3)
        Friendship.objects.create(user=self.user3, friend=self.user1)

        res2 = self.client.get('/friends/')
        self.assertEqual(res2.status_code, 200)
        content2 = res2.content.decode('utf-8')
        self.assertIn('2 Friends', content2)
