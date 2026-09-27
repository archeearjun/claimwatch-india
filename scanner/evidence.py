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
    "MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli",
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

def is_verified_primary(row):
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

def resolve_official_destination(row):
    direct_url = row.get("url") or ""
    direct_host = hostname(direct_url)
    source_url = row.get("source_url") or ""
    source_host = hostname(source_url)

    def official_host(host):
        return bool(host) and (
            host.endswith(".gov.in")
            or host.endswith(".nic.in")
            or any(host == h or host.endswith("." + h) for h in INDEPENDENT_OFFICIAL_HOSTS)
        )

    parsed_direct = urlparse(direct_url) if direct_url else None
    if official_host(direct_host) and parsed_direct and parsed_direct.path not in {"", "/"}:
        return direct_url

    if not official_host(source_host):
        return source_url or direct_url

    title = clean_html(row.get("title") or "")
    title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title).strip()
    if title:
        try:
            results = bing_web(
                f'"{title[:180]}" site:{source_host}',
                "official_title_resolve",
                "primary",
                limit=5,
            )
            for candidate in results:
                url = candidate.get("url") or candidate.get("source_url") or ""
                host = hostname(url)
                parsed = urlparse(url) if url else None
                if (
                    parsed
                    and parsed.path not in {"", "/"}
                    and (host == source_host or host.endswith("." + source_host))
                ):
                    return url
        except Exception:
            pass

    return source_url or direct_url


def hydrate_official_row(row, query_text):
    if row.get("tier") != "primary" or not is_verified_primary(row):
        return row

    url = resolve_official_destination(row)
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
        if row.get("tier") == "primary" and not is_verified_primary(row):
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
        if len(matched_terms) < 2 and not (shared_numbers and len(matched_terms) >= 1):
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

    # MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli label order:
    # entailment, neutral, contradiction.
    return {
        "entailment": float(probs[0]),
        "neutral": float(probs[1]),
        "contradiction": float(probs[2]),
    }

CLAIM_METRIC_STOPWORDS = STOPWORDS | {
    "about","around","approximately","approx","nearly","more","less","than",
    "under","over","today","current","currently","scheme","programme","program",
    "pradhan","mantri","yojana","bharat","viksit","new","first",
    "rupees","rupee","rs","crore","lakh","million","billion","trillion",
    "percent","percentage","mw","gw",
}

FINANCE_OUTLAY_RE = re.compile(
    r"\b(?:outlay|budget|allocation|allocated|investment|invested|cost|worth|earmarked)\b",
    re.I,
)
FINANCE_EXPENDITURE_RE = re.compile(
    r"\b(?:spent|spending|expenditure|expended|incurred)\b",
    re.I,
)
FINANCE_DISBURSEMENT_RE = re.compile(
    r"\b(?:disburs(?:e|ed|es|ing)|released|release|paid|payment|incentive|payout)\b",
    re.I,
)


def financial_metric(text):
    value = text or ""
    if FINANCE_OUTLAY_RE.search(value):
        return "outlay"
    if FINANCE_EXPENDITURE_RE.search(value):
        return "expenditure"
    if FINANCE_DISBURSEMENT_RE.search(value):
        return "disbursement"
    return None


def quantity_context_terms(text, quantity, radius=95):
    if not quantity:
        return set()
    start = max(0, int(quantity.get("start") or 0) - radius)
    end = min(len(text or ""), int(quantity.get("end") or 0) + radius)
    window = (text or "")[start:end]
    return {
        term for term in tokens(window)
        if term not in CLAIM_METRIC_STOPWORDS and len(term) >= 3
    }


def metric_context_overlap(claim_text, claim_quantity, evidence_text, evidence_quantity):
    claim_terms = quantity_context_terms(claim_text, claim_quantity)
    evidence_terms = quantity_context_terms(evidence_text, evidence_quantity)
    return sorted(claim_terms & evidence_terms)


def quantity_metric_compatible(claim_text, claim_quantity, evidence_text, evidence_quantity):
    overlap = metric_context_overlap(
        claim_text,
        claim_quantity,
        evidence_text,
        evidence_quantity,
    )

    claim_finance = financial_metric(claim_text)
    evidence_finance = financial_metric(evidence_text)
    if claim_finance or evidence_finance:
        # Financial quantities are especially easy to confuse: total outlay,
        # actual expenditure and one disbursement are different metrics.
        if not claim_finance or not evidence_finance or claim_finance != evidence_finance:
            return False, overlap
        return len(overlap) >= 1, overlap

    return len(overlap) >= 2, overlap


def numeric_claim_operator(text):
    low = (text or "").lower()
    if re.search(r"\b(?:more than|over|above|greater than|at least)\b", low) or re.search(r"(?:से अधिक|से ज्यादा|कम से कम)", low):
        return "gte"
    if re.search(r"\b(?:less than|under|below|at most)\b", low) or re.search(r"(?:से कम|अधिकतम)", low):
        return "lte"
    if re.search(r"\b(?:nearly|about|around|approximately|approx\.?|roughly)\b", low) or re.search(r"(?:लगभग|करीब)", low):
        return "approx"
    return "eq"


