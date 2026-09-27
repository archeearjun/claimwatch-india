import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

REPO = "https://github.com/archeearjun/claimwatch-india"
USER_AGENT = f"ClaimWatchIndia/0.2 (+{REPO})"
OUT = Path("data/discovery/latest.json")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
TIMEOUT = 18

CHECKABLE_RE = re.compile(
    r"(\b\d+(?:\.\d+)?\s*(?:%|percent|crore|lakh|million|billion|km|years?|months?|days?)?\b"
    r"|\b(?:19|20)\d{2}\b"
    r"|\b(?:doubled|tripled|increased|decreased|reduced|highest|lowest|more than|less than|only|never|always)\b"
    r"|\b(?:created|built|provided|delivered|achieved|completed|launched|opened|closed|added|removed)\b)",
    re.I,
)

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def stable_id(*parts):
    return hashlib.sha256("|".join(str(p or "") for p in parts).encode("utf-8")).hexdigest()[:20]

def split_sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return [s.strip() for s in re.split(r"(?<=[.!?।])\s+", text) if 20 <= len(s.strip()) <= 700]

def candidate_claims(text, limit=12):
    rows = []
    for sentence in split_sentences(text):
        if CHECKABLE_RE.search(sentence):
            # Keep only a short source fragment in the discovery feed.
            rows.append(sentence[:280])
        if len(rows) >= limit:
            break
    return rows

def fetch(url, **kwargs):
    return SESSION.get(url, timeout=TIMEOUT, **kwargs)

def robots_allowed(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        r = fetch(robots_url)
        if r.status_code >= 400:
            return True
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(r.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        return True

def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body
    if not node:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()

def read_article_for_claims(url):
    if not robots_allowed(url):
        return [], "robots_disallowed"
    try:
        r = fetch(url)
        if r.status_code != 200:
            return [], f"http_{r.status_code}"
        ctype = r.headers.get("content-type", "")
        if "text/html" not in ctype:
            return [], "not_html"
        return candidate_claims(visible_text(r.text)), "read"
    except requests.RequestException as exc:
        return [], f"fetch_error:{type(exc).__name__}"

def scan_gdelt():
    params = {
        "query": '("Narendra Modi" OR BJP OR "Bharatiya Janata Party")',
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 50,
        "sort": "datedesc",
        "timespan": "1h",
    }
    try:
        r = SESSION.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles", [])
    except Exception as exc:
        return [], [{"source": "gdelt", "error": type(exc).__name__}]

    out = []
    # Read only a small bounded subset each run; metadata is kept for the rest.
    read_budget = 8
    for a in articles:
        url = a.get("url")
        if not url:
            continue
        claims, read_status = ([], "metadata_only")
        if read_budget > 0:
            claims, read_status = read_article_for_claims(url)
            read_budget -= 1
        out.append({
            "id": stable_id("news", url),
            "kind": "news",
            "source": a.get("domain") or "GDELT",
            "title": a.get("title"),
            "url": url,
            "published_at": a.get("seendate"),
            "language": a.get("language"),
            "source_country": a.get("sourcecountry"),
            "read_status": read_status,
            "candidate_claims": claims,
        })
    return out, []

def scan_pmindia():
    pages = [
        "https://www.pmindia.gov.in/en/pms-speeches/",
        "https://www.pmindia.gov.in/en/speeches/",
    ]
    found = {}
    errors = []
    for page in pages:
        try:
            r = fetch(page)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
                href = urljoin(page, a["href"])
                if len(title) < 18 or "pmindia.gov.in" not in urlparse(href).netloc:
                    continue
                low = title.lower()
                if not any(k in low for k in ("speech", "address", "remarks", "statement", "mann ki baat")):
                    continue
                found[href] = title
        except Exception as exc:
            errors.append({"source": page, "error": type(exc).__name__})

    out = []
    for url, title in list(found.items())[:30]:
        claims, read_status = read_article_for_claims(url)
        out.append({
            "id": stable_id("pmindia", url),
            "kind": "official_speech_or_video",
            "source": "PMIndia",
            "title": title,
            "url": url,
            "published_at": None,
            "read_status": read_status,
            "candidate_claims": claims,
            "transcript_strategy": "publisher_text_if_available_else_local_audio_transcription",
        })
    return out, errors

def scan_youtube():
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return [], [{"source": "youtube", "error": "YOUTUBE_API_KEY_not_configured"}]

    published_after = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    queries = ["Narendra Modi speech rally", "BJP rally speech"]
    out, errors = [], []
    for q in queries:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "maxResults": 20,
            "publishedAfter": published_after,
            "q": q,
            "key": key,
        }
        try:
            r = SESSION.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=TIMEOUT)
            r.raise_for_status()
            for row in r.json().get("items", []):
                vid = row.get("id", {}).get("videoId")
                sn = row.get("snippet", {})
                if not vid:
                    continue
                out.append({
                    "id": stable_id("youtube", vid),
                    "kind": "youtube_video",
                    "source": "YouTube",
                    "video_id": vid,
                    "channel_id": sn.get("channelId"),
                    "channel_title": sn.get("channelTitle"),
                    "title": sn.get("title"),
                    "description": (sn.get("description") or "")[:500],
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "published_at": sn.get("publishedAt"),
                    "candidate_claims": [],
                    "transcript_status": "queued",
                    "transcript_strategy": "official_or_publisher_transcript_else_browser_local_whisper",
                })
        except Exception as exc:
            errors.append({"source": "youtube", "query": q, "error": type(exc).__name__})
    return out, errors

def dedupe(items):
    seen, out = set(), []
    for item in items:
        key = item.get("url") or item.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

def main():
    items, errors = [], []
    for fn in (scan_pmindia, scan_gdelt, scan_youtube):
        rows, errs = fn()
        items.extend(rows)
        errors.extend(errs)

    payload = {
        "generated_at": now_iso(),
        "scope": "Initial research scope: Narendra Modi / BJP public statements and commitments",
        "items": dedupe(items),
        "errors": errors,
        "notes": [
            "Discovery is not a truth verdict.",
            "News text is read transiently for candidate-claim extraction; the feed stores URLs and short fragments, not full articles.",
            "YouTube caption download is not assumed to be available for arbitrary public videos.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT} with {len(payload['items'])} items and {len(errors)} scanner notices.")

if __name__ == "__main__":
    main()
