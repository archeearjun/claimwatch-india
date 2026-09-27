import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.robotparser import RobotFileParser

import feedparser
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = ROOT / "data" / "discovery" / "latest.json"
PROMISES = ROOT / "data" / "promises" / "corpus.json"
OUT = ROOT / "data" / "evidence" / "latest.json"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ClaimWatchIndia/0.5"
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})
TIMEOUT = 18

NLI_MODEL_NAME = os.getenv(
    "NLI_MODEL",
    "cross-encoder/nli-MiniLM2-L6-H768",
)
ENABLE_NLI = os.getenv("ENABLE_NLI", "1") != "0"

OFFICIAL_DOMAINS = {
    "pib.gov.in",
    "mospi.gov.in",
    "rbi.org.in",
    "data.gov.in",
    "cag.gov.in",
    "sansad.in",
    "indiabudget.gov.in",
    "mohfw.gov.in",
    "labour.gov.in",
    "startupindia.gov.in",
    "education.gov.in",
    "agriwelfare.gov.in",
    "financialservices.gov.in",
    "meity.gov.in",
    "mha.gov.in",
    "commerce.gov.in",
    "dpiit.gov.in",
}

NON_VERIFYING_DOMAINS = {
    "pmindia.gov.in",
    "narendramodi.in",
    "bjp.org",
    "www.bjp.org",
    "library.bjp.org",
    "youtube.com",
    "www.youtube.com",
}

STOPWORDS = {
    "about","after","again","against","all","also","and","are","around","as","at",
    "be","because","been","before","being","between","both","but","by","can","country",
    "during","each","for","from","government","has","have","having","he","her","here",
    "him","his","how","i","in","india","indian","into","is","it","its","more","most",
    "new","no","not","of","on","one","only","or","our","over","prime","said","says",
    "she","so","some","such","than","that","the","their","them","then","there","these",
    "they","this","those","through","to","today","under","up","was","we","were","what",
    "when","where","which","who","will","with","would","you","your","minister","modi",
}

NUMBER_RE = re.compile(
    r"(?<!\w)(?:₹|Rs\.?\s*)?\d+(?:[.,]\d+)*(?:\s*(?:%|percent|crore|lakh|million|billion|trillion|km|years?|months?|days?|rupees?))?",
    re.I,
)
ACTION_RE = re.compile(
    r"\b(?:launched|implemented|notified|approved|operationali[sz]ed|rolled out|"
    r"expanded|extended|completed|achieved|reached|introduced|established|set up|"
    r"sanctioned|allocated|started|commenced|covered|provided|increased|created)\b",
    re.I,
)
COMPLETION_RE = re.compile(
    r"\b(?:completed|achieved|fully implemented|operationalised|operationalized|"
    r"rolled out nationwide|target achieved|reached the target)\b",
    re.I,
)

_nli = None
_nli_error = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts):
    return hashlib.sha256(
        "|".join(str(p or "") for p in parts).encode("utf-8")
    ).hexdigest()[:20]