def numeric_relation_holds(operator, claimed, observed):
    if claimed == 0:
        tolerance = 0.01
    else:
        tolerance = max(abs(claimed) * 0.005, 1e-9)

    if operator == "gte":
        return observed >= claimed
    if operator == "lte":
        return observed <= claimed
    if operator == "approx":
        return abs(observed - claimed) <= max(abs(claimed) * 0.05, tolerance)
    return abs(observed - claimed) <= tolerance


def structured_claim_numeric_signal(text, evidence):
    claim_quantities = parse_quantity_mentions(text)
    if len(claim_quantities) != 1:
        return None

    claim_quantity = claim_quantities[0]
    claim_years = set(re.findall(r"\b20\d{2}\b", text or ""))
    operator = numeric_claim_operator(text)

    for row in evidence:
        if not (
            row.get("tier") == "primary"
            and is_verified_primary(row)
            and float(row.get("relevance") or 0) >= 0.30
            and len(row.get("matched_terms") or []) >= 3
        ):
            continue

        evidence_text = " ".join([
            row.get("title") or "",
            row.get("snippet") or "",
        ])
        if claim_years and not claim_years.intersection(
            set(re.findall(r"\b20\d{2}\b", evidence_text))
        ):
            continue

        observed = unique_quantities([
            q for q in parse_quantity_mentions(evidence_text)
            if q["kind"] == claim_quantity["kind"]
        ])

        # Multiple numbers make an automated comparison ambiguous unless one
        # value exactly matches the claim and no conflicting candidate exists.
        if len(observed) != 1:
            matching = [
                q for q in observed
                if numeric_relation_holds(
                    "eq",
                    claim_quantity["value"],
                    q["value"],
                )
            ]
            if len(matching) == 1:
                observed = matching
            else:
                continue

        observed_quantity = observed[0]
        metric_ok, metric_overlap = quantity_metric_compatible(
            text,
            claim_quantity,
            evidence_text,
            observed_quantity,
        )
        if not metric_ok:
            continue

        holds = numeric_relation_holds(
            operator,
            claim_quantity["value"],
            observed_quantity["value"],
        )

        proof = {
            "claimed": claim_quantity,
            "observed": observed_quantity,
            "operator": operator,
            "evidence_id": row.get("id"),
            "evidence_url": row.get("source_url") or row.get("url"),
            "evidence_title": row.get("title"),
            "metric_overlap": metric_overlap,
            "metric_type": financial_metric(text),
        }

        if holds:
            return {
                "level": "automated_supported",
                "label": "Supported by deterministic primary-source comparison",
                "publishable_verdict": True,
                "verdict": "supported",
                "reason": (
                    "The numerical claim and the verified primary-source value are directly "
                    "comparable under the structured numeric rule."
                ),
                "evidence_id": row.get("id"),
                "assessment_mode": "deterministic_numeric_primary_gate",
                "proof": proof,
            }

        return {
            "level": "automated_contradicted",
            "label": "Contradicted by deterministic primary-source comparison",
            "publishable_verdict": True,
            "verdict": "contradicted",
            "reason": (
                "The verified primary-source value conflicts with the numerical claim under "
                "the structured numeric rule."
            ),
            "evidence_id": row.get("id"),
            "assessment_mode": "deterministic_numeric_primary_gate",
            "proof": proof,
        }

    return None


