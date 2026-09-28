import unittest
from src.ticketmaster_parser import parse_ticketmaster_text, parse_tm_date, extract_venue_and_location, clean_event_title

class TestTicketmasterParser(unittest.TestCase):

    def test_parse_date_formats(self):
        self.assertEqual(parse_tm_date("OCT 24, 2024").strftime("%Y-%m-%d"), "2024-10-24")
        self.assertEqual(parse_tm_date("Thu, Oct 24, 2024 • 7:30 PM").strftime("%Y-%m-%d"), "2024-10-24")
        self.assertEqual(parse_tm_date("Sat • Nov 02, 2024 • 8:00 PM").strftime("%Y-%m-%d"), "2024-11-02")
        self.assertEqual(parse_tm_date("Friday, Aug 25, 2023 at 7:30 PM").strftime("%Y-%m-%d"), "2023-08-25")
        self.assertEqual(parse_tm_date("10/24/2024").strftime("%Y-%m-%d"), "2024-10-24")
        self.assertEqual(parse_tm_date("2024-05-18").strftime("%Y-%m-%d"), "2024-05-18")

    def test_extract_venue_and_location(self):
        v, c, s = extract_venue_and_location("Capital One Arena - Washington, DC")
        self.assertEqual(v, "Capital One Arena")
        self.assertEqual(c, "Washington")
        self.assertEqual(s, "DC")

        v2, c2, s2 = extract_venue_and_location("The Anthem, Washington, DC")
        self.assertEqual(v2, "The Anthem")
        self.assertEqual(c2, "Washington")
        self.assertEqual(s2, "DC")

        v3, c3, s3 = extract_venue_and_location("CFG Bank Arena - Baltimore, MD Order # 12345")
        self.assertEqual(v3, "CFG Bank Arena")
        self.assertEqual(c3, "Baltimore")
        self.assertEqual(s3, "MD")

    def test_clean_event_title(self):
        art, tour = clean_event_title("Iron Maiden - The Future Past Tour")
        self.assertEqual(art, "Iron Maiden")
        self.assertEqual(tour, "The Future Past Tour")

        art2, tour2 = clean_event_title("Ghost: RE-IMPERATOUR U.S.A. 2023")
        self.assertEqual(art2, "Ghost")
        self.assertEqual(tour2, "RE-IMPERATOUR U.S.A. 2023")

        art3, tour3 = clean_event_title("Hans Zimmer Live")
        self.assertEqual(art3, "Hans Zimmer Live")
        self.assertEqual(tour3, "")

    def test_multiline_paste(self):
        sample = """
        OCT 24, 2024
        Iron Maiden - The Future Past Tour
        Capital One Arena - Washington, DC
        Order # 12-34567/VA1
        Past Event

        Fri, Aug 25, 2023 • 7:30 PM
        Ghost: RE-IMPERATOUR U.S.A. 2023
        Jiffy Lube Live - Bristow, VA
        Order # 98-76543/VA2
        View Order Details
        """
        events = parse_ticketmaster_text(sample)
        self.assertEqual(len(events), 2)

        self.assertEqual(events[0]["artist"], "Iron Maiden")
        self.assertEqual(events[0]["venue"], "Capital One Arena")
        self.assertEqual(events[0]["city"], "Washington")
        self.assertEqual(events[0]["state"], "DC")
        self.assertEqual(events[0]["date"], "2024-10-24")
        self.assertEqual(events[0]["order_number"], "12-34567/VA1")
        self.assertEqual(events[0]["tour_notes"], "The Future Past Tour")

        self.assertEqual(events[1]["artist"], "Ghost")
        self.assertEqual(events[1]["venue"], "Jiffy Lube Live")
        self.assertEqual(events[1]["city"], "Bristow")
        self.assertEqual(events[1]["state"], "VA")
        self.assertEqual(events[1]["date"], "2023-08-25")
        self.assertEqual(events[1]["order_number"], "98-76543/VA2")
        self.assertEqual(events[1]["tour_notes"], "RE-IMPERATOUR U.S.A. 2023")

    def test_exact_ticketmaster_order_history_paste(self):
        sample = """2026
    Iron Maiden: Run For Your Lives World Tour 2026
    Friday, September 11, 2026

    Fri • Sep 11, 2026

    Jiffy Lube Live
    View details

    Order #3-12036/WDC
    RUSH: Fifty Something
    Monday, August 3, 2026

    Mon • Aug 3, 2026

    Madison Square Garden
    View details

    Order #60-27114/NY7
2025
    BEAR McCREARY "THEMES & VARIATIONS" TOUR 2025
    Sunday, May 25, 2025

    Sun • May 25, 2025

    9:30 CLUB

    Order #40-42562/WDC
    Devin Townsend's PowerNerd Tour! w/ Tesseract
    Saturday, May 10, 2025

    Sat • May 10, 2025

    The Fillmore Silver Spring

    Order #14-18736/WDC
    Dream Theater: 40th Anniversary Tour
    Friday, March 21, 2025

    Fri • Mar 21, 2025

    The Anthem

    Order #36-24832/WDC
2024
    OPETH: North American Tour 2024
    Sunday, October 20, 2024

    Sun • Oct 20, 2024

    Warner Theatre

    Order #31-27893/WDC
    Ringo Starr and His All Starr Band
    Tuesday, September 17, 2024

    Tue • Sep 17, 2024

    The Anthem

    Order #56-32416/WDC
        """
        events = parse_ticketmaster_text(sample)
        self.assertEqual(len(events), 7)

        # 1. Iron Maiden
        self.assertEqual(events[0]["artist"], "Iron Maiden")
        self.assertEqual(events[0]["venue"], "Jiffy Lube Live")
        self.assertEqual(events[0]["date"], "2026-09-11")
        self.assertEqual(events[0]["order_number"], "3-12036/WDC")
        self.assertEqual(events[0]["tour_notes"], "Run For Your Lives World Tour 2026")

        # 2. RUSH
        self.assertEqual(events[1]["artist"], "RUSH")
        self.assertEqual(events[1]["venue"], "Madison Square Garden")
        self.assertEqual(events[1]["date"], "2026-08-03")
        self.assertEqual(events[1]["order_number"], "60-27114/NY7")
        self.assertEqual(events[1]["tour_notes"], "Fifty Something")

        # 3. Bear McCreary
        self.assertEqual(events[2]["artist"], "BEAR McCREARY")
        self.assertEqual(events[2]["venue"], "9:30 CLUB")
        self.assertEqual(events[2]["date"], "2025-05-25")
        self.assertEqual(events[2]["order_number"], "40-42562/WDC")

        # 4. Devin Townsend
        self.assertEqual(events[3]["artist"], "Devin Townsend")
        self.assertEqual(events[3]["venue"], "The Fillmore Silver Spring")
        self.assertEqual(events[3]["date"], "2025-05-10")
        self.assertEqual(events[3]["order_number"], "14-18736/WDC")

if __name__ == "__main__":
    unittest.main()

