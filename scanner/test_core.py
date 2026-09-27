import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evidence
import manifestos
import scan


class ManifestoExtractionTests(unittest.TestCase):
    def test_calendar_year_is_not_a_numeric_target(self):
        text = "In 2012 the programme began. We will build 100 schools by 2028."
        self.assertEqual(manifestos.extract_numbers(text), ["100 schools"])
        self.assertEqual(manifestos.extract_deadlines(text), ["by 2028"])
        self.assertEqual(manifestos.extract_years(text), ["2012", "2028"])

    def test_bare_historical_year_is_not_a_deadline(self):
        self.assertEqual(
            manifestos.extract_deadlines("The Act was passed in 2012 and remains in force."),
            [],
        )


class DiscoveryExtractionTests(unittest.TestCase):
    def test_social_footer_is_not_a_claim(self):
        text = (
            "Follow us on Facebook https://facebook.com/BJP4India and Instagram "
            "https://instagram.com/bjp4india. "
            "Around 100 processing units are operating in the district."
        )
        rows = scan.candidate_claims(text)
        self.assertTrue(any("100 processing units" in row for row in rows))
        self.assertFalse(any("facebook" in row.lower() for row in rows))

    def test_duplicate_candidate_is_removed(self):
        text = (
            "The project created 100 jobs in 2026. "
            "The project created 100 jobs in 2026."
        )
        rows = scan.candidate_claims(text)
        self.assertEqual(len(rows), 1)


class EvidenceRankingTests(unittest.TestCase):
    def test_unrelated_official_result_is_dropped(self):
        claim = "Around 100 amla processing units are operating in Pratapgarh."
        rows = [{
            "tier": "primary",
            "source": "Official portal",
            "title": "Student Workbook",
            "snippet": "Mathematics workbook for school students",
            "url": "https://example.gov.in/workbook",
        }]
        self.assertEqual(evidence.rank_evidence(claim, rows), [])

    def test_relevant_result_is_retained(self):
        claim = "Around 100 amla processing units are operating in Pratapgarh."
        rows = [{
            "tier": "primary",
            "source": "Official portal",
            "title": "Pratapgarh amla processing units",
            "snippet": "The district has 100 amla processing units operating under the programme.",
            "url": "https://example.gov.in/amla",
        }]
        ranked = evidence.rank_evidence(claim, rows)
        self.assertEqual(len(ranked), 1)
        self.assertIn("100", ranked[0]["shared_numbers"])
        self.assertGreaterEqual(len(ranked[0]["matched_terms"]), 2)


if __name__ == "__main__":
    unittest.main()