def strict_signal(text, evidence, factchecks):
    deterministic = structured_claim_numeric_signal(text, evidence)
    if deterministic:
        return deterministic

    primary = [
        e for e in evidence
        if e.get("tier") == "primary"
        and is_verified_primary(e)
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

        numeric_metric_ok = True
        if claim_numbers:
            claim_quantities = unique_quantities(parse_quantity_mentions(text))
            premise_quantities = unique_quantities(parse_quantity_mentions(premise))
            compatible_pairs = []
            for cq in claim_quantities:
                for pq in premise_quantities:
                    if cq.get("kind") != pq.get("kind"):
                        continue
                    metric_ok, _ = quantity_metric_compatible(text, cq, premise, pq)
                    if metric_ok:
                        compatible_pairs.append((cq, pq))
            numeric_metric_ok = bool(compatible_pairs)

        numeric_support_ok = (
            (not claim_numbers or bool(claim_numbers & shared_numbers))
            and numeric_metric_ok
        )
        numeric_conflict_possible = bool(
            claim_numbers and row_numbers and not shared_numbers and numeric_metric_ok
        )

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
            "label": "Conflicting verified primary evidence found",
            "publishable_verdict": False,
            "verdict": "insufficient",
            "reason": "Verified primary-source evidence produced conflicting machine signals; human review is required.",
        }

    if best_support:
        return {
            "level": "automated_supported",
            "label": "Supported by verified primary evidence",
            "publishable_verdict": True,
            "verdict": "supported",
            "reason": "A highly relevant verified primary source entails the claim under the strict automated threshold.",
            "evidence_id": best_support.get("id"),
            "assessment_mode": "strict_nli_primary_gate",
        }

    if best_contradiction:
        return {
            "level": "automated_contradicted",
            "label": "Contradicted by verified primary evidence",
            "publishable_verdict": True,
            "verdict": "contradicted",
            "reason": "A highly relevant verified primary source contradicts the numerical claim under the strict automated threshold.",
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
            "label": "Relevant verified primary evidence candidate found",
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

    current_year = datetime.now(timezone.utc).year
    deadline_priority = []
    remainder = []

    for promise in promises:
        deadline = promise_deadline_year(promise)
        if (
            promise.get("measurable")
            and deadline
            and deadline < current_year
        ):
            deadline_priority.append(promise)
        else:
            remainder.append(promise)

    deadline_priority.sort(
        key=lambda p: (
            promise_deadline_year(p) or 9999,
            -int(p.get("candidate_score", 0)),
            p.get("id", ""),
        )
    )

    # Keep a fixed tranche of overdue measurable commitments under frequent
    # scrutiny while rotating the rest of the corpus.
    fixed_count = min(10, batch_size, len(deadline_priority))
    fixed = deadline_priority[:fixed_count]

    pool = [
        p for p in deadline_priority[fixed_count:] + remainder
        if p.get("id") not in {x.get("id") for x in fixed}
    ]
    if not pool or len(fixed) >= batch_size:
        return fixed[:batch_size]

    pool.sort(
        key=lambda p: (
            not p.get("measurable"),
            not bool(p.get("deadline_hints")),
            -int(p.get("candidate_score", 0)),
            -int(p.get("year", 0)),
            p.get("id", ""),
        )
    )
    epoch_hours = int(datetime.now(timezone.utc).timestamp() // 3600)
    remaining = batch_size - len(fixed)
    start = (epoch_hours * max(1, remaining)) % len(pool)
    rotating = [
        pool[(start + offset) % len(pool)]
        for offset in range(min(remaining, len(pool)))
    ]
    return fixed + rotating


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

WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def promise_deadline_year(promise):
    year = int(promise.get("year") or 0)
    hints = " ".join(promise.get("deadline_hints") or []).lower()
    text = " ".join([
        promise.get("exact_text") or "",
        hints,
    ]).lower()

    explicit = re.search(r"\b(?:by|before|until|through)\s+(20\d{2})\b", text)
    if explicit:
        return int(explicit.group(1))

    numeric = re.search(r"\b(?:within|next|over the next|in the next)\s+(\d+)\s+years?\b", text)
    if numeric and year:
        return year + int(numeric.group(1))

    words = re.search(
        r"\b(?:within|next|over the next|in the next)\s+"
        r"(one|two|three|four|five|six|seven|eight|nine|ten)\s+years?\b",
        text,
    )
    if words and year:
        return year + WORD_NUMBERS[words.group(1)]

    return None


def promise_outcome(packet):
    current_year = datetime.now(timezone.utc).year
    deadline_year = promise_deadline_year(packet)
    signal = packet.get("implementation_signal") or "needs_research"

    result = {
        "status": "insufficient_evidence",
        "deadline_year": deadline_year,
        "proof_strength": "none",
        "proof_evidence_id": None,
        "proof": None,
        "reason": "Available evidence is not yet sufficient for a documented outcome.",
    }

    deterministic = structured_promise_proof(packet)
    if deterministic:
        proof = deterministic.get("proof") or {}
        return {
            **result,
            **deterministic,
            "deadline_year": deadline_year,
            "proof_evidence_id": proof.get("evidence_id"),
        }

    if deadline_year and current_year <= deadline_year:
        return {
            **result,
            "status": "deadline_not_reached",
            "reason": f"The extracted deadline is {deadline_year}, so the promise is not yet overdue.",
        }

    verified_primary = [
        row for row in packet.get("evidence", [])
        if row.get("tier") == "primary"
        and is_verified_primary(row)
        and len(row.get("promise_core_overlap") or []) >= 2
    ]

    if signal == "target_evidence_detected":
        proof = next(
            (row for row in verified_primary if row.get("shared_numbers")),
            verified_primary[0] if verified_primary else None,
        )
        return {
            **result,
            "status": "target_reached_evidence",
            "proof_strength": "verified_primary_target_language",
            "proof_evidence_id": proof.get("id") if proof else None,
            "reason": (
                "Verified primary evidence uses completion/target language, but the structured "
                "comparison does not yet prove whether the target was met by the deadline."
            ),
        }

    if signal == "action_detected" and verified_primary:
        return {
            **result,
            "status": "progress_documented",
            "proof_strength": "verified_primary_action_with_object_overlap",
            "proof_evidence_id": verified_primary[0].get("id"),
            "reason": (
                "Verified primary evidence overlaps the subject of the promise and documents "
                "implementation activity, but not the promised outcome."
            ),
        }

    if deadline_year and current_year > deadline_year:
        return {
            **result,
            "status": "deadline_passed_unresolved",
            "reason": (
                f"The extracted deadline ({deadline_year}) has passed, but ClaimWatch does not "
                "have direct comparable post-deadline evidence proving fulfilment or non-fulfilment."
            ),
        }

    if not packet.get("measurable"):
        return {
            **result,
            "status": "not_machine_measurable",
            "reason": (
                "The extracted commitment does not contain a sufficiently specific numeric or "
                "time-bound target for automatic outcome proof."
            ),
        }

    return result


def promise_outcome_summary(promise_packets):
    counts = Counter()
    for packet in promise_packets:
        outcome = packet.get("outcome") or promise_outcome(packet)
        counts[outcome.get("status") or "insufficient_evidence"] += 1

    return {
        "fulfilled_by_deadline": counts["fulfilled_by_deadline"],
        "target_reached_evidence": counts["target_reached_evidence"],
        "proven_unfulfilled_by_deadline": counts["proven_unfulfilled_by_deadline"],
        "progress_documented": counts["progress_documented"],
        "deadline_passed_unresolved": counts["deadline_passed_unresolved"],
        "deadline_not_reached": counts["deadline_not_reached"],
        "insufficient_evidence": counts["insufficient_evidence"],
        "not_machine_measurable": counts["not_machine_measurable"],
        "audited_total": sum(counts.values()),
    }


PROMISE_GENERIC_TERMS = {
    "will","would","shall","ensure","provide","continue","make","work","develop",
    "establish","create","launch","introduce","implement","expand","increase",
    "strengthen","support","promote","scheme","mission","programme","program",
    "government","bharat","india","country","people","citizens","modi","guarantee",
    "towards","further","focus","facilitate","enable","committed","commitment",
}

PROMISE_ACTION_RE = re.compile(
    r"\b(?:launched|implemented|notified|approved|operationali[sz]ed|"
    r"rolled out|expanded|extended|completed|achieved|reached|introduced|"
    r"established|set up|sanctioned|allocated|started|commenced|covered|"
    r"provided|increased|created|constructed|formed|built|delivered)\b",
    re.I,
)
PROMISE_COMPLETION_RE = re.compile(
    r"\b(?:completed|achieved|fully implemented|target achieved|"
    r"reached the target|operationali[sz]ed|completed the target)\b",
    re.I,
)
OBSERVATION_RE = re.compile(
    r"\b(?:as of|so far|till date|to date|total|have been|has been|were|"
    r"created|constructed|formed|established|completed|achieved|covered|"
    r"provided|reached|registered|sanctioned)\b",
    re.I,
)
REDUCTION_RE = re.compile(
    r"\b(?:reduce|reduced|decrease|decreased|lower|lowered|bring down|cut)\b",
    re.I,
)
QUANTITY_RE = re.compile(
    r"(?<!\w)(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(%|percent|lakh\s+crore|crore|lakh|million|billion|trillion|mw|gw|"
    r"प्रतिशत|लाख\s+करोड़|लाख\s+करोड|करोड़|करोड|लाख)?\b",
    re.I,
)
SCALE_FACTORS = {
    "crore": 10_000_000.0,
    "lakh": 100_000.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
    "mw": 1.0,
    "gw": 1_000.0,
    "lakh crore": 1_000_000_000_000.0,
    "लाख करोड़": 1_000_000_000_000.0,
    "लाख करोड": 1_000_000_000_000.0,
    "करोड़": 10_000_000.0,
    "करोड": 10_000_000.0,
    "लाख": 100_000.0,
}


def promise_core_terms(promise):
    source = promise.get("exact_text") or promise.get("anchor") or ""
    out = []
    for term in tokens(source):
        if term in PROMISE_GENERIC_TERMS or len(term) < 4:
            continue
        if term not in out:
            out.append(term)
        if len(out) >= 14:
            break
    return out


def promise_core_overlap(promise, evidence_text):
    core = set(promise_core_terms(promise))
    if not core:
        return []
    evidence_terms = set(tokens(evidence_text))
    return sorted(core & evidence_terms)


def sanitize_promise_evidence(promise, rows):
    normalized = []
    for original in rows or []:
        row = dict(original)
        if row.get("tier") == "primary" and not is_verified_primary(row):
            row["tier"] = "secondary"
        normalized.append(row)

    promise_text = " ".join([
        promise.get("exact_text") or promise.get("anchor") or "",
        " ".join(promise.get("numbers") or []),
        " ".join(promise.get("deadline_hints") or []),
    ])
    reranked = rank_evidence(promise_text, normalized, limit=10)

    cleaned = []
    for row in reranked:
        evidence_text = " ".join([
            row.get("title") or "",
            row.get("snippet") or "",
            row.get("claim_text") or "",
        ])
        core_overlap = promise_core_overlap(promise, evidence_text)
        row["promise_core_overlap"] = core_overlap

        matched_terms = row.get("matched_terms") or []
        shared_numbers = row.get("shared_numbers") or []
        relevance = float(row.get("relevance") or 0)

        if len(core_overlap) < 1 and len(matched_terms) < 2:
            continue
        if row.get("tier") == "secondary" and relevance < 0.18 and len(core_overlap) < 2:
            continue
        if shared_numbers and not core_overlap:
            continue

        cleaned.append(row)

    cleaned.sort(
        key=lambda row: (
            0 if row.get("tier") == "primary" and is_verified_primary(row) else 1,
            -float(row.get("relevance") or 0),
        )
    )
    return cleaned[:10]


def classify_promise_evidence(promise, evidence):
    verified_primary = [
        row for row in evidence
        if row.get("tier") == "primary"
        and is_verified_primary(row)
        and len(row.get("promise_core_overlap") or []) >= 2
        and float(row.get("relevance") or 0) >= 0.22
    ]
    secondary = [
        row for row in evidence
        if row.get("tier") == "secondary"
        and len(row.get("promise_core_overlap") or []) >= 2
        and float(row.get("relevance") or 0) >= 0.20
    ]

    promise_numbers = numeric_tokens(
        " ".join([
            promise.get("exact_text") or "",
            " ".join(promise.get("numbers") or []),
        ])
    )

    action_evidence = []
    target_evidence = []
    for row in verified_primary:
        evidence_text = " ".join([
            row.get("title") or "",
            row.get("snippet") or "",
        ])
        if PROMISE_ACTION_RE.search(evidence_text):
            action_evidence.append(row)
            evidence_numbers = numeric_tokens(evidence_text)
            if (
                promise.get("measurable")
                and promise_numbers
                and promise_numbers & evidence_numbers
                and PROMISE_COMPLETION_RE.search(evidence_text)
                and len(row.get("promise_core_overlap") or []) >= 2
            ):
                target_evidence.append(row)

    if target_evidence:
        return "target_evidence_detected"
    if action_evidence:
        return "action_detected"
    if verified_primary and secondary:
        return "current_primary_and_reporting_found"
    if verified_primary:
        return "current_primary_evidence_found"
    if secondary:
        return "current_reporting_found"
    return "needs_research"


def parse_quantity_mentions(text):
    rows = []
    for match in QUANTITY_RE.finditer(text or ""):
        raw_number = match.group(1)
        unit = (match.group(2) or "").lower()
        try:
            base = float(raw_number.replace(",", ""))
        except ValueError:
            continue
        if not unit and 1900 <= base <= 2100:
            continue

        context = (text or "")[max(0, match.start() - 8):match.end() + 18]
        if unit in {"%", "percent", "प्रतिशत"}:
            normalized = base
            kind = "percent"
        elif unit in {"mw", "gw"}:
            normalized = base * SCALE_FACTORS.get(unit, 1.0)
            kind = "power_mw"
        elif (
            "₹" in context
            or re.search(r"\b(?:rs\.?|rupees?|रुपये|रुपया)\b", context, re.I)
            or unit in {
                "crore","lakh","million","billion","trillion","lakh crore",
                "करोड़","करोड","लाख","लाख करोड़","लाख करोड",
            }
        ):
            normalized = base * SCALE_FACTORS.get(unit, 1.0)
            kind = "currency_rupees"
        else:
            normalized = base * SCALE_FACTORS.get(unit, 1.0)
            kind = "count"

        rows.append({
            "raw": match.group(0).strip(),
            "value": normalized,
            "kind": kind,
            "start": match.start(),
            "end": match.end(),
        })
    return rows


def unique_quantities(rows):
    unique = []
    seen = set()
    for row in rows or []:
        key = (
            row.get("kind"),
            round(float(row.get("value") or 0), 9),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def promise_target_quantity(promise):
    text = promise.get("exact_text") or ""
    deadline_year = promise_deadline_year(promise)

    candidates = []
    for match in QUANTITY_RE.finditer(text):
        raw = match.group(0).strip()
        following = text[match.end():match.end() + 16].lower()
        if re.match(r"\s*(?:years?|months?|days?)\b", following):
            continue

        rows = parse_quantity_mentions(raw)
        if not rows:
            continue
        quantity = rows[0]

        context = text[max(0, match.start() - 55):match.end() + 55].lower()
        score = 0

        if re.search(r"\b(?:we will|will|aim to|target|commit|achieve|reach|create|build|provide|increase|reduce)\b", context):
            score += 3
        if re.search(r"\b(?:target of|target|by 20\d{2}|within|next five years|next 5 years)\b", context):
            score += 2
        if re.search(r"\b(?:already|currently|existing|achieved|as on|baseline|so far)\b", context):
            score -= 5

        if deadline_year:
            deadline_positions = [
                m.start()
                for m in re.finditer(re.escape(str(deadline_year)), text)
            ]
            if deadline_positions:
                distance = min(abs(match.start() - pos) for pos in deadline_positions)
                if distance <= 45:
                    score += 7
                elif distance <= 100:
                    score += 4
                elif distance <= 180:
                    score += 1

        candidates.append((score, match.start(), quantity))

    if not candidates:
        # Fall back to parsed target-like fields from the corpus.
        for raw in promise.get("numbers") or []:
            if re.search(r"\b(?:years?|months?|days?)\b", raw, re.I):
                continue
            rows = parse_quantity_mentions(raw)
            if rows:
                candidates.append((0, 0, rows[0]))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], item[1]))
    best_score = candidates[0][0]
    best = [item for item in candidates if item[0] == best_score]

    # Multiple equally plausible targets are not safe for an automatic proof.
    if len(best) != 1:
        return None
    return best[0][2]


