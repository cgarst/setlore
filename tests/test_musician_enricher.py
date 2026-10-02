import unittest
from django.test import TestCase
from apps.catalog.models import Artist, MusicianTenure
from src.musician_enricher import MusicianEnricher
from src.musician_tracker import get_effective_band_tenures, analyze_musicians_live, consolidate_musician_bands

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

    def test_is_past_or_temporary_detection(self):
        # Disambiguation keywords
        self.assertTrue(self.enricher.is_past_or_temporary(disambiguation="US singer, briefly a member of Dream Theater"))
        self.assertTrue(self.enricher.is_past_or_temporary(disambiguation="heavy metal vocalist, ex‐Dream Theater"))
        self.assertTrue(self.enricher.is_past_or_temporary(disambiguation="former bassist of Megadeth"))
        self.assertTrue(self.enricher.is_past_or_temporary(disambiguation="past member of Yes"))
        
        # Attribute / relation comments
        self.assertTrue(self.enricher.is_past_or_temporary(attrs=["guest", "guitar"]))
        self.assertTrue(self.enricher.is_past_or_temporary(attrs=["touring"]))
        self.assertTrue(self.enricher.is_past_or_temporary(rel_comment="temporary fill-in drummer"))

        # Confirmed current/standard member
        self.assertFalse(self.enricher.is_past_or_temporary(disambiguation="US keyboardist and composer", attrs=["original"]))
        self.assertFalse(self.enricher.is_past_or_temporary(disambiguation="American progressive metal guitarist"))

    def test_dream_theater_brief_and_present_members(self):
        mock_dt_data = {
            "relations": [
                # Steve Stone: undated, briefly a member in disambiguation -> ended=True, bounded to 1900-1900
                {
                    "type": "member of band",
                    "artist": {
                        "name": "Steve Stone",
                        "disambiguation": "US singer, briefly a member of Dream Theater"
                    },
                    "begin": None,
                    "end": None,
                    "ended": False,
                    "attributes": []
                },
                # Jordan Rudess: begin 1999 to present -> start 1999, end None
                {
                    "type": "member of band",
                    "artist": {
                        "name": "Jordan Rudess",
                        "disambiguation": "US keyboardist and composer"
                    },
                    "begin": "1999",
                    "end": None,
                    "ended": False,
                    "attributes": ["keyboard"]
                },
                # Chris Collins: ended=True with explicit dates
                {
                    "type": "member of band",
                    "artist": {
                        "name": "Chris Collins",
                        "disambiguation": "heavy metal vocalist, ex‐Dream Theater"
                    },
                    "begin": "1987",
                    "end": "1987",
                    "ended": True,
                    "attributes": ["lead vocals"]
                },
                # Former member with ended=True but no end year specified -> bounded to start year
                {
                    "type": "member of band",
                    "artist": {
                        "name": "Old Singer",
                        "disambiguation": "former vocalist"
                    },
                    "begin": "1988",
                    "end": None,
                    "ended": True,
                    "attributes": ["lead vocals"]
                }
            ]
        }
        parsed = {p["musician"]: p for p in self.enricher.parse_member_tenures(mock_dt_data)}

        # Steve Stone must NOT be active to present
        self.assertIn("Steve Stone", parsed)
        self.assertEqual(parsed["Steve Stone"]["start"], 1900)
        self.assertEqual(parsed["Steve Stone"]["end"], 1900)

        # Jordan Rudess IS active to present (from 1999 to present)
        self.assertIn("Jordan Rudess", parsed)
        self.assertEqual(parsed["Jordan Rudess"]["start"], 1999)
        self.assertIsNone(parsed["Jordan Rudess"]["end"])

        # Chris Collins is 1987-1987
        self.assertIn("Chris Collins", parsed)
        self.assertEqual(parsed["Chris Collins"]["start"], 1987)
        self.assertEqual(parsed["Chris Collins"]["end"], 1987)

        # Old Singer is capped at 1988-1988
        self.assertIn("Old Singer", parsed)
        self.assertEqual(parsed["Old Singer"]["start"], 1988)
        self.assertEqual(parsed["Old Singer"]["end"], 1988)

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

    def test_consolidate_musician_bands_stale_cache(self):
        stale_data = {
            "top_musicians": [
                {
                    "musician": "Paul Waggoner",
                    "role": "Lead Guitar / Backing Vocals",
                    "instrument": "Guitar",
                    "total_shows": 5,
                    "unique_bands_count": 2,
                    "bands": {
                        "Between The Buried And Me": 1,
                        "Between the Buried and Me": 4
                    },
                    "shows": []
                }
            ],
            "supergroup_musicians": [
                {
                    "musician": "Paul Waggoner",
                    "unique_bands_count": 2,
                    "total_shows": 5,
                    "bands": {
                        "Between The Buried And Me": 1,
                        "Between the Buried and Me": 4
                    }
                }
            ],
            "by_instrument": {
                "Guitar": [
                    {
                        "musician": "Paul Waggoner",
                        "unique_bands_count": 2,
                        "total_shows": 5,
                        "bands": {
                            "Between The Buried And Me": 1,
                            "Between the Buried and Me": 4
                        }
                    }
                ]
            }
        }
        cleaned = consolidate_musician_bands(stale_data)
        m = cleaned["top_musicians"][0]
        self.assertEqual(m["unique_bands_count"], 1)
        self.assertEqual(len(m["bands"]), 1)
        self.assertEqual(list(m["bands"].values())[0], 5)
        # Because unique_bands_count is 1, he should no longer be in supergroup_musicians
        self.assertEqual(len(cleaned["supergroup_musicians"]), 0)
