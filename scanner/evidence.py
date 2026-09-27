import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY_PATH = ROOT / "data" / "discovery" / "latest.json"
PROMISES_PATH = ROOT / "data" / "promises" / "candidates.json"
OUT_PATH = ROOT / "data" / "evidence" / "latest.json"

TIMEOUT = 18
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 ClaimWatchIndia/0.5 (+https://github.com/archeearjun/claimwatch-india)"
})

STOPWORDS = {
    "the","a","an","and","or","of","to","in","for","on","with","by","from","as","at","that","this",
    "is","are","be","been","being","we","our","will","shall","would","can","could","may","more","all",
    "its","their","they","it","into","through","across","over","under","such","these","those","also",
    "prime","minister","narendra","modi","india","indian","government","bjp","bharat","people","country",
    "said","says","new","today","years","year"
}

PREFERRED_OFFICIAL_SCOPE = (
    '(site:pib.gov.in OR site:pmindia.gov.in OR site:rbi.org.in '
    'OR site:mospi.gov.in OR site:cag.gov.in OR site:indiabudget.gov.in)'
)
BROAD_OFFICIAL_SCOPE = "site:gov.in"

GENERIC_TITLES = {
    "home","homepage","student workbook","annual report","index","document",
    "download","press release","new applications"
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def stable_id(*parts):
    return hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()[:18]

def clean_html(value):
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()

def tokens(text):
    return [
        t for t in re.findall(r"[^\W\d_][\w-]{1,}", (text or "").lower(), flags=re.UNICODE)
        if t not in STOPWORDS and len(t) >= 2
    ]

def keywords(text, limit=8):
    return [word for word, _ in Counter(tokens(text)).most_common(limit)]

def numeric_tokens(text):
    return set(re.findall(r"\b\d+(?:\.\d+)?\b", text or ""))

def jaccard(a, b):
    aa, bb = set(a), set(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)

def parse_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return value

def google_news(query, scope, tier, limit=8):
    params = {
        "q": query,
        "hl": "en-IN",
        "gl": "IN",
        "ceid": "IN:en",
    }
    url = "https://news.google.com/rss/search?" + urlencode(params)
    response = SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    rows = []
    for entry in feed.entries[:limit]:
        source = "Google News"
        if entry.get("source") and entry.source.get("title"):
            source = entry.source.get("title")
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", ""))
        rows.append({
            "id": stable_id(scope, entry.get("link"), title),
            "scope": scope,
            "tier": tier,
            "source": source,
            "title": title,
            "url": entry.get("link"),
            "published_at": parse_date(entry.get("published")),
            "snippet": summary[:320],
        })
    return rows

def factcheck_search(query):
    key = os.getenv("FACTCHECK_API_KEY", "").strip()
    if not key:
        return []

    params = {
        "query": query[:500],
        "languageCode": "en",
        "pageSize": 10,
        "key": key,
    }
    response = SESSION.get(
        "https://factchecktools.googleapis.com/v1alpha1/claims:search",
        params=params,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    rows = []
    for claim in response.json().get("claims", []):
        for review in claim.get("claimReview", []) or []:
            publisher = review.get("publisher", {}) or {}
            rows.append({
                "id": stable_id("factcheck", review.get("url"), claim.get("text")),
                "tier": "fact_check",
                "source": publisher.get("name") or publisher.get("site") or "Fact check",
                "site": publisher.get("site"),
                "claim_text": (claim.get("text") or "")[:360],
                "claimant": claim.get("claimant"),
                "claim_date": claim.get("claimDate"),
                "title": review.get("title"),
                "url": review.get("url"),
                "review_date": review.get("reviewDate"),
                "rating": review.get("textualRating"),
            })
    return rows[:10]

def rank_evidence(query_text, rows, limit=8):
    query_terms = keywords(query_text, limit=12)
    query_set = set(query_terms)
    query_numbers = numeric_tokens(query_text)
    ranked = []

    for row in rows:
        evidence_text = " ".join([
            row.get("title") or "",
            row.get("snippet") or "",
            row.get("claim_text") or "",
        ])
        evidence_terms = keywords(evidence_text, limit=16)
        evidence_set = set(evidence_terms)
        matched_terms = sorted(query_set & evidence_set)
        shared_numbers = sorted(query_numbers & numeric_tokens(evidence_text))
        lexical = jaccard(query_terms, evidence_terms)

        # Being returned by an official-domain query is not enough.
        # The result must actually overlap with the subject of the claim.
        if len(matched_terms) < 2 and not shared_numbers:
            continue

        generic_title = clean_html(row.get("title", "")).lower().split(" - ")[0].strip()
        if generic_title in GENERIC_TITLES and len(matched_terms) < 2 and not shared_numbers:
            continue

        relevance = lexical
        relevance += min(0.18, len(matched_terms) * 0.035)
        if shared_numbers:
            relevance += min(0.22, len(shared_numbers) * 0.11)
        if row.get("tier") == "primary":
            relevance += 0.05

        if relevance < 0.13:
            continue

        enriched = dict(row)
        enriched["lexical_overlap"] = round(lexical, 3)
        enriched["relevance"] = round(min(relevance, 1.0), 3)
        enriched["matched_terms"] = matched_terms[:8]
        enriched["shared_numbers"] = shared_numbers
        ranked.append(enriched)

    seen_titles = set()
    deduped = []
    for row in sorted(
        ranked,
        key=lambda x: (-x["relevance"], x.get("published_at") or "")
    ):
        key = re.sub(r"\W+", " ", (row.get("title") or "").lower()).strip()[:140]
        if key and key in seen_titles:
            continue
        if key:
            seen_titles.add(key)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped

def query_from_text(text, fallback_terms=None):
    terms = keywords(text, limit=6)
    if len(terms) < 3 and fallback_terms:
        for term in fallback_terms:
            if term not in terms:
                terms.append(term)
            if len(terms) >= 6:
                break
    nums = list(numeric_tokens(text))[:2]
    return " ".join(nums + terms)[:240].strip()

def retrieve_evidence(query, query_text):
    collected = []

    preferred = google_news(
        f"{query} {PREFERRED_OFFICIAL_SCOPE}",
        "preferred_official_search",
        "primary",
        limit=8,
    )
    preferred_ranked = rank_evidence(query_text, preferred, limit=6)
    collected.extend(preferred_ranked)

    if len(preferred_ranked) < 2:
        broad = google_news(
            f"{query} {BROAD_OFFICIAL_SCOPE}",
            "broad_official_search",
            "primary",
            limit=8,
        )
        collected.extend(rank_evidence(query_text, broad, limit=5))

    news = google_news(query, "news_search", "secondary", limit=8)
    collected.extend(rank_evidence(query_text, news, limit=6))

    return rank_evidence(query_text, collected, limit=8)

def promise_matches(claim_text, promises, limit=4):
    claim_terms = keywords(claim_text, limit=12)
    matches = []
    for promise in promises:
        promise_terms = promise.get("keywords", [])
        score = jaccard(claim_terms, promise_terms)
        overlap = set(claim_terms) & set(promise_terms)
        if score < 0.14 or len(overlap) < 2:
            continue
        matches.append({
            "id": promise["id"],
            "year": promise["year"],
            "category": promise["category"],
            "page": promise["page"],
            "anchor": promise.get("anchor"),
            "source_url": promise.get("source_url"),
            "similarity": round(score, 3),
            "matched_terms": sorted(overlap)[:6],
        })
    return sorted(
        matches,
        key=lambda x: (-x["similarity"], -len(x["matched_terms"]))
    )[:limit]

def strict_signal(text, evidence, factchecks):
    primary = [
        e for e in evidence
        if e.get("tier") == "primary"
        and e.get("relevance", 0) >= 0.22
        and len(e.get("matched_terms", [])) >= 2
    ]
    shared_numeric = [
        e for e in primary
        if e.get("shared_numbers") and e.get("lexical_overlap", 0) >= 0.12
    ]

    if shared_numeric:
        return {
            "level": "structured_support_candidate",
            "label": "Relevant primary evidence shares claim terms and numeric values",
            "publishable_verdict": False,
        }
    if factchecks:
        return {
            "level": "prior_fact_checks_found",
            "label": "Prior fact-check reviews found; ratings remain attributed",
            "publishable_verdict": False,
        }
    if primary:
        return {
            "level": "primary_evidence_found",
            "label": "Relevant primary evidence candidate found",
            "publishable_verdict": False,
        }
    secondary = [
        e for e in evidence
        if e.get("tier") == "secondary" and e.get("relevance", 0) >= 0.18
    ]
    if secondary:
        return {
            "level": "reporting_found",
            "label": "Relevant reporting found; primary evidence still needed",
            "publishable_verdict": False,
        }
    return {
        "level": "needs_research",
        "label": "No sufficiently relevant evidence candidate found automatically",
        "publishable_verdict": False,
    }

def build_claim_packets(discovery, promises):
    candidates = []
    for item in discovery.get("items", []):
        if item.get("kind") not in {"official_speech_or_video", "youtube_video"}:
            continue
        for idx, claim in enumerate(item.get("candidate_claims", []) or []):
            candidates.append((item, idx, claim))

    candidates.sort(
        key=lambda row: row[0].get("published_at") or "",
        reverse=True
    )
    packets, errors = [], []

    for item, idx, claim in candidates[:14]:
        query = query_from_text(claim)
        if not query:
            continue

        try:
            evidence = retrieve_evidence(query, claim)
        except Exception as exc:
            evidence = []
            errors.append({
                "stage": "claim_evidence_search",
                "claim": claim[:120],
                "error": type(exc).__name__,
            })

        factchecks = []
        try:
            factchecks = factcheck_search(claim)
        except Exception as exc:
            errors.append({
                "stage": "factcheck_search",
                "claim": claim[:120],
                "error": type(exc).__name__,
            })

        packets.append({
            "id": stable_id("claim_packet", item.get("id"), idx, claim),
            "claim": claim,
            "claim_source": {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "title": item.get("title"),
                "source": item.get("source"),
                "channel_title": item.get("channel_title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
            },
            "query": query,
            "numbers": sorted(numeric_tokens(claim)),
            "promise_matches": promise_matches(claim, promises),
            "evidence": evidence,
            "fact_checks": factchecks,
            "signal": strict_signal(claim, evidence, factchecks),
            "verdict": "pending",
        })

    return packets, errors

def build_promise_packets(promises):
    priority = sorted(
        promises,
        key=lambda p: (
            not p.get("measurable"),
            not bool(p.get("deadline_hints")),
            -int(p.get("candidate_score", 0)),
            -int(p.get("year", 0)),
        ),
    )

    packets, errors = [], []
    for promise in priority[:30]:
        query_seed = " ".join(
            (promise.get("numbers") or [])
            + (promise.get("deadline_hints") or [])
            + (promise.get("keywords") or [])
        )
        query = query_from_text(query_seed, promise.get("keywords"))
        if not query:
            continue

        pseudo_text = " ".join([
            promise.get("anchor") or "",
            " ".join(promise.get("keywords") or []),
            " ".join(promise.get("numbers") or []),
            " ".join(promise.get("deadline_hints") or []),
        ])

        try:
            evidence = retrieve_evidence(query, pseudo_text)
        except Exception as exc:
            evidence = []
            errors.append({
                "stage": "promise_evidence_search",
                "promise_id": promise["id"],
                "error": type(exc).__name__,
            })

        primary_count = sum(
            1 for e in evidence
            if e.get("tier") == "primary"
            and e.get("relevance", 0) >= 0.20
            and len(e.get("matched_terms", [])) >= 2
        )
        secondary_count = sum(
            1 for e in evidence
            if e.get("tier") == "secondary"
            and e.get("relevance", 0) >= 0.18
        )

        if primary_count and secondary_count:
            signal = "current_primary_and_reporting_found"
        elif primary_count:
            signal = "current_primary_evidence_found"
        elif secondary_count:
            signal = "current_reporting_found"
        else:
            signal = "needs_research"

        packets.append({
            "id": stable_id("promise_packet", promise["id"], query),
            "promise_id": promise["id"],
            "year": promise["year"],
            "category": promise["category"],
            "page": promise["page"],
            "anchor": promise.get("anchor"),
            "keywords": promise.get("keywords"),
            "numbers": promise.get("numbers"),
            "deadline_hints": promise.get("deadline_hints"),
            "measurable": promise.get("measurable"),
            "source_url": promise.get("source_url"),
            "pdf_url": promise.get("pdf_url"),
            "related_promises": promise.get("related_promises", []),
            "query": query,
            "evidence": evidence,
            "implementation_signal": signal,
            "status": "pending",
            "status_reason": (
                "Retrieval can identify current evidence, but fulfilment requires "
                "the original target and current metric to be directly comparable."
            ),
        })

    return packets, errors

def main():
    discovery = (
        json.loads(DISCOVERY_PATH.read_text(encoding="utf-8"))
        if DISCOVERY_PATH.exists()
        else {"items": []}
    )
    promise_data = (
        json.loads(PROMISES_PATH.read_text(encoding="utf-8"))
        if PROMISES_PATH.exists()
        else {"promises": []}
    )
    promises = promise_data.get("promises", [])

    claim_packets, claim_errors = build_claim_packets(discovery, promises)
    promise_packets, promise_errors = build_promise_packets(promises)

    all_evidence = [
        e
        for packet in claim_packets + promise_packets
        for e in packet.get("evidence", [])
    ]
    fact_checks = [
        f
        for packet in claim_packets
        for f in packet.get("fact_checks", [])
    ]

    payload = {
        "generated_at": now_iso(),
        "capabilities": {
            "official_evidence_search": True,
            "news_context_search": True,
            "fact_check_api_configured": bool(
                os.getenv("FACTCHECK_API_KEY", "").strip()
            ),
            "manifesto_memory_loaded": bool(promises),
        },
        "summary": {
            "claim_packets": len(claim_packets),
            "promise_packets": len(promise_packets),
            "primary_candidates": sum(
                1 for e in all_evidence if e.get("tier") == "primary"
            ),
            "secondary_candidates": sum(
                1 for e in all_evidence if e.get("tier") == "secondary"
            ),
            "prior_fact_checks": len(fact_checks),
            "publishable_auto_verdicts": 0,
        },
        "claim_packets": claim_packets,
        "promise_packets": promise_packets,
        "errors": claim_errors + promise_errors,
        "notes": [
            "Evidence candidates are not verdicts.",
            "Official-search results are discarded unless they share substantive terms or numeric values with the claim.",
            "Google Fact Check Tools matches are attributed to their publishers and are not adopted as ClaimWatch verdicts automatically.",
            "A contradiction is not treated as proof of deliberate deception.",
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUT_PATH}: {len(claim_packets)} claim packets, "
        f"{len(promise_packets)} promise packets, "
        f"{len(payload['errors'])} errors."
    )

if __name__ == "__main__":
    main()