def evidence_year(row):
    published = row.get("published_at")
    if published:
        try:
            return datetime.fromisoformat(str(published).replace("Z", "+00:00")).year
        except Exception:
            pass

    text = " ".join([
        row.get("title") or "",
        row.get("snippet") or "",
    ])
    years = [
        int(value)
        for value in re.findall(r"\b20\d{2}\b", text)
        if 2000 <= int(value) <= datetime.now(timezone.utc).year
    ]
    return max(years) if years else None


def comparable_observed_quantity(promise, row, target):
    evidence_text = " ".join([
        row.get("title") or "",
        row.get("snippet") or "",
    ])
    if len(promise_core_overlap(promise, evidence_text)) < 2:
        return None
    if not OBSERVATION_RE.search(evidence_text):
        return None

    quantities = unique_quantities([
        q for q in parse_quantity_mentions(evidence_text)
        if q["kind"] == target["kind"]
    ])
    if not quantities:
        return None

    # If the evidence contains the target number and one other number, prefer
    # the other number as the observed value only when the sentence explicitly
    # frames progress/achievement.
    different = [
        q for q in quantities
        if abs(q["value"] - target["value"]) > max(1.0, target["value"] * 1e-9)
    ]
    if len(different) == 1:
        return different[0]
    if len(quantities) == 1:
        return quantities[0]
    return None


