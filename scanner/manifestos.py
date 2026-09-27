import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "corpus" / "manifesto_sources.json"
OUT_PATH = ROOT / "data" / "promises" / "candidates.json"
TIMEOUT = 45

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "ClaimWatchIndia/0.4 (+https://github.com/archeearjun/claimwatch-india)",
    "Accept": "application/pdf,*/*;q=0.8",
})

STOPWORDS = {
    "the","a","an","and","or","of","to","in","for","on","with","by","from","as","at","that","this",
    "is","are","be","been","being","we","our","will","shall","would","can","could","may","more","all",
    "its","their","they","it","into","through","across","over","under","such","these","those","also",
    "ensure","provide","promote","make","take","work","continue","develop","establish","create","new",
    "india","indian","government","bjp","bharat","people","country","national"
}

COMMITMENT_RE = re.compile(
    r"\b(?:we\s+will|we\s+shall|we\s+are\s+committed|we\s+commit|our\s+government\s+will|"
    r"bjp\s+will|will\s+ensure|will\s+provide|will\s+launch|will\s+establish|will\s+expand|"
    r"will\s+continue|will\s+make|will\s+work|will\s+develop|will\s+introduce|will\s+set\s+up|"
    r"aim\s+to|target\s+to|committed\s+to|promise\s+to|pledge\s+to|shall\s+ensure)\b",
    re.I,
)

ACTION_RE = re.compile(
    r"\b(?:build|construct|provide|deliver|create|generate|increase|reduce|double|triple|expand|"
    r"establish|launch|implement|complete|connect|cover|achieve|raise|lower|universalise|"
    r"modernise|modernize|digitise|digitize|strengthen|extend|eliminate|eradicate|ensure)\b",
    re.I,
)

NUMBER_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|percent|crore|lakh|million|billion|trillion|km|mw|gw|"
    r"years?|months?|days?|houses?|jobs?|schools?|hospitals?|colleges?|universities?)?\b",
    re.I,
)

YEAR_RE = re.compile(r"\\b(?:19|20)\\d{2}\\b")

DEADLINE_RE = re.compile(
    r"\b(?:by\s+(?:20\d{2}|the\s+year\s+20\d{2})|within\s+\d+\s+(?:years?|months?)|"
    r"next\s+(?:five|5|ten|10)\s+years?|before\s+20\d{2}|20\d{2})\b",
    re.I,
)

CATEGORY_RULES = {
    "jobs_economy": ["job","employment","employ","economy","economic","manufactur","msme","startup","industry","business","trade","gdp","income"],
    "agriculture": ["farmer","farm","agriculture","crop","irrigation","mandi","fertil","rural"],
    "health_welfare": ["health","hospital","doctor","insurance","ayushman","nutrition","welfare","pension","poor","housing","toilet","sanitation"],
    "education_skills": ["education","school","college","university","skill","training","student","teacher","research"],
    "infrastructure": ["road","rail","railway","highway","airport","port","metro","infrastructure","electricity","power","water","housing","urban"],
    "governance": ["governance","corruption","administration","judicial","police","tax","transparency","federal","panchayat","election"],
    "security_defence": ["security","defence","defense","terror","border","armed","military","nuclear","crime"],
    "women_social_justice": ["women","woman","girl","sc ","st ","obc","minority","tribal","dalit","social justice","disabled","divyang"],
    "technology_digital": ["digital","technology","science","semiconductor","ai ","artificial intelligence","internet","broadband","space"],
    "environment": ["environment","climate","renewable","solar","forest","river","pollution","green","wildlife"],
    "culture_heritage": ["culture","heritage","tourism","temple","pilgrimage","language","civilisation","civilization"],
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def stable_id(*parts):
    return hashlib.sha256("|".join(str(x) for x in parts).encode("utf-8")).hexdigest()[:18]

def normalize_text(text):
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-\s+(?=\w)", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def split_sentences(text):
    text = normalize_text(text)
    chunks = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])|\s+[•▪●]\s+", text)
    return [c.strip(" •▪●\t") for c in chunks if 35 <= len(c.strip()) <= 1300]

def tokens(text):
    return [
        t for t in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower())
        if t not in STOPWORDS
    ]

def key_terms(text, limit=7):
    counts = Counter(tokens(text))
    return [word for word, _ in counts.most_common(limit)]

def category_for(text):
    low = text.lower()
    scores = {}
    for category, needles in CATEGORY_RULES.items():
        score = sum(1 for n in needles if n in low)
        if score:
            scores[category] = score
    return max(scores, key=scores.get) if scores else "other"

def anchor(text, max_words=14):
    words = normalize_text(text).split()
    clipped = words[:max_words]
    result = " ".join(clipped)
    if len(words) > max_words:
        result += " …"
    return result

def extract_deadlines(text):
    found = []
    for match in DEADLINE_RE.finditer(text):
        value = match.group(0).strip()
        if value not in found:
            found.append(value)
    return found[:3]

def extract_numbers(text):
    """Return target-like numeric expressions, excluding bare calendar years."""
    found = []
    for match in NUMBER_RE.finditer(text):
        value = match.group(0).strip()
        if YEAR_RE.fullmatch(value):
            continue
        if value not in found:
            found.append(value)
    return found[:5]

def extract_years(text):
    found = []
    for value in YEAR_RE.findall(text):
        if value not in found:
            found.append(value)
    return found[:5]

