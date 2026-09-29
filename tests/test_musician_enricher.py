import unittest
from django.test import TestCase
from apps.catalog.models import Artist, MusicianTenure
from src.musician_enricher import MusicianEnricher
from src.musician_tracker import get_effective_band_tenures, analyze_musicians_live

class MusicianEnricherTests(TestCase):
    def setUp(self):
        self.enricher = MusicianEnricher()

    def test_classify_instrument(self):
        # Rhythm section
        self.assertEqual(self.enricher.classify_instrument(['drums (drum set)', 'percussion']), 'Drums')
        self.assertEqual(self.enricher.classify_instrument(['Chapman stick', 'electric bass guitar']), 'Bass')
        self.assertEqual(self.enricher.classify_instrument(['bass guitar']), 'Bass')

        # Guitar & Keyboards disambiguation (Jordan Rudess lap steel case)
        self.assertEqual(self.enricher.classify_instrument(['continuum', 'keyboard', 'lap steel guitar']), 'Keyboards')
        self.assertEqual(self.enricher.classify_instrument(['keyboard', 'synthesizer']), 'Keyboards')
        self.assertEqual(self.enricher.classify_instrument(['guitar', 'lead guitar']), 'Guitar')

        # Vocals
        self.assertEqual(self.enricher.classify_instrument(['lead vocals']), 'Vocals')

        # Multi-instrumentalist prioritizing primary instrument
        self.assertEqual(self.enricher.classify_instrument(['drums (drum set)', 'background vocals']), 'Drums')
        self.assertEqual(self.enricher.classify_instrument(['guitar', 'background vocals']), 'Guitar')

    def test_format_role(self):
        self.assertEqual(
            self.enricher.format_role(['drums (drum set)', 'background vocals', 'original']),
            'Drums / Backing Vocals'
        )
        self.assertEqual(
            self.enricher.format_role(['electric bass guitar', 'chapman stick']),
            'Bass / Chapman Stick'
        )
        self.assertEqual(
            self.enricher.format_role(['original']),
            'Musician'
        )

    def test_parse_member_tenures_merging(self):
        mock_mb_data = {
            "relations": [
                {
                    "type": "member of band",
                    "artist": {"name": "Mike Portnoy"},
                    "begin": "1985",
                    "end": "2010-09-08",
                    "ended": True,
                    "attributes": ["drums (drum set)", "background vocals"]
                },
                {
                    "type": "member of band",
                    "artist": {"name": "Mike Portnoy"},
                    "begin": "2023-10-25",
                    "end": None,
                    "ended": False,
                    "attributes": ["drums (drum set)"]
                },
                {
                    "type": "member of band",
                    "artist": {"name": "Mike Portnoy"},
                    "begin": "2023-10-25",
                    "end": None,
                    "ended": False,
                    "attributes": ["background vocals", "original"]
                }
            ]
        }
        parsed = self.enricher.parse_member_tenures(mock_mb_data)
        # Portnoy should have exactly 2 tenures, not 3 (attributes for 2023 merged!)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0]["musician"], "Mike Portnoy")
        self.assertEqual(parsed[0]["start"], 1985)
        self.assertEqual(parsed[0]["end"], 2010)

        self.assertEqual(parsed[1]["musician"], "Mike Portnoy")
        self.assertEqual(parsed[1]["start"], 2023)
        self.assertIsNone(parsed[1]["end"])
        self.assertIn("drums (drum set)", parsed[1]["attributes"])
        self.assertIn("background vocals", parsed[1]["attributes"])

    def test_deduplication_against_existing_db(self):
        artist = Artist.objects.create(name="Test Band", normalized_name="test band")
        # Existing tenure in DB
        MusicianTenure.objects.create(
            artist=artist,
            musician_name="John Doe",
            role="Guitar / Vocals",
            instrument="Guitar",
            start_year=1990,
            end_year=None
        )

        incoming = [
            # Exact duplicate of existing John Doe (should NOT be added)
            {"musician": "John Doe", "role": "Lead Guitar", "instrument": "Guitar", "start": 1990, "end": None, "attributes": ["guitar"]},
            # Close start year John Doe (e.g. MB says 1991, should match existing and NOT be added)
            {"musician": "John Doe", "role": "Guitar", "instrument": "Guitar", "start": 1991, "end": None, "attributes": ["guitar"]},
            # Brand new member (SHOULD be added)
            {"musician": "Jane Smith", "role": "Drums", "instrument": "Drums", "start": 1995, "end": 2005, "attributes": ["drums (drum set)"]}
        ]

        created, updated = self.enricher._sync_tenures_to_db(artist, incoming)
        self.assertEqual(created, 1)  # Only Jane Smith created
        self.assertEqual(MusicianTenure.objects.filter(artist=artist).count(), 2)

        # Running again with same data is 100% idempotent
        created_again, _ = self.enricher._sync_tenures_to_db(artist, incoming)
        self.assertEqual(created_again, 0)
        self.assertEqual(MusicianTenure.objects.filter(artist=artist).count(), 2)

    def test_effective_tenures_and_live_analytics(self):
        artist = Artist.objects.create(name="Test Prog Band", normalized_name="test prog band")
        MusicianTenure.objects.create(
            artist=artist,
            musician_name="Master Musician",
            role="Keyboards",
            instrument="Keyboards",
            start_year=2000,
            end_year=None
        )

        effective = get_effective_band_tenures()
        self.assertIn("test prog band", effective)
        self.assertEqual(len(effective["test prog band"]), 1)
        self.assertEqual(effective["test prog band"][0]["musician"], "Master Musician")

        # Test analyze_musicians_live with concert
        concert_recs = [{
            "id": "c1",
            "year": 2015,
            "display_date": "10/12/2015",
            "venue": "Rock Club",
            "artists": ["Test Prog Band"]
        }]
        res = analyze_musicians_live(concert_recs)
        self.assertEqual(res["total_musicians_tracked"], 1)
        self.assertEqual(res["top_musicians"][0]["musician"], "Master Musician")
        self.assertEqual(res["top_musicians"][0]["instrument"], "Keyboards")
        self.assertEqual(res["top_musicians"][0]["total_shows"], 1)

    def test_case_insensitive_band_names_musician_tracking(self):
        # Create artist and tenure for BTBAM
        artist = Artist.objects.create(name="Between the Buried and Me", normalized_name="between the buried and me")
        MusicianTenure.objects.create(
            artist=artist,
            musician_name="Tommy Giles Rogers",
            role="Lead Vocals / Keyboards",
            instrument="Vocals",
            start_year=2000,
            end_year=None
        )

        concert_recs = [
            {
                "id": "c1",
                "year": 2018,
                "display_date": "03/10/2018",
                "venue": "Venue A",
                "artists": ["Between The Buried And Me"]
            },
            {
                "id": "c2",
                "year": 2022,
                "display_date": "08/15/2022",
                "venue": "Venue B",
                "artists": ["Between the Buried and Me"]
            }
        ]

        res = analyze_musicians_live(concert_recs)
        self.assertEqual(res["total_musicians_tracked"], 1)
        m = res["top_musicians"][0]
        self.assertEqual(m["musician"], "Tommy Giles Rogers")
        self.assertEqual(m["total_shows"], 2)
        # Should count as 1 unique band, NOT 2
        self.assertEqual(m["unique_bands_count"], 1)
        self.assertEqual(len(m["bands"]), 1)
        # Verify supergroup/multi-band does not include him because he only has 1 unique band
        self.assertEqual(len(res["supergroup_musicians"]), 0)