def structured_promise_proof(packet):
    deadline_year = promise_deadline_year(packet)
    target = promise_target_quantity(packet)
    if not deadline_year or not target:
        return None

    current_year = datetime.now(timezone.utc).year
    direction = "lte" if REDUCTION_RE.search(packet.get("exact_text") or "") else "gte"

    verified_primary = [
        row for row in packet.get("evidence", [])
        if row.get("tier") == "primary"
        and is_verified_primary(row)
        and len(row.get("promise_core_overlap") or []) >= 2
    ]

    for row in verified_primary:
        year = evidence_year(row)
        if not year:
            continue
        observed = comparable_observed_quantity(packet, row, target)
        if not observed:
            continue

        met = (
            observed["value"] <= target["value"]
            if direction == "lte"
            else observed["value"] >= target["value"]
        )

        proof = {
            "target": target,
            "observed": observed,
            "evidence_year": year,
            "evidence_id": row.get("id"),
            "evidence_url": row.get("source_url") or row.get("url"),
            "direction": direction,
        }

        if met:
            if year <= deadline_year:
                return {
                    "status": "fulfilled_by_deadline",
                    "proof_strength": "deterministic_numeric_comparison",
                    "proof": proof,
                    "reason": (
                        f"Verified primary evidence dated {year} reports an observed value "
                        f"meeting the target before or at the {deadline_year} deadline."
                    ),
                }
            return {
                "status": "target_reached_evidence",
                "proof_strength": "deterministic_numeric_comparison",
                "proof": proof,
                "reason": (
                    f"Verified primary evidence dated {year} reports the target value as reached, "
                    f"but this alone does not prove it was reached by the {deadline_year} deadline."
                ),
            }

        # A red-bar failure requires evidence near the deadline, not simply a
        # later absence of evidence. This makes non-fulfilment a positive proof.
        if (
            current_year > deadline_year
            and deadline_year <= year <= deadline_year + 1
        ):
            return {
                "status": "proven_unfulfilled_by_deadline",
                "proof_strength": "deterministic_numeric_comparison",
                "proof": proof,
                "reason": (
                    f"Verified primary evidence dated {year} reports an observed value below "
                    f"the promised target after the {deadline_year} deadline."
                ),
            }

    return None


