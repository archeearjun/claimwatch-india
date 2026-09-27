import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "promises" / "sources.json"
OUT = ROOT / "data" / "promises" / "corpus.json"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 ClaimWatchIndia/0.4 (+https://github.com/archeearjun/claimwatch-india)",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
})
TIMEOUT = 30

PROMISE_RE = re.compile(
    r"\b(?:we\s+will|we\s+shall|we\s+commit|we\s+are\s+committed|"
    r"will\s+ensure|will\s+provide|will\s+launch|will\s+establish|"
    r"will\s+expand|will\s+increase|will\s+continue|will\s+create|"
    r"will\s+develop|will\s+implement|will\s+make|will\s+introduce|"
    r"will\s+set\s+up|will\s+strengthen|will\s+promote|will\s+support|"
    r"will\s+work|will\s+build|will\s+bring|will\s+extend|will\s+revamp|"
    r"will\s+modernise|will\s+modernize|will\s+transform|will\s+facilitate|"
    r"aim\s+to|propose\s+to|committed\s+to)\b",
    re.I,
)

NUMBER_RE = re.compile(
    r"(?<!\w)(?:₹|Rs\.?\s*)?\d+(?:[.,]\d+)*(?:\s*(?:%|percent|crore|lakh|million|billion|trillion|km|years?|months?|days?|rupees?))?",
    re.I,
)
DEADLINE_RE = re.compile(
    r"\b(?:by|before|within|over\s+the\s+next|in\s+the\s+next)\s+(?:the\s+year\s+)?(?:20\d{2}|\d+\s+(?:years?|months?|days?))\b",
    re.I,
)
CONTINUATION_RE = re.compile(r"\b(?:continue|expand|extend|further|scale\s+up)\b", re.I)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def stable_id(*parts):
    return hashlib.sha256(
        "|".join(str(p or "") for p in parts).encode("utf-8")
    ).hexdigest()[:18]


def normalize(text):
    text = text.replace("\u00ad", "")
    text = re.sub(r"[\u2022\uf0b7\u25cf\u25aa]+", ". ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .\t\r\n")


def split_candidates(page_text):
    # Preserve sentence-sized chunks while also treating bullet/newline boundaries
    # as possible statement boundaries in design-heavy manifestos.
    text = page_text.replace("\r", "\n")
    text = re.sub(r"(?m)^\s*[•▪●■►Ÿ]+\s*", "", text)
    text = re.sub(r"\n{2,}", ". ", text)
    text = re.sub(r"\n(?=[A-Z][A-Za-z])", ". ", text)
    text = re.sub(r"\s+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)

    out = []
    for part in parts:
        part = normalize(part)
        if 30 <= len(part) <= 1400 and PROMISE_RE.search(part):
            out.append(part)
    return out


def promise_type(text):
    if NUMBER_RE.search(text) and DEADLINE_RE.search(text):
        return "numeric_target_with_deadline"
    if NUMBER_RE.search(text):
        return "numeric_or_quantified"
    if DEADLINE_RE.search(text):
        return "time_bound"
    if CONTINUATION_RE.search(text):
        return "continuation_or_expansion"
    return "policy_action"


def download_pdf(source):
    errors = []
    for url in source.get("download_urls", []):
        try:
            response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
            if response.status_code != 200:
                errors.append(f"{url}: HTTP {response.status_code}")
                continue
            content_type = response.headers.get("content-type", "").lower()
            data = response.content
            if not data.startswith(b"%PDF") and "pdf" not in content_type:
                errors.append(f"{url}: not a PDF")
                continue
            return data, url, errors
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}")
    return None, None, errors


def extract_source(source):
    data, retrieval_url, errors = download_pdf(source)
    if not data:
        return [], {
            **source,
            "retrieval_status": "failed",
            "retrieval_url": None,
            "errors": errors,
        }

    reader = PdfReader(io.BytesIO(data))
    promises = []
    seen = set()

    for page_index, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""

        for text in split_candidates(page_text):
            dedupe_key = re.sub(r"\W+", " ", text.lower()).strip()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            numbers = NUMBER_RE.findall(text)
            deadline_match = DEADLINE_RE.search(text)
            ptype = promise_type(text)

            promises.append({
                "id": f"BJP-{source['election_year']}-{stable_id(source['election_year'], page_index, text)}",
                "party": source["party"],
                "election_year": source["election_year"],
                "page": page_index,
                "exact_text": text,
                "promise_type": ptype,
                "measurable": ptype in {
                    "numeric_target_with_deadline",
                    "numeric_or_quantified",
                    "time_bound",
                },
                "numbers": numbers,
                "deadline_text": deadline_match.group(0) if deadline_match else None,
                "canonical_source": source["canonical_page"],
                "retrieval_url": retrieval_url,
                "machine_extracted": True,
                "review_status": "unreviewed",
                "implementation_status": "pending",
            })

    meta = {
        **source,
        "retrieval_status": "ok",
        "retrieval_url": retrieval_url,
        "pages": len(reader.pages),
        "extracted_promises": len(promises),
        "errors": errors,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return promises, meta


def main():
    config = json.loads(SOURCES.read_text(encoding="utf-8"))
    all_promises = []
    source_results = []

    for source in config["sources"]:
        promises, meta = extract_source(source)
        all_promises.extend(promises)
        source_results.append(meta)
        print(
            f"{source['election_year']}: {meta['retrieval_status']} "
            f"({len(promises)} promise candidates)"
        )

    all_promises.sort(key=lambda p: (p["election_year"], p["page"], p["id"]))

    payload = {
        "generated_at": now_iso(),
        "method": "Machine-extracted promise candidates from primary manifesto text. Candidate extraction is not an implementation verdict.",
        "sources": source_results,
        "stats": {
            "total": len(all_promises),
            "by_year": {
                str(year): sum(1 for p in all_promises if p["election_year"] == year)
                for year in sorted({p["election_year"] for p in all_promises})
            },
            "measurable_candidates": sum(1 for p in all_promises if p["measurable"]),
        },
        "promises": all_promises,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(all_promises)} candidates.")


if __name__ == "__main__":
    main()
