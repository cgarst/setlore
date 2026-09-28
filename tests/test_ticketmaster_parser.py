import unittest
from src.ticketmaster_parser import parse_ticketmaster_text, parse_tm_date, extract_venue_and_location, clean_event_title

class TestTicketmasterParser(unittest.TestCase):

    def test_parse_date_formats(self):
        self.assertEqual(parse_tm_date("OCT 24, 2026").strftime("%Y-%m-%d"), "2026-10-24")
        self.assertEqual(parse_tm_date("Thu, Oct 24, 2026 • 7:30 PM").strftime("%Y-%m-%d"), "2026-10-24")
        self.assertEqual(parse_tm_date("Sat • Nov 02, 2026 • 8:00 PM").strftime("%Y-%m-%d"), "2026-11-02")
        self.assertEqual(parse_tm_date("Friday, Aug 25, 2025 at 7:30 PM").strftime("%Y-%m-%d"), "2025-08-25")
        self.assertEqual(parse_tm_date("10/24/2026").strftime("%Y-%m-%d"), "2026-10-24")
        self.assertEqual(parse_tm_date("2026-05-18").strftime("%Y-%m-%d"), "2026-05-18")

    def test_extract_venue_and_location(self):
        v, c, s = extract_venue_and_location("Starlight Arena - Star City, CA")
        self.assertEqual(v, "Starlight Arena")
        self.assertEqual(c, "Star City")
        self.assertEqual(s, "CA")

        v2, c2, s2 = extract_venue_and_location("The Neon Lounge, Metro City, NY")
        self.assertEqual(v2, "The Neon Lounge")
        self.assertEqual(c2, "Metro City")
        self.assertEqual(s2, "NY")

        v3, c3, s3 = extract_venue_and_location("Solaris Pavilion - Austin, TX Order # 11111")
        self.assertEqual(v3, "Solaris Pavilion")
        self.assertEqual(c3, "Austin")
        self.assertEqual(s3, "TX")

    def test_clean_event_title(self):
        art, tour = clean_event_title("Quantum Echo - Nebula World Tour")
        self.assertEqual(art, "Quantum Echo")
        self.assertEqual(tour, "Nebula World Tour")

        art2, tour2 = clean_event_title("Cosmic Voyager: Odyssey 2026")
        self.assertEqual(art2, "Cosmic Voyager")
        self.assertEqual(tour2, "Odyssey 2026")

        art3, tour3 = clean_event_title("The Electric Soundscape")
        self.assertEqual(art3, "The Electric Soundscape")
        self.assertEqual(tour3, "")

    def test_multiline_paste(self):
        sample = """
        OCT 24, 2026
        Quantum Echo - Nebula World Tour
        Starlight Arena - Star City, CA
        Order # 11-22334/CA1
        Past Event

        Fri, Aug 25, 2025 • 7:30 PM
        Cosmic Voyager: Odyssey 2025
        Solaris Amphitheater - Austin, TX
        Order # 55-66778/TX2
        View Order Details
        """
        events = parse_ticketmaster_text(sample)
        self.assertEqual(len(events), 2)

        self.assertEqual(events[0]["artist"], "Quantum Echo")
        self.assertEqual(events[0]["venue"], "Starlight Arena")
        self.assertEqual(events[0]["city"], "Star City")
        self.assertEqual(events[0]["state"], "CA")
        self.assertEqual(events[0]["date"], "2026-10-24")
        self.assertEqual(events[0]["order_number"], "11-22334/CA1")
        self.assertEqual(events[0]["tour_notes"], "Nebula World Tour")

        self.assertEqual(events[1]["artist"], "Cosmic Voyager")
        self.assertEqual(events[1]["venue"], "Solaris Amphitheater")
        self.assertEqual(events[1]["city"], "Austin")
        self.assertEqual(events[1]["state"], "TX")
        self.assertEqual(events[1]["date"], "2025-08-25")
        self.assertEqual(events[1]["order_number"], "55-66778/TX2")
        self.assertEqual(events[1]["tour_notes"], "Odyssey 2025")

    def test_exact_ticketmaster_order_history_paste(self):
        sample = """2026
    Quantum Echo: Supernova World Tour 2026
    Friday, September 11, 2026

    Fri • Sep 11, 2026

    Starlight Amphitheater
    View details

    Order #10-10001/DEMO
    Cosmic Voyager: Fifty Galaxies Tour
    Monday, August 3, 2026

    Mon • Aug 3, 2026

    Metropolis Arena
    View details

    Order #20-20002/DEMO
2025
    AETHERIA "SYNTHETIC DREAMS" TOUR 2025
    Sunday, May 25, 2025

    Sun • May 25, 2025

    The Neon Pavilion

    Order #30-30003/DEMO
    Soundwave Collective's Aurora Tour! w/ Stellar Pulse
    Saturday, May 10, 2025

    Sat • May 10, 2025

    Cyber City Hall

    Order #40-40004/DEMO
    Midnight Eclipse: 20th Anniversary Tour
    Friday, March 21, 2025

    Fri • Mar 21, 2025

    The Grand Ballroom

    Order #50-50005/DEMO
        """
        events = parse_ticketmaster_text(sample)
        self.assertEqual(len(events), 5)

        # 1. Quantum Echo
        self.assertEqual(events[0]["artist"], "Quantum Echo")
        self.assertEqual(events[0]["venue"], "Starlight Amphitheater")
        self.assertEqual(events[0]["date"], "2026-09-11")
        self.assertEqual(events[0]["order_number"], "10-10001/DEMO")
        self.assertEqual(events[0]["tour_notes"], "Supernova World Tour 2026")

        # 2. Cosmic Voyager
        self.assertEqual(events[1]["artist"], "Cosmic Voyager")
        self.assertEqual(events[1]["venue"], "Metropolis Arena")
        self.assertEqual(events[1]["date"], "2026-08-03")
        self.assertEqual(events[1]["order_number"], "20-20002/DEMO")
        self.assertEqual(events[1]["tour_notes"], "Fifty Galaxies Tour")

        # 3. Aetheria
        self.assertEqual(events[2]["artist"], "AETHERIA")
        self.assertEqual(events[2]["venue"], "The Neon Pavilion")
        self.assertEqual(events[2]["date"], "2025-05-25")
        self.assertEqual(events[2]["order_number"], "30-30003/DEMO")

        # 4. Soundwave Collective
        self.assertEqual(events[3]["artist"], "Soundwave Collective")
        self.assertEqual(events[3]["venue"], "Cyber City Hall")
        self.assertEqual(events[3]["date"], "2025-05-10")
        self.assertEqual(events[3]["order_number"], "40-40004/DEMO")

    def test_full_page_copypaste_with_excess_noise(self):
        sample = """Skip to main content
USUnited States selected, change country

    Hotels
    Sell
    Gift Cards
    Help
    VIP

PayPal Preferred Payments Partner
Ticketmaster Home page

Search
Search
My Account

    Home
    My Tickets

My Tickets

    Upcoming Events
    Past Events
    My Listings

Welcome back! Alex
My Tickets

    Upcoming Events
    Past Events
    My Listings

My Profile
My Settings
Sign Out
Need Help?
Loaded 12 past orders

2026
    Quantum Echo: Supernova World Tour 2026
    Friday, September 11, 2026

    Fri • Sep 11, 2026

    Starlight Amphitheater
    View details

    Order #10-10001/DEMO
    Cosmic Voyager: Fifty Galaxies Tour
    Monday, August 3, 2026

    Mon • Aug 3, 2026

    Metropolis Arena
    View details

    Order #20-20002/DEMO

Ticketmaster Logo
Let's connect

    Facebook(Opens in new tab)X(Opens in new tab)Blog(Opens in new tab)Youtube(Opens in new tab)Instagram(Opens in new tab)

Download Our Apps

    Download on the App Store(Opens in new tab)Get it on Google Play(Opens in new tab)

By continuing past this page, you agree to our terms of use
Helpful Links

    Help/FAQ
    Sell
    My Account
    Contact Us
    Gift Cards
    Do Not Sell or Share My Personal Information
    Get Started on Ticketmaster

Our Network

    Live Nation
    House of Blues
    Front Gate Tickets
    TicketWeb
    universe
    NFL
    NBA
    NHL

About Us

    Ticketmaster Blog
    Ticketing Truths
    Ad Choices
    Careers
    Ticket Your Event
    Innovation

Friends & Partners

    PayPal
    Allianz
    AWS
    Affiliates

    Our Policies
    Privacy Policy
    Cookie Policy
    Manage my cookies and ad choices

© 1999-2026 Ticketmaster. All rights reserved.
        """
        events = parse_ticketmaster_text(sample)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["artist"], "Quantum Echo")
        self.assertEqual(events[0]["venue"], "Starlight Amphitheater")
        self.assertEqual(events[0]["date"], "2026-09-11")
        self.assertEqual(events[0]["order_number"], "10-10001/DEMO")
        self.assertEqual(events[0]["tour_notes"], "Supernova World Tour 2026")

        self.assertEqual(events[1]["artist"], "Cosmic Voyager")
        self.assertEqual(events[1]["venue"], "Metropolis Arena")
        self.assertEqual(events[1]["date"], "2026-08-03")
        self.assertEqual(events[1]["order_number"], "20-20002/DEMO")
        self.assertEqual(events[1]["tour_notes"], "Fifty Galaxies Tour")

if __name__ == "__main__":
    unittest.main()
