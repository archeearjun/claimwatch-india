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

    def test_target_selector_prefers_future_target_over_baseline(self):
        promise = {
            "year": 2019,
            "exact_text": (
                "We have achieved 76.87 GW of renewable capacity as on 2019. "
                "We will achieve 175 GW by 2022."
            ),
            "numbers": ["76.87 GW", "175 GW"],
            "deadline_hints": ["by 2022"],
        }
        target = evidence.promise_target_quantity(promise)
        self.assertEqual(target["kind"], "power_mw")
        self.assertEqual(target["value"], 175000.0)

    def test_duration_is_not_selected_as_outcome_target(self):
        promise = {
            "year": 2019,
            "exact_text": "In the next 5 years we will create 200 new schools.",
            "numbers": ["5 years", "200 schools"],
            "deadline_hints": ["next 5 years"],
        }
        target = evidence.promise_target_quantity(promise)
        self.assertEqual(target["value"], 200.0)

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

    def test_structured_numeric_claim_support(self):
        claim = "The growth rate was 7.8 percent in 2026."
        rows = [{
            "id": "gdp-1",
            "tier": "primary",
            "source": "Statistics Department",
            "source_url": "https://stats.example.gov.in/gdp",
            "url": "https://stats.example.gov.in/gdp",
            "title": "GDP growth rate in 2026",
            "snippet": "Official estimates show the GDP growth rate was 7.8 percent in 2026.",
            "relevance": 0.8,
            "matched_terms": ["growth", "rate", "2026"],
            "shared_numbers": ["7.8", "2026"],
        }]
        signal = evidence.structured_claim_numeric_signal(claim, rows)
        self.assertEqual(signal["verdict"], "supported")
        self.assertTrue(signal["publishable_verdict"])

    def test_structured_numeric_claim_contradiction(self):
        claim = "The growth rate was 8.5 percent in 2026."
        rows = [{
            "id": "gdp-2",
            "tier": "primary",
            "source": "Statistics Department",
            "source_url": "https://stats.example.gov.in/gdp",
            "url": "https://stats.example.gov.in/gdp",
            "title": "GDP growth in 2026",
            "snippet": "Official estimates show growth of 7.8 percent in 2026.",
            "relevance": 0.8,
            "matched_terms": ["growth", "rate", "2026"],
            "shared_numbers": ["2026"],
        }]
        signal = evidence.structured_claim_numeric_signal(claim, rows)
        self.assertEqual(signal["verdict"], "contradicted")
        self.assertTrue(signal["publishable_verdict"])

    def test_compound_lakh_crore_is_parsed_as_currency(self):
        rows = evidence.parse_quantity_mentions(
            "About 1 lakh crore rupees are being spent on this scheme."
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "currency_rupees")
        self.assertEqual(rows[0]["value"], 1_000_000_000_000.0)

    def test_disbursement_does_not_contradict_total_spending(self):
        claim = "About 1 lakh crore rupees are being spent on this scheme."
        rows = [{
            "id": "finance-1",
            "tier": "primary",
            "source": "PIB",
            "source_url": "https://www.pib.gov.in",
            "url": "https://www.pib.gov.in",
            "title": (
                "Prime Minister disburses ₹2,400 crore under the employment scheme"
            ),
            "snippet": "",
            "relevance": 0.9,
            "matched_terms": ["employment", "scheme", "crore"],
            "shared_numbers": [],
        }]
        signal = evidence.structured_claim_numeric_signal(claim, rows)
        self.assertIsNone(signal)

    def test_rozgar_mela_numeric_claim_support(self):
        claim = (
            "Today, more than 51,000 youth across the nation are receiving "
            "appointment letters for government service."
        )
        rows = [{
            "id": "rozgar-1",
            "tier": "primary",
            "source": "PIB",
            "source_url": "https://www.pib.gov.in",
            "url": "https://www.pib.gov.in",
            "title": (
                "Under 20th Rozgar Mela, PM to distribute more than 51,000 "
                "appointment letters to the newly appointed youth in Government on 19 September"
            ),
            "snippet": "",
            "relevance": 0.535,
            "matched_terms": ["appointment", "letters", "than", "youth"],
            "shared_numbers": ["51000"],
        }]
        signal = evidence.structured_claim_numeric_signal(claim, rows)
        self.assertEqual(signal["verdict"], "supported")
        self.assertTrue(signal["publishable_verdict"])


    def test_current_generated_rozgar_packet_replays_to_support(self):
        feed_path = Path(__file__).resolve().parents[1] / "data" / "evidence" / "latest.json"
        payload = __import__("json").loads(feed_path.read_text(encoding="utf-8"))
        packet = next(
            item for item in payload.get("claim_packets", [])
            if "51,000 youth" in (item.get("claim") or "")
        )

        claim_quantities = evidence.parse_quantity_mentions(packet["claim"])
        self.assertEqual(len(claim_quantities), 1)
        self.assertEqual(claim_quantities[0]["value"], 51000.0)

        candidates = [
            row for row in packet.get("evidence", [])
            if row.get("tier") == "primary"
            and evidence.is_verified_primary(row)
            and float(row.get("relevance") or 0) >= 0.30
            and len(row.get("matched_terms") or []) >= 3
        ]
        self.assertTrue(candidates)

        row = candidates[0]
        evidence_text = " ".join([
            row.get("title") or "",
            row.get("snippet") or "",
        ])
        observed = evidence.unique_quantities([
            q for q in evidence.parse_quantity_mentions(evidence_text)
            if q["kind"] == claim_quantities[0]["kind"]
        ])
        exact = [
            q for q in observed
            if evidence.numeric_relation_holds(
                "eq",
                claim_quantities[0]["value"],
                q["value"],
            )
        ]
        self.assertEqual(len(exact), 1)

        signal = evidence.structured_claim_numeric_signal(
            packet["claim"],
            packet.get("evidence", []),
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal["verdict"], "supported")


if __name__ == "__main__":
    unittest.main()