def candidate_score(sentence):
    score = 0
    if COMMITMENT_RE.search(sentence):
        score += 5
    if ACTION_RE.search(sentence):
        score += 2
    if extract_numbers(sentence):
        score += 2
    if DEADLINE_RE.search(sentence):
        score += 2
    if len(sentence) > 500:
        score -= 1
    return score

def jaccard(a, b):
    aa, bb = set(a), set(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)

def parse_manifesto(source):
    # Some official sites throttle automated runners.  When an explicitly
    # registered fallback points to the same official document, try that copy
    # first while preserving the canonical BJP URL as provenance.
    fallbacks = source.get("fallback_pdf_urls") or []
    urls = [*fallbacks, source["pdf_url"]] if fallbacks else [source["pdf_url"]]
    pdf_bytes = None
    download_url_used = None
    failures = []

    for url in urls:
        try:
            response = SESSION.get(url, timeout=(15, 55))
            response.raise_for_status()
            candidate = response.content
            if not candidate.startswith(b"%PDF"):
                raise ValueError("response was not a PDF")
            pdf_bytes = candidate
            download_url_used = url
            break
        except Exception as exc:
            failures.append(f"{url}: {type(exc).__name__}")

    if pdf_bytes is None:
        raise RuntimeError("all manifesto download URLs failed: " + " | ".join(failures))

    sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    reader = PdfReader(io.BytesIO(pdf_bytes))

    rows = []
    seen = set()

    for page_index, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        for sentence in split_sentences(raw):
            score = candidate_score(sentence)
            if score < 5:
                continue

            norm = " ".join(tokens(sentence))
            if len(norm) < 25:
                continue
            fingerprint = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:20]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)

            terms = key_terms(sentence)
            deadlines = extract_deadlines(sentence)
            numbers = extract_numbers(sentence)
            historical_years = extract_years(sentence)
            measurable = bool(numbers or deadlines)

            rows.append({
                "id": f"BJP-{source['year']}-{stable_id(page_index, fingerprint)}",
                "year": source["year"],
                "category": category_for(sentence),
                "page": page_index,
                "anchor": anchor(sentence),
                "keywords": terms,
                "numbers": numbers,
                "deadline_hints": deadlines,
                "historical_years": historical_years,
                "measurable": measurable,
                "candidate_score": score,
                "source_url": source["landing_url"],
                "pdf_url": source["pdf_url"],
                "source_title": source["title"],
                "source_text_hash": fingerprint,
            })

    rows.sort(key=lambda r: (-r["candidate_score"], r["page"], r["id"]))
    return rows, {
        "year": source["year"],
        "title": source["title"],
        "landing_url": source["landing_url"],
        "pdf_url": source["pdf_url"],
        "download_url_used": download_url_used,
        "used_fallback": download_url_used != source["pdf_url"],
        "pdf_sha256": sha256,
        "pages": len(reader.pages),
        "candidate_count": len(rows),
    }

def link_related(promises):
    by_year = defaultdict(list)
    for p in promises:
        by_year[p["year"]].append(p)

    years = sorted(by_year)
    for promise in promises:
        related = []
        for year in years:
            if year == promise["year"]:
                continue
            best = None
            best_score = 0.0
            for other in by_year[year]:
                if other["category"] != promise["category"]:
                    continue
                score = jaccard(promise["keywords"], other["keywords"])
                if score > best_score:
                    best_score = score
                    best = other
            overlap = (
                set(promise["keywords"]) & set(best["keywords"])
                if best else set()
            )
            if best and best_score >= 0.20 and len(overlap) >= 2:
                related.append({
                    "id": best["id"],
                    "year": best["year"],
                    "similarity": round(best_score, 3),
                    "matched_terms": sorted(overlap)[:6],
                })
        promise["related_promises"] = sorted(related, key=lambda x: -x["similarity"])[:3]

def main():
    registry = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    all_promises = []
    source_meta = []
    errors = []

    for source in registry.get("sources", []):
        try:
            rows, meta = parse_manifesto(source)
            all_promises.extend(rows)
            source_meta.append(meta)
            print(f"{source['year']}: {len(rows)} promise candidates")
        except Exception as exc:
            errors.append({
                "year": source.get("year"),
                "source": source.get("pdf_url"),
                "error": type(exc).__name__,
                "detail": str(exc)[:200],
            })

    link_related(all_promises)

    by_year = Counter(str(p["year"]) for p in all_promises)
    by_category = Counter(p["category"] for p in all_promises)
    payload = {
        "generated_at": now_iso(),
        "sources": source_meta,
        "summary": {
            "total": len(all_promises),
            "by_year": dict(sorted(by_year.items())),
            "by_category": dict(sorted(by_category.items())),
            "measurable": sum(1 for p in all_promises if p["measurable"]),
            "with_deadline": sum(1 for p in all_promises if p["deadline_hints"]),
            "related_links": sum(len(p.get("related_promises", [])) for p in all_promises),
        },
        "promises": all_promises,
        "errors": errors,
        "notes": [
            "These are machine-extracted promise candidates, not claims that a promise was fulfilled or broken.",
            "The repository stores short anchors plus page/hash metadata rather than reproducing manifesto pages.",
            "Implementation status requires separate current evidence.",
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH} with {len(all_promises)} candidates and {len(errors)} errors.")

if __name__ == "__main__":
    main()