def retrieve_promise_evidence(promise, base_query, pseudo_text):
    evidence = retrieve_evidence(
        base_query,
        pseudo_text,
        hydrate_primary=True,
    )

    deadline = promise_deadline_year(promise)
    current_year = datetime.now(timezone.utc).year
    if not (
        promise.get("measurable")
        and deadline
        and deadline < current_year
    ):
        return evidence

    target = promise_target_quantity(promise)
    core = promise_core_terms(promise)[:6]
    target_text = target.get("raw") if target else ""
    deadline_query = " ".join(
        part for part in (
            target_text,
            " ".join(core),
            str(deadline),
            "target achieved progress as of",
        )
        if part
    )[:260]

    if not deadline_query or deadline_query == base_query:
        return evidence

    try:
        extra = retrieve_evidence(
            deadline_query,
            pseudo_text,
            hydrate_primary=True,
        )
    except Exception:
        extra = []

    seen = set()
    merged = []
    for row in evidence + extra:
        key = row.get("id") or row.get("source_url") or row.get("url") or row.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(row)
    return merged


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
            promise.get("exact_text") or promise.get("anchor") or "",
            " ".join(promise.get("keywords") or []),
            " ".join(promise.get("numbers") or []),
            " ".join(promise.get("deadline_hints") or []),
        ])

        try:
            evidence = retrieve_promise_evidence(
                promise,
                query,
                pseudo_text,
            )
        except Exception as exc:
            evidence = []
            errors.append({
                "stage": "promise_evidence_search",
                "promise_id": promise["id"],
                "error": type(exc).__name__,
            })

        evidence = sanitize_promise_evidence(promise, evidence)
        signal = classify_promise_evidence(promise, evidence)

        packet = {
            "id": stable_id("promise_packet", promise["id"], query),
            "checked_at": now_iso(),
            "promise_id": promise["id"],
            "year": promise["year"],
            "category": promise["category"],
            "page": promise["page"],
            "anchor": promise.get("anchor"),
            "exact_text": promise.get("exact_text"),
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
        }
        packet["outcome"] = promise_outcome(packet)
        packets.append(packet)

    return packets, errors