def clean_html(value):
    if not value:
        return ""
    soup = BeautifulSoup(value, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def domain_of(url):
    try:
        return urlparse(url).netloc.lower().split(":")[0]
    except Exception:
        return ""


def is_official(domain):
    if domain in OFFICIAL_DOMAINS:
        return True
    return domain.endswith(".gov.in") or domain.endswith(".nic.in")


def source_tier(url):
    domain = domain_of(url)
    if is_official(domain):
        return 1
    return 2


def tokens(text):
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", (text or "").lower())
    return [
        word for word in words
        if word not in STOPWORDS and not word.isdigit()
    ]


def important_terms(text, limit=9):
    counts = {}
    for word in tokens(text):
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(
        counts,
        key=lambda word: (-counts[word], -len(word), word),
    )
    nums = NUMBER_RE.findall(text or "")
    result = ranked[:limit]
    for num in nums[:3]:
        cleaned = re.sub(r"\s+", " ", num).strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def lexical_score(claim, evidence):
    ct = set(tokens(claim))
    et = set(tokens(evidence))
    if not ct or not et:
        return 0.0
    overlap = len(ct & et)
    base = overlap / max(1, min(len(ct), 10))

    claim_nums = set(NUMBER_RE.findall(claim or ""))
    evidence_nums = set(NUMBER_RE.findall(evidence or ""))
    num_bonus = 0.0
    if claim_nums:
        shared = len(claim_nums & evidence_nums)
        num_bonus = min(0.35, 0.18 * shared)

    return min(1.0, base + num_bonus)


def extract_best_sentence(text, claim):
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    parts = re.split(r"(?<=[.!?।])\s+", clean)
    candidates = [
        p.strip() for p in parts
        if 35 <= len(p.strip()) <= 700
    ]
    if not candidates:
        return clean[:350]
    return max(candidates, key=lambda p: lexical_score(claim, p))[:350]


def robots_allowed(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = SESSION.get(robots_url, timeout=8)
        if response.status_code >= 400:
            return True
        rp = RobotFileParser()
        rp.parse(response.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def fetch_page_excerpt(url, claim):
    if not robots_allowed(url):
        return None, "robots_disallowed"
    try:
        response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            return None, f"http_{response.status_code}"
        ctype = response.headers.get("content-type", "").lower()
        if "html" not in ctype:
            return None, "non_html"

        html = response.text
        if len(html) > 4_000_000:
            html = html[:4_000_000]
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script","style","noscript","svg","nav","footer","header","form","aside"]):
            tag.decompose()

        blocks = []
        for selector in ("article", ".entry-content", ".content", "main", "body"):
            node = soup.select_one(selector)
            if node:
                text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                if len(text) >= 120:
                    blocks.append(text)
        if not blocks:
            return None, "no_text"

        text = max(blocks, key=len)
        return extract_best_sentence(text, claim), "page"
    except Exception as exc:
        return None, f"fetch_error:{type(exc).__name__}"


def rss_search(url, params, source_name):
    try:
        response = SESSION.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        rows = []
        for entry in feed.entries[:10]:
            link = entry.get("link")
            if not link:
                continue
            rows.append({
                "title": clean_html(entry.get("title", "")),
                "url": link,
                "summary": clean_html(
                    entry.get("summary", "") or entry.get("description", "")
                )[:500],
                "published_at": entry.get("published"),
                "search_source": source_name,
            })
        return rows, None
    except Exception as exc:
        return [], f"{source_name}:{type(exc).__name__}"


def bing_search(query):
    return rss_search(
        "https://www.bing.com/search",
        {"q": query, "format": "rss", "count": "10"},
        "Bing RSS",
    )


def google_news_search(query):
    return rss_search(
        "https://news.google.com/rss/search",
        {
            "q": query,
            "hl": "en-IN",
            "gl": "IN",
            "ceid": "IN:en",
        },
        "Google News RSS",
    )


def domain_set_for(text):
    lower = (text or "").lower()
    domains = ["pib.gov.in"]

    if any(k in lower for k in ("gdp", "growth", "economy", "investment", "inflation")):
        domains += ["mospi.gov.in", "rbi.org.in", "indiabudget.gov.in"]
    elif any(k in lower for k in ("job", "employment", "employee", "startup", "rozgar", "worker")):
        domains += ["labour.gov.in", "mospi.gov.in", "startupindia.gov.in"]
    elif any(k in lower for k in ("health", "hospital", "ayushman", "medical", "vaccine")):
        domains += ["mohfw.gov.in", "data.gov.in"]
    elif any(k in lower for k in ("farm", "farmer", "agriculture", "crop", "kisan")):
        domains += ["agriwelfare.gov.in", "data.gov.in"]
    elif any(k in lower for k in ("export", "import", "trade", "fta", "manufactur")):
        domains += ["commerce.gov.in", "dpiit.gov.in", "rbi.org.in"]
    elif any(k in lower for k in ("education", "school", "university", "student")):
        domains += ["education.gov.in", "data.gov.in"]
    elif any(k in lower for k in ("digital", "semiconductor", "internet", "technology", "electronics")):
        domains += ["meity.gov.in", "dpiit.gov.in"]
    else:
        domains += ["data.gov.in", "sansad.in"]

    out = []
    for domain in domains:
        if domain not in out:
            out.append(domain)
    return out[:4]


def search_evidence(statement, include_news=True):
    terms = important_terms(statement)
    query_core = " ".join(terms[:10])
    if not query_core:
        return [], [], []

    queries = []
    results = []
    errors = []

    # Search several independent official sources. PM/party sources are
    # deliberately excluded from truth verification.
    for domain in domain_set_for(statement):
        query = f"site:{domain} {query_core}"
        queries.append(query)
        rows, error = bing_search(query)
        if error:
            errors.append(error)
        results.extend(rows)
        time.sleep(0.15)

    if include_news:
        news_query = query_core
        queries.append(f"news:{news_query}")
        rows, error = google_news_search(news_query)
        if error:
            errors.append(error)
        results.extend(rows)

    deduped = []
    seen = set()
    for row in results:
        url = row.get("url")
        if not url or url in seen:
            continue
        seen.add(url)

        domain = domain_of(url)
        if domain in NON_VERIFYING_DOMAINS:
            continue

        excerpt = row.get("summary") or row.get("title") or ""
        status = "search_snippet"

        # Fetch a bounded number of official pages to ground the verdict in the
        # source document itself instead of a search-engine snippet.
        if is_official(domain) and len(deduped) < 12:
            page_excerpt, fetch_status = fetch_page_excerpt(url, statement)
            if page_excerpt:
                excerpt = page_excerpt
                status = fetch_status

        score = lexical_score(statement, f"{row.get('title','')} {excerpt}")
        if score < 0.08:
            continue

        deduped.append({
            "id": stable_id(url, excerpt),
            "title": row.get("title"),
            "url": url,
            "domain": domain,
            "tier": source_tier(url),
            "excerpt": excerpt[:350],
            "retrieval": status,
            "published_at": row.get("published_at"),
            "search_source": row.get("search_source"),
            "lexical_score": round(score, 4),
            "numbers": NUMBER_RE.findall(excerpt),
        })

    deduped.sort(
        key=lambda x: (
            x["tier"],
            -x["lexical_score"],
        )
    )
    return deduped[:16], queries, errors


def get_nli():
    global _nli, _nli_error
    if not ENABLE_NLI:
        return None
    if _nli is not None:
        return _nli
    if _nli_error is not None:
        return None

    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(NLI_MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(NLI_MODEL_NAME)
        model.eval()
        _nli = (tokenizer, model, torch)
        return _nli
    except Exception as exc:
        _nli_error = f"{type(exc).__name__}: {exc}"
        return None


def nli_scores(premise, hypothesis):
    bundle = get_nli()
    if not bundle:
        return None

    tokenizer, model, torch = bundle
    features = tokenizer(
        premise,
        hypothesis,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch.no_grad():
        logits = model(**features).logits[0]
        probs = torch.softmax(logits, dim=-1).tolist()

    # The chosen model documents this label order.
    return {
        "contradiction": float(probs[0]),
        "entailment": float(probs[1]),
        "neutral": float(probs[2]),
    }


def normalized_number_set(text):
    values = set()
    for raw in NUMBER_RE.findall(text or ""):
        value = re.sub(r"\s+", " ", raw).strip().lower()
        value = value.replace(",", "")
        values.add(value)
    return values


def assess_claim(claim_text, evidence):
    claim_numbers = normalized_number_set(claim_text)
    official = [item for item in evidence if item["tier"] == 1]

    scored = []
    for item in official[:8]:
        scores = nli_scores(item["excerpt"], claim_text)
        if scores:
            item = {**item, "nli": {k: round(v, 4) for k, v in scores.items()}}
        scored.append(item)

    support = None
    contradiction = None

    for item in scored:
        scores = item.get("nli")
        if not scores:
            continue

        ev_numbers = normalized_number_set(item["excerpt"])
        numbers_compatible = (
            not claim_numbers
            or bool(claim_numbers & ev_numbers)
        )

        if (
            scores["entailment"] >= 0.88
            and item["lexical_score"] >= 0.18
            and numbers_compatible
        ):
            if not support or scores["entailment"] > support["nli"]["entailment"]:
                support = item

        # A contradiction threshold is deliberately stricter. For numerical
        # claims, require the evidence to discuss numbers and share the metric
        # vocabulary so unrelated numbers cannot create a false contradiction.
        evidence_has_number = bool(ev_numbers)
        if (
            scores["contradiction"] >= 0.94
            and item["lexical_score"] >= 0.24
            and (not claim_numbers or evidence_has_number)
        ):
            if not contradiction or scores["contradiction"] > contradiction["nli"]["contradiction"]:
                contradiction = item

    if support and contradiction:
        return "insufficient", "conflicting_machine_evidence", scored

    if support:
        return "supported", "official_source_entailment", scored

    if contradiction:
        return "contradicted", "official_source_contradiction", scored

    return "insufficient", "no_strong_independent_grounding", scored


def recent_claims(discovery, limit=12):
    rows = []
    for item in discovery.get("items", []):
        # Only statements from the speaker's own official speech text enter the
        # automated truth checker. News items are evidence/context, not claims
        # attributed to the speaker.
        if item.get("kind") != "official_speech_or_video":
            continue

        for idx, raw in enumerate(item.get("candidate_claims", [])):
            if isinstance(raw, str):
                text = raw
                reasons = []
                numbers = NUMBER_RE.findall(text)
            else:
                text = raw.get("text", "")
                reasons = raw.get("reasons", [])
                numbers = raw.get("numbers", NUMBER_RE.findall(text))

            if not text:
                continue

            rows.append({
                "id": stable_id(item.get("id"), idx, text),
                "speaker": "Narendra Modi",
                "text": text,
                "source_title": item.get("title"),
                "source_url": item.get("url"),
                "source_date": item.get("published_at"),
                "reasons": reasons,
                "numbers": numbers,
            })
            if len(rows) >= limit:
                return rows
    return rows


def assess_recent_claims(discovery):
    output = []

    for claim in recent_claims(discovery):
        evidence, queries, errors = search_evidence(claim["text"], include_news=True)
        verdict, reason, enriched = assess_claim(claim["text"], evidence)

        output.append({
            **claim,
            "verdict": verdict,
            "reason_code": reason,
            "assessment_mode": "automated_nli_with_primary_source_gate" if get_nli() else "retrieval_only",
            "human_reviewed": False,
            "checked_at": now_iso(),
            "search_queries": queries,
            "search_errors": errors,
            "evidence": enriched[:8],
        })

    return output


def load_previous():
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def promise_rotation(promises, previous, limit=24):
    existing = {
        row.get("promise_id"): row
        for row in previous.get("promise_actions", [])
        if row.get("promise_id")
    }

    now = datetime.now(timezone.utc)
    slot = ((now.timetuple().tm_yday * 24) + now.hour) % 12

    # First give untracked measurable 2024 commitments a chance to enter.
    priority = [
        p for p in promises
        if p.get("election_year") == 2024
        and p.get("measurable")
        and p.get("id") not in existing
    ][:8]

    rotated = []
    for p in promises:
        key = int(hashlib.sha256(p["id"].encode()).hexdigest()[:8], 16) % 12
        if key == slot:
            rotated.append(p)

    selected = []
    seen = set()
    for p in priority + rotated:
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        selected.append(p)
        if len(selected) >= limit:
            break

    return selected, existing


def assess_promise_action(promise):
    text = promise.get("exact_text", "")
    evidence, queries, errors = search_evidence(text, include_news=True)
    official = [e for e in evidence if e["tier"] == 1]

    best = official[0] if official else None
    machine_status = "no_current_evidence_found"
    reason = "no_independent_official_match"

    if best:
        combined = f"{best.get('title','')} {best.get('excerpt','')}"
        if ACTION_RE.search(combined):
            machine_status = "action_detected"
            reason = "official_action_language_found"

            promise_numbers = normalized_number_set(text)
            evidence_numbers = normalized_number_set(combined)
            if (
                promise.get("measurable")
                and promise_numbers
                and promise_numbers & evidence_numbers
                and COMPLETION_RE.search(combined)
            ):
                machine_status = "target_evidence_detected"
                reason = "official_completion_language_with_matching_target_number"
        else:
            machine_status = "related_official_evidence_found"
            reason = "official_match_without_clear_implementation_language"

    return {
        "promise_id": promise["id"],
        "party": promise.get("party"),
        "election_year": promise.get("election_year"),
        "page": promise.get("page"),
        "exact_text": text,
        "promise_type": promise.get("promise_type"),
        "measurable": promise.get("measurable"),
        "canonical_source": promise.get("canonical_source"),
        "machine_status": machine_status,
        "reason_code": reason,
        "human_reviewed": False,
        "checked_at": now_iso(),
        "search_queries": queries,
        "search_errors": errors,
        "evidence": evidence[:6],
    }


def assess_promises(corpus, previous):
    promises = corpus.get("promises", [])
    selected, existing = promise_rotation(promises, previous)

    for promise in selected:
        existing[promise["id"]] = assess_promise_action(promise)

    rows = list(existing.values())
    rows.sort(
        key=lambda row: (
            -(row.get("election_year") or 0),
            row.get("page") or 0,
            row.get("promise_id") or "",
        )
    )
    return rows


def main():
    discovery = json.loads(DISCOVERY.read_text(encoding="utf-8")) if DISCOVERY.exists() else {}
    corpus = json.loads(PROMISES.read_text(encoding="utf-8")) if PROMISES.exists() else {}
    previous = load_previous()

    claim_assessments = assess_recent_claims(discovery)
    promise_actions = assess_promises(corpus, previous)

    verdict_counts = {}
    for claim in claim_assessments:
        verdict_counts[claim["verdict"]] = verdict_counts.get(claim["verdict"], 0) + 1

    promise_counts = {}
    for row in promise_actions:
        status = row.get("machine_status", "unknown")
        promise_counts[status] = promise_counts.get(status, 0) + 1

    payload = {
        "generated_at": now_iso(),
        "method": {
            "claim_assessment": "Automated retrieval + English NLI. Only independent official evidence can produce supported/contradicted machine verdicts. News may provide context but cannot alone determine a verdict.",
            "promise_assessment": "Rotating evidence search. Status describes detected implementation evidence; it is not a final political score or human-reviewed fulfilment judgment.",
            "nli_model": NLI_MODEL_NAME if get_nli() else None,
            "nli_error": _nli_error,
        },
        "stats": {
            "claims_checked": len(claim_assessments),
            "claim_verdicts": verdict_counts,
            "promises_with_action_records": len(promise_actions),
            "promise_machine_statuses": promise_counts,
        },
        "claims": claim_assessments,
        "promise_actions": promise_actions,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {OUT}: {len(claim_assessments)} claim checks, "
        f"{len(promise_actions)} promise action records."
    )
    if _nli_error:
        print(f"NLI warning: {_nli_error}")


if __name__ == "__main__":
    main()
