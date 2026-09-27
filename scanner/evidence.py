import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.robotparser import RobotFileParser

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
    "User-Agent": "Mozilla/5.0 ClaimWatchIndia/0.6 (+https://github.com/archeearjun/claimwatch-india)"
})

NLI_MODEL_NAME = os.getenv(
    "NLI_MODEL",
    "cross-encoder/nli-MiniLM2-L6-H768",
)
ENABLE_NLI = os.getenv("ENABLE_NLI", "1") != "0"
_NLI_BUNDLE = None
_NLI_ERROR = None

INDEPENDENT_OFFICIAL_HOSTS = {
    "pib.gov.in",
    "rbi.org.in",
    "mospi.gov.in",
    "cag.gov.in",
    "indiabudget.gov.in",
    "data.gov.in",
    "sansad.in",
    "parliamentofindia.nic.in",
    "indiacode.nic.in",
}
NON_VERIFYING_SOURCE_NAMES = {
    "pm india",
    "narendra modi",
    "bharatiya janata party",
    "bjp",
}

STOPWORDS = {
    "the","a","an","and","or","of","to","in","for","on","with","by","from","as","at","that","this",
    "is","are","be","been","being","we","our","will","shall","would","can","could","may","more","all",
    "its","their","they","it","into","through","across","over","under","such","these","those","also",
    "prime","minister","narendra","modi","india","indian","government","bjp","bharat","people","country",
    "said","says","new","today","years","year"
}

PREFERRED_OFFICIAL_SCOPE = (
    '(site:pib.gov.in OR site:rbi.org.in OR site:mospi.gov.in '
    'OR site:cag.gov.in OR site:indiabudget.gov.in OR site:data.gov.in '
    'OR site:sansad.in OR site:indiacode.nic.in)'
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
    values = re.findall(
        r"\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\b",
        text or "",
    )
    return {value.replace(",", "") for value in values}

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

def normalize_claim(raw):
    if isinstance(raw, str):
        return {
            "text": raw.strip(),
            "reasons": [],
            "numbers": sorted(numeric_tokens(raw)),
            "context_before": "",
        }
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
        return {
            "text": text,
            "reasons": list(raw.get("reasons") or []),
            "numbers": list(raw.get("numbers") or sorted(numeric_tokens(text))),
            "context_before": str(raw.get("context_before") or "").strip(),
        }
    return {"text": "", "reasons": [], "numbers": [], "context_before": ""}

def hostname(value):
    try:
        return urlparse(str(value or "")).hostname.lower().replace("www.", "", 1)
    except Exception:
        return ""

def is_independent_primary(row):
    source_name = str(row.get("source") or "").strip().lower()
    if source_name in NON_VERIFYING_SOURCE_NAMES:
        return False

    hosts = {
        hostname(row.get("source_url")),
        hostname(row.get("resolved_url")),
        hostname(row.get("url")),
    }
    hosts.discard("")

    for host in hosts:
        if host in {"pmindia.gov.in", "narendramodi.in", "bjp.org", "library.bjp.org"}:
            return False
        if any(host == official or host.endswith("." + official) for official in INDEPENDENT_OFFICIAL_HOSTS):
            return True
        if host.endswith(".gov.in") or host.endswith(".nic.in"):
            return True

    # Never trust the query that produced a result as evidence of provenance.
    # The publisher/resolved host itself must be an approved official domain.
    return False

def robots_allowed(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = SESSION.get(robots_url, timeout=7)
        if response.status_code >= 400:
            return True
        robot = RobotFileParser()
        robot.parse(response.text.splitlines())
        return robot.can_fetch(SESSION.headers.get("User-Agent", "*"), url)
    except Exception:
        return True

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
        source_url = ""
        if entry.get("source") and entry.source.get("href"):
            source_url = entry.source.get("href") or ""
        rows.append({
            "id": stable_id(scope, entry.get("link"), title),
            "scope": scope,
            "tier": tier,
            "source": source,
            "source_url": source_url,
            "title": title,
            "url": entry.get("link"),
            "published_at": parse_date(entry.get("published")),
            "snippet": summary[:320],
        })
    return rows

def bing_web(query, scope, tier, limit=8):
    params = {
        "q": query,
        "format": "rss",
        "count": str(limit),
    }
    response = SESSION.get(
        "https://www.bing.com/search",
        params=params,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    rows = []
    for entry in feed.entries[:limit]:
        url = entry.get("link") or ""
        title = clean_html(entry.get("title", ""))
        summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
        rows.append({
            "id": stable_id(scope, url, title),
            "scope": scope,
            "tier": tier,
            "source": hostname(url) or "Web",
            "source_url": url,
            "title": title,
            "url": url,
            "published_at": parse_date(entry.get("published")),
            "snippet": summary[:420],
            "retrieval": "search_result",
        })
    return rows

def best_page_excerpt(text, query_text):
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?।])\s+", text)
        if 35 <= len(part.strip()) <= 850
    ]
    if not sentences:
        return text[:420]

    query_terms = keywords(query_text, limit=12)
    query_numbers = numeric_tokens(query_text)

    def score(sentence):
        terms = keywords(sentence, limit=18)
        lexical = jaccard(query_terms, terms)
        shared = query_numbers & numeric_tokens(sentence)
        return lexical + min(0.4, len(shared) * 0.18)

    return max(sentences, key=score)[:520]