def previous_claim_families():
    if not OUT_PATH.exists():
        return []
    try:
        payload = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(payload.get("claim_families", []) or [])


def normalized_claim_key(text):
    return re.sub(r"\W+", " ", (text or "").lower()).strip()


def claim_number_signature(text):
    return tuple(sorted(numeric_tokens(text)))


def claim_lexical_similarity(a, b):
    aa = keywords(a, limit=16)
    bb = keywords(b, limit=16)
    if not aa or not bb:
        return 0.0
    aset, bset = set(aa), set(bb)
    shared = len(aset & bset)
    j = shared / max(1, len(aset | bset))
    containment = shared / max(1, min(len(aset), len(bset)))
    return 0.55 * j + 0.45 * containment


def claim_family_similarity(a, b):
    a_nums = set(claim_number_signature(a))
    b_nums = set(claim_number_signature(b))
    if a_nums or b_nums:
        if not a_nums or not b_nums or not (a_nums & b_nums):
            return 0.0

    lexical = claim_lexical_similarity(a, b)
    if lexical < 0.18:
        return lexical

    forward = nli_scores(a, b)
    backward = nli_scores(b, a)
    if forward and backward:
        semantic = min(
            forward.get("entailment", 0.0),
            backward.get("entailment", 0.0),
        )
        return max(lexical, semantic)
    return lexical


def merge_claim_families(claim_packets):
    families = previous_claim_families()
    by_exact = {
        normalized_claim_key(family.get("canonical_claim")): family
        for family in families
        if family.get("canonical_claim")
    }

    for family in families:
        family.setdefault("occurrences", [])
        family.setdefault("current_verdict", None)
        family.setdefault("evidence_state", "pending")
        family.setdefault("human_reviewed", False)

    for packet in claim_packets:
        claim = packet.get("claim") or ""
        if not claim:
            continue

        exact_key = normalized_claim_key(claim)
        family = by_exact.get(exact_key)
        match_score = 1.0 if family else 0.0

        if family is None:
            lexical_candidates = []
            for candidate in families:
                canonical = candidate.get("canonical_claim") or ""
                score = claim_lexical_similarity(claim, canonical)
                if score >= 0.18:
                    lexical_candidates.append((score, candidate))
            lexical_candidates.sort(key=lambda row: -row[0])

            for _, candidate in lexical_candidates[:4]:
                score = claim_family_similarity(
                    claim,
                    candidate.get("canonical_claim") or "",
                )
                if score > match_score:
                    match_score = score
                    family = candidate

            if match_score < 0.86:
                family = None

        if family is None:
            family = {
                "id": stable_id("claim_family", claim),
                "canonical_claim": claim,
                "occurrences": [],
                "current_verdict": None,
                "evidence_state": "pending",
                "human_reviewed": False,
                "created_at": now_iso(),
            }
            families.append(family)
            by_exact[exact_key] = family
            match_score = 1.0

        source = packet.get("claim_source") or {}
        occurrence_id = stable_id(
            "occurrence",
            source.get("url"),
            source.get("published_at"),
            claim,
        )
        existing_ids = {
            occurrence.get("id")
            for occurrence in family.get("occurrences", [])
        }
        if occurrence_id not in existing_ids:
            family["occurrences"].append({
                "id": occurrence_id,
                "text": claim,
                "source_title": source.get("title"),
                "source": source.get("source") or source.get("channel_title"),
                "url": source.get("url"),
                "published_at": source.get("published_at"),
                "match_score": round(match_score, 4),
            })

        family["occurrences"] = sorted(
            family["occurrences"],
            key=lambda row: row.get("published_at") or "",
        )[-60:]
        family["occurrence_count"] = len(family["occurrences"])
        family["first_seen"] = (
            family["occurrences"][0].get("published_at")
            if family["occurrences"] else None
        )
        family["last_seen"] = (
            family["occurrences"][-1].get("published_at")
            if family["occurrences"] else None
        )

        signal = packet.get("signal") or {}
        family["evidence_state"] = signal.get("level") or packet.get("verdict") or "pending"
        family["latest_checked_at"] = now_iso()
        family["latest_claim_packet_id"] = packet.get("id")

        if signal.get("publishable_verdict"):
            family["current_verdict"] = packet.get("verdict")
            family["verdict_reason"] = signal.get("reason")
            family["verdict_evidence_id"] = signal.get("evidence_id")
            family["verdict_updated_at"] = now_iso()

    families.sort(
        key=lambda family: (
            -int(family.get("occurrence_count") or 0),
            family.get("canonical_claim") or "",
        )
    )
    return families[:1000]


