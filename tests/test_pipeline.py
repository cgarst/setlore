import unittest
from pathlib import Path
from src.csv_parser import parse_date, clean_artists_string
from src.gap_analysis import match_score, reconcile_history

class TestConcertPipeline(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(parse_date("7/29/2003").strftime("%d-%m-%Y"), "29-07-2003")
        self.assertEqual(parse_date("8/11/16").strftime("%d-%m-%Y"), "11-08-2016")
        self.assertEqual(parse_date("2024-06-08").strftime("%d-%m-%Y"), "08-06-2024")

    def test_clean_artists(self):
        res = clean_artists_string("Gigantour 3: Megadeth, In Flames, Children of Bodom")
        self.assertEqual(res, ["Megadeth", "In Flames", "Children of Bodom"])

    def test_gap_analysis(self):
        csv_recs = [{
            "id": "csv_1",
            "date": "29-07-2003",
            "raw_date": "7/29/2003",
            "primary_artist": "Iron Maiden",
            "artists": ["Iron Maiden", "Dio", "Motorhead"],
            "venue": "Merriweather",
            "year": 2003
        }]
        user_sl = [{
            "id": "sl_1",
            "eventDate": "29-07-2003",
            "artist": {"name": "Iron Maiden"},
            "venue": {"name": "Merriweather Post Pavilion", "city": {"name": "Columbia"}},
            "sets": {"set": [{"song": [{"name": "The Trooper"}]}]}
        }]
        reconciled = reconcile_history(csv_recs, user_sl)
        self.assertEqual(reconciled["matched_count"], 1)
        self.assertEqual(reconciled["coverage_percentage"], 100.0)
        self.assertEqual(len(reconciled["csv_only"]), 0)

if __name__ == "__main__":
    unittest.main()