def hydrate_official_row(row, query_text):
    if row.get("tier") != "primary" or not is_independent_primary(row):
        return row

    url = row.get("source_url") or row.get("url")
    host = hostname(url)
    if not url or not (
        host.endswith(".gov.in")
        or host.endswith(".nic.in")
        or any(host == h or host.endswith("." + h) for h in INDEPENDENT_OFFICIAL_HOSTS)
    ):
        return row

    if not robots_allowed(url):
        return {**row, "retrieval": "robots_disallowed"}

    try:
        response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        resolved = response.url
        if response.status_code != 200:
            return {**row, "retrieval": f"http_{response.status_code}", "resolved_url": resolved}

        ctype = response.headers.get("content-type", "").lower()
        if "html" not in ctype:
            return {**row, "retrieval": "non_html", "resolved_url": resolved}

        html = response.text[:4_000_000]
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script","style","noscript","svg","nav","footer","header","form","aside"]):
            tag.decompose()

        blocks = []
        for selector in ("article", ".entry-content", ".content", ".content-area", "main", "body"):
            node = soup.select_one(selector)
            if not node:
                continue
            value = clean_html(node.get_text(" ", strip=True))
            if len(value) >= 150:
                blocks.append(value)

        if not blocks:
            return {**row, "retrieval": "no_page_text", "resolved_url": resolved}

        page_text = max(blocks, key=len)
        excerpt = best_page_excerpt(page_text, query_text)
        if not excerpt:
            return {**row, "retrieval": "no_relevant_excerpt", "resolved_url": resolved}

        return {
            **row,
            "url": resolved,
            "source_url": resolved,
            "resolved_url": resolved,
            "snippet": excerpt,
            "retrieval": "official_page_excerpt",
        }
    except Exception as exc:
        return {**row, "retrieval": f"fetch_error:{type(exc).__name__}"}

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
        if row.get("tier") == "primary" and not is_independent_primary(row):
            continue

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

def retrieve_evidence(query, query_text, hydrate_primary=False):
    collected = []

    # Direct-web RSS produces inspectable destination URLs. It is preferred for
    # automated truth checks because we can fetch the actual government page.
    try:
        direct = bing_web(
            f"{query} {PREFERRED_OFFICIAL_SCOPE}",
            "official_web_search",
            "primary",
            limit=8,
        )
        direct_ranked = rank_evidence(query_text, direct, limit=6)
        if hydrate_primary:
            direct_ranked = [
                hydrate_official_row(row, query_text)
                for row in direct_ranked[:4]
            ]
            direct_ranked = rank_evidence(query_text, direct_ranked, limit=6)
        collected.extend(direct_ranked)
    except Exception:
        direct_ranked = []

    preferred = google_news(
        f"{query} {PREFERRED_OFFICIAL_SCOPE}",
        "preferred_official_search",
        "primary",
        limit=8,
    )
    preferred_ranked = rank_evidence(query_text, preferred, limit=6)
    collected.extend(preferred_ranked)

    if len(direct_ranked) + len(preferred_ranked) < 2:
        broad = google_news(
            f"{query} {BROAD_OFFICIAL_SCOPE}",
            "broad_official_search",
            "primary",
            limit=8,
        )
        collected.extend(rank_evidence(query_text, broad, limit=5))

    news = google_news(query, "news_search", "secondary", limit=8)
    collected.extend(rank_evidence(query_text, news, limit=6))

    return rank_evidence(query_text, collected, limit=10)

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

