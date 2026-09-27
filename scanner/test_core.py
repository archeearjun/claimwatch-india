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

    def test_pdf_fi_ligature_and_nav_numbers_are_cleaned(self):
        cleaned = manifestos.normalize_text(
            "Road Connectivity 13 14 15 16 We will improve \x00nancial access in the next \x00ve years."
        )
        self.assertIn("financial access", cleaned)
        self.assertIn("five years", cleaned)
        self.assertNotIn("13 14 15 16", cleaned)

    def test_navigation_numbers_do_not_create_measurable_target(self):
        cleaned = manifestos.normalize_text(
            "Road Connectivity 13 14 15 16 We will complete the dedicated freight corridor project."
        )
        self.assertEqual(manifestos.extract_numbers(cleaned), [])

    def test_comma_grouped_target_stays_one_number(self):
        self.assertEqual(
            manifestos.extract_numbers("We will create 10,000 FPOs by 2022."),
            ["10,000 FPOs"],
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

    def test_not_only_is_not_a_statistical_comparison(self):
        rows = scan.candidate_claims(
            "Behind this achievement is not only your hard work but also your family's support."
        )
        self.assertEqual(rows, [])

    def test_embedded_editor_markup_is_not_a_claim(self):
        rows = scan.candidate_claims(
            'News Updates <span data-mce-type="bookmark">65279</span> PM remarks 19 Sep 2026.'
        )
        self.assertEqual(rows, [])


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

    def test_claim_object_is_normalized(self):
        row = evidence.normalize_claim({
            "text": "More than 51,000 youth received appointment letters.",
            "reasons": ["number"],
            "numbers": ["51,000"],
        })
        self.assertEqual(row["text"], "More than 51,000 youth received appointment letters.")
        self.assertEqual(row["numbers"], ["51,000"])

    def test_claim_maker_source_is_not_independent_primary(self):
        row = {
            "tier": "primary",
            "source": "PM India",
            "source_url": "https://www.pmindia.gov.in/en/news_updates/example/",
            "url": "https://news.google.com/rss/articles/example",
        }
        self.assertFalse(evidence.is_verified_primary(row))

    def test_comma_grouped_number_is_one_token(self):
        self.assertEqual(
            evidence.numeric_tokens("More than 51,000 appointment letters"),
            {"51000"},
        )

    def test_non_official_result_cannot_inherit_primary_tier(self):
        row = {
            "tier": "primary",
            "source": "Reader's Digest",
            "source_url": "https://www.rd.com/article/example/",
            "url": "https://www.rd.com/article/example/",
        }
        self.assertFalse(evidence.is_verified_primary(row))

    def test_five_year_promise_derives_deadline(self):
        promise = {
            "year": 2014,
            "exact_text": "We will build 100 houses in the district in the next five years.",
            "deadline_hints": ["next five years"],
        }
        self.assertEqual(evidence.promise_deadline_year(promise), 2019)

    def test_structured_proof_can_prove_deadline_miss(self):
        packet = {
            "year": 2014,
            "exact_text": "We will build 100 houses in the district in the next five years.",
            "anchor": "We will build 100 houses in the district",
            "numbers": ["100 houses"],
            "deadline_hints": ["next five years"],
            "measurable": True,
            "evidence": [{
                "id": "official-1",
                "tier": "primary",
                "source": "District Department",
                "source_url": "https://housing.example.gov.in/report",
                "url": "https://housing.example.gov.in/report",
                "title": "District housing progress",
                "snippet": "As of 2020, 80 houses have been constructed in the district.",
                "published_at": "2020-05-01T00:00:00+00:00",
                "relevance": 0.8,
                "matched_terms": ["district", "houses"],
                "shared_numbers": [],
            }],
        }
        packet["evidence"] = evidence.sanitize_promise_evidence(
            packet, packet["evidence"]
        )
        proof = evidence.structured_promise_proof(packet)
        self.assertEqual(proof["status"], "proven_unfulfilled_by_deadline")
        self.assertEqual(proof["proof"]["target"]["value"], 100.0)
        self.assertEqual(proof["proof"]["observed"]["value"], 80.0)

    def test_structured_proof_can_prove_fulfilled_by_deadline(self):
        packet = {
            "year": 2014,
            "exact_text": "We will build 100 houses in the district in the next five years.",
            "anchor": "We will build 100 houses in the district",
            "numbers": ["100 houses"],
            "deadline_hints": ["next five years"],
            "measurable": True,
            "evidence": [{
                "id": "official-2",
                "tier": "primary",
                "source": "District Department",
                "source_url": "https://housing.example.gov.in/report",
                "url": "https://housing.example.gov.in/report",
                "title": "District housing target achieved",
                "snippet": "In 2019, 100 houses were completed in the district.",
                "published_at": "2019-12-01T00:00:00+00:00",
                "relevance": 0.8,
                "matched_terms": ["district", "houses"],
                "shared_numbers": ["100"],
            }],
        }
        packet["evidence"] = evidence.sanitize_promise_evidence(
            packet, packet["evidence"]
        )
        proof = evidence.structured_promise_proof(packet)
        self.assertEqual(proof["status"], "fulfilled_by_deadline")


if __name__ == "__main__":
    unittest.main()