def claim_family_summary(families):
    repeated = [
        family for family in families
        if int(family.get("occurrence_count") or 0) >= 2
    ]
    contradicted = [
        family for family in repeated
        if family.get("current_verdict") == "contradicted"
    ]
    supported = [
        family for family in repeated
        if family.get("current_verdict") == "supported"
    ]
    return {
        "families_total": len(families),
        "repeated_families": len(repeated),
        "repeated_contradicted_families": len(contradicted),
        "repeated_supported_families": len(supported),
        "occurrences_total": sum(
            int(family.get("occurrence_count") or 0)
            for family in families
        ),
    }


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
    claim_families = merge_claim_families(claim_packets)

    promise_batch = select_promise_batch(promises, batch_size=30)
    fresh_promise_packets, promise_errors = build_promise_packets(promise_batch)
    merged_promises = previous_promise_packets()
    for packet in fresh_promise_packets:
        merged_promises[packet["promise_id"]] = packet

    current_promise_map = {
        promise.get("id"): promise
        for promise in promises
        if promise.get("id")
    }

    promise_packets = list(merged_promises.values())
    for packet in promise_packets:
        current = current_promise_map.get(packet.get("promise_id"))
        if current:
            for field in (
                "year","category","page","anchor","exact_text","keywords","numbers",
                "deadline_hints","measurable","source_url","pdf_url","related_promises",
            ):
                packet[field] = current.get(field)

        packet["evidence"] = sanitize_promise_evidence(
            packet,
            packet.get("evidence", []),
        )
        packet["implementation_signal"] = classify_promise_evidence(
            packet,
            packet.get("evidence", []),
        )
        packet["outcome"] = promise_outcome(packet)
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
            "verified_primary_gate": True,
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
            "claim_verdicts": {
                "supported": sum(
                    1 for packet in claim_packets
                    if packet.get("signal", {}).get("publishable_verdict")
                    and packet.get("verdict") == "supported"
                ),
                "contradicted": sum(
                    1 for packet in claim_packets
                    if packet.get("signal", {}).get("publishable_verdict")
                    and packet.get("verdict") == "contradicted"
                ),
                "pending": sum(
                    1 for packet in claim_packets
                    if not packet.get("signal", {}).get("publishable_verdict")
                ),
            },
            "promise_outcomes": promise_outcome_summary(promise_packets),
            "claim_memory": claim_family_summary(claim_families),
        },
        "claim_packets": claim_packets,
        "claim_families": claim_families,
        "promise_packets": promise_packets,
        "errors": claim_errors + promise_errors,
        "notes": [
            "Evidence candidates are not verdicts.",
            "Official-search results are discarded unless they share substantive terms or numeric values with the claim.",
            "Google Fact Check Tools matches are attributed to their publishers and are not adopted as ClaimWatch verdicts automatically.",
            "PMIndia/party material is provenance for what was said, not proof that the claim is true.",
            "Automated supported/contradicted verdicts require a strict verified-primary NLI gate; uncertain cases remain pending.",
            "Promise evidence scanning rotates through the corpus and preserves prior checks so coverage accumulates over time.",
            "The promise dashboard distinguishes proven outcome states from overdue-but-unresolved commitments; an expired deadline alone is not proof of non-fulfilment.",
            "Repeated claims are grouped into semantic families only after numeric compatibility and bidirectional multilingual entailment checks.",
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