def get_nli():
    global _NLI_BUNDLE, _NLI_ERROR
    if not ENABLE_NLI:
        return None
    if _NLI_BUNDLE is not None:
        return _NLI_BUNDLE
    if _NLI_ERROR is not None:
        return None

    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(NLI_MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(NLI_MODEL_NAME)
        model.eval()
        _NLI_BUNDLE = (tokenizer, model, torch)
        return _NLI_BUNDLE
    except Exception as exc:
        _NLI_ERROR = f"{type(exc).__name__}: {exc}"
        return None

def nli_scores(evidence_text, claim_text):
    bundle = get_nli()
    if not bundle:
        return None

    tokenizer, model, torch = bundle
    features = tokenizer(
        evidence_text,
        claim_text,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = model(**features).logits[0]
        probs = torch.softmax(logits, dim=-1).tolist()

    # cross-encoder/nli-MiniLM2-L6-H768 label order:
    # contradiction, entailment, neutral.
    return {
        "contradiction": float(probs[0]),
        "entailment": float(probs[1]),
        "neutral": float(probs[2]),
    }

def strict_signal(text, evidence, factchecks):
    primary = [
        e for e in evidence
        if e.get("tier") == "primary"
        and is_independent_primary(e)
        and e.get("relevance", 0) >= 0.22
        and len(e.get("matched_terms", [])) >= 2
    ]
    claim_numbers = numeric_tokens(text)

    best_support = None
    best_contradiction = None

    for row in primary[:6]:
        premise = " ".join([
            row.get("title") or "",
            row.get("snippet") or "",
            row.get("claim_text") or "",
        ]).strip()
        scores = nli_scores(premise, text)
        if not scores:
            continue

        row["nli"] = {k: round(v, 4) for k, v in scores.items()}
        shared_numbers = set(row.get("shared_numbers") or [])
        row_numbers = numeric_tokens(premise)
        numeric_support_ok = not claim_numbers or bool(claim_numbers & shared_numbers)
        numeric_conflict_possible = bool(claim_numbers and row_numbers and not shared_numbers)

        if (
            scores["entailment"] >= 0.92
            and row.get("relevance", 0) >= 0.28
            and len(row.get("matched_terms", [])) >= 3
            and numeric_support_ok
        ):
            if best_support is None or scores["entailment"] > best_support["nli"]["entailment"]:
                best_support = row

        if (
            scores["contradiction"] >= 0.97
            and row.get("relevance", 0) >= 0.30
            and len(row.get("matched_terms", [])) >= 3
            and numeric_conflict_possible
        ):
            if (
                best_contradiction is None
                or scores["contradiction"] > best_contradiction["nli"]["contradiction"]
            ):
                best_contradiction = row

    if best_support and best_contradiction:
        return {
            "level": "conflicting_primary_evidence",
            "label": "Conflicting independent primary evidence found",
            "publishable_verdict": False,
            "verdict": "insufficient",
            "reason": "Independent official evidence produced conflicting machine signals; human review is required.",
        }

    if best_support:
        return {
            "level": "automated_supported",
            "label": "Supported by independent primary evidence",
            "publishable_verdict": True,
            "verdict": "supported",
            "reason": "A highly relevant independent official source entails the claim under the strict automated threshold.",
            "evidence_id": best_support.get("id"),
            "assessment_mode": "strict_nli_primary_gate",
        }

    if best_contradiction:
        return {
            "level": "automated_contradicted",
            "label": "Contradicted by independent primary evidence",
            "publishable_verdict": True,
            "verdict": "contradicted",
            "reason": "A highly relevant independent official source contradicts the numerical claim under the strict automated threshold.",
            "evidence_id": best_contradiction.get("id"),
            "assessment_mode": "strict_nli_primary_gate",
        }

    shared_numeric = [
        e for e in primary
        if e.get("shared_numbers") and e.get("lexical_overlap", 0) >= 0.12
    ]
    if shared_numeric:
        return {
            "level": "structured_support_candidate",
            "label": "Relevant primary evidence shares claim terms and numeric values",
            "publishable_verdict": False,
            "verdict": "pending",
        }
    if factchecks:
        return {
            "level": "prior_fact_checks_found",
            "label": "Prior fact-check reviews found; ratings remain attributed",
            "publishable_verdict": False,
            "verdict": "pending",
        }
    if primary:
        return {
            "level": "primary_evidence_found",
            "label": "Relevant independent primary evidence candidate found",
            "publishable_verdict": False,
            "verdict": "pending",
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
            "verdict": "pending",
        }
    return {
        "level": "needs_research",
        "label": "No sufficiently relevant evidence candidate found automatically",
        "publishable_verdict": False,
        "verdict": "pending",
    }

def build_claim_packets(discovery, promises):
    candidates = []
    seen_claims = set()
    for item in discovery.get("items", []):
        # Scheduled truth checks require statement text, not video metadata.
        # PMIndia rows contain publisher speech text. YouTube descriptions stay
        # in discovery until matched to a publisher transcript or transcribed
        # locally through the browser workflow.
        if item.get("kind") != "official_speech_or_video":
            continue

        for idx, raw_claim in enumerate(item.get("candidate_claims", []) or []):
            normalized = normalize_claim(raw_claim)
            text_key = re.sub(r"\W+", " ", normalized["text"].lower()).strip()
            if not normalized["text"] or text_key in seen_claims:
                continue
            seen_claims.add(text_key)
            candidates.append((item, idx, normalized))

    candidates.sort(
        key=lambda row: row[0].get("published_at") or "",
        reverse=True
    )
    packets, errors = [], []

    for item, idx, claim_record in candidates[:14]:
        claim = claim_record["text"]
        context_before = claim_record.get("context_before", "")
        retrieval_text = " ".join(
            part for part in (context_before, claim) if part
        ).strip()
        query = query_from_text(retrieval_text)
        if not query:
            continue

        try:
            evidence = retrieve_evidence(query, retrieval_text, hydrate_primary=True)
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

        signal = strict_signal(claim, evidence, factchecks)

        packets.append({
            "id": stable_id("claim_packet", item.get("id"), idx, claim),
            "claim": claim,
            "extraction": {
                "reasons": claim_record.get("reasons", []),
                "numbers": claim_record.get("numbers", []),
                "context_before": context_before,
            },
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
            "signal": signal,
            "verdict": signal.get("verdict", "pending"),
            "human_reviewed": False,
        })

    return packets, errors

def select_promise_batch(promises, batch_size=30):
    if not promises:
        return []

    priority = sorted(
        promises,
        key=lambda p: (
            not p.get("measurable"),
            not bool(p.get("deadline_hints")),
            -int(p.get("candidate_score", 0)),
            -int(p.get("year", 0)),
            p.get("id", ""),
        ),
    )

    # Advance by one batch each UTC hour. 781 current candidates and a batch of
    # 30 are coprime, so repeated hourly scans eventually visit every candidate
    # rather than getting stuck in a subset.
    now = datetime.now(timezone.utc)
    epoch_hours = int(now.timestamp() // 3600)
    start = (epoch_hours * batch_size) % len(priority)
    return [
        priority[(start + offset) % len(priority)]
        for offset in range(min(batch_size, len(priority)))
    ]

def previous_promise_packets():
    if not OUT_PATH.exists():
        return {}
    try:
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        packet.get("promise_id"): packet
        for packet in payload.get("promise_packets", [])
        if packet.get("promise_id")
    }

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
            evidence = retrieve_evidence(query, pseudo_text, hydrate_primary=False)
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
            and is_independent_primary(e)
            and e.get("relevance", 0) >= 0.20
            and len(e.get("matched_terms", [])) >= 2
        )
        secondary_count = sum(
            1 for e in evidence
            if e.get("tier") == "secondary"
            and e.get("relevance", 0) >= 0.18
        )

        independent_primary = [
            e for e in evidence
            if e.get("tier") == "primary" and is_independent_primary(e)
        ]
        action_terms = re.compile(
            r"\b(?:launched|implemented|notified|approved|operationali[sz]ed|"
            r"rolled out|expanded|extended|completed|achieved|reached|introduced|"
            r"established|set up|sanctioned|allocated|started|commenced|covered|"
            r"provided|increased|created)\b",
            re.I,
        )
        completion_terms = re.compile(
            r"\b(?:completed|achieved|fully implemented|target achieved|"
            r"reached the target|operationali[sz]ed)\b",
            re.I,
        )
        promise_numbers = numeric_tokens(pseudo_text)
        action_evidence = []
        target_evidence = []

        for e in independent_primary:
            evidence_text = " ".join([
                e.get("title") or "",
                e.get("snippet") or "",
            ])
            if action_terms.search(evidence_text):
                action_evidence.append(e)
                evidence_numbers = numeric_tokens(evidence_text)
                if (
                    promise.get("measurable")
                    and promise_numbers
                    and promise_numbers & evidence_numbers
                    and completion_terms.search(evidence_text)
                ):
                    target_evidence.append(e)

        if target_evidence:
            signal = "target_evidence_detected"
        elif action_evidence:
            signal = "action_detected"
        elif primary_count and secondary_count:
            signal = "current_primary_and_reporting_found"
        elif primary_count:
            signal = "current_primary_evidence_found"
        elif secondary_count:
            signal = "current_reporting_found"
        else:
            signal = "needs_research"

        packets.append({
            "id": stable_id("promise_packet", promise["id"], query),
            "checked_at": now_iso(),
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
            "human_reviewed": False,
            "status_reason": (
                "This is an automated implementation-evidence signal. Final fulfilment "
                "requires the original target and current metric to be directly comparable."
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

    promise_batch = select_promise_batch(promises, batch_size=30)
    fresh_promise_packets, promise_errors = build_promise_packets(promise_batch)
    merged_promises = previous_promise_packets()
    for packet in fresh_promise_packets:
        merged_promises[packet["promise_id"]] = packet
    promise_packets = list(merged_promises.values())
    promise_packets.sort(
        key=lambda p: (
            -int(p.get("year", 0)),
            p.get("page", 0),
            p.get("promise_id", ""),
        )
    )

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
            "independent_primary_gate": True,
            "automated_nli": bool(get_nli()),
            "nli_model": NLI_MODEL_NAME if get_nli() else None,
            "nli_error": _NLI_ERROR,
            "news_context_search": True,
            "fact_check_api_configured": bool(
                os.getenv("FACTCHECK_API_KEY", "").strip()
            ),
            "manifesto_memory_loaded": bool(promises),
        },
        "summary": {
            "claim_packets": len(claim_packets),
            "promise_packets": len(promise_packets),
            "promise_checked_this_run": len(fresh_promise_packets),
            "promise_total_candidates": len(promises),
            "promise_coverage_pct": round(
                (len(promise_packets) / len(promises) * 100) if promises else 0,
                1,
            ),
            "primary_candidates": sum(
                1 for e in all_evidence if e.get("tier") == "primary"
            ),
            "secondary_candidates": sum(
                1 for e in all_evidence if e.get("tier") == "secondary"
            ),
            "prior_fact_checks": len(fact_checks),
            "publishable_auto_verdicts": sum(
                1 for packet in claim_packets
                if packet.get("signal", {}).get("publishable_verdict")
            ),
        },
        "claim_packets": claim_packets,
        "promise_packets": promise_packets,
        "errors": claim_errors + promise_errors,
        "notes": [
            "Evidence candidates are not verdicts.",
            "Official-search results are discarded unless they share substantive terms or numeric values with the claim.",
            "Google Fact Check Tools matches are attributed to their publishers and are not adopted as ClaimWatch verdicts automatically.",
            "PMIndia/party material is provenance for what was said, not independent proof that the claim is true.",
            "Automated supported/contradicted verdicts require a strict independent-primary NLI gate; uncertain cases remain pending.",
            "Promise evidence scanning rotates through the corpus and preserves prior checks so coverage accumulates over time.",
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
