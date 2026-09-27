import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

import feedparser
import requests
from bs4 import BeautifulSoup

OUT = Path("data/discovery/latest.json")
TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ClaimWatchIndia/0.4"
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

NUMBER_RE = re.compile(
    r"(?<!\w)(?:₹|Rs\.?\s*)?\d+(?:[.,]\d+)*(?:\s*(?:%|percent|crore|lakh|million|billion|trillion|km|years?|months?|days?|rupees?|dollars?))?",
    re.I,
)
RANK_RE = re.compile(
    r"\b(?:largest|smallest|highest|lowest|fastest|third largest|top\s*\d+|number\s*one|no\.\s*\d+)\b",
    re.I,
)
COMPARISON_RE = re.compile(
    r"\b(?:doubled|tripled|increased|decreased|reduced|rose|fell|grew|declined|more than|less than|nearly|around|about|record|highest|lowest)\b",
    re.I,
)
ACCOMPLISHMENT_RE = re.compile(
    r"\b(?:created|built|provided|delivered|achieved|completed|launched|opened|closed|connected|covered|added|removed|signed|approved|implemented|established|joined|received)\b",
    re.I,
)
FUTURE_RE = re.compile(
    r"\b(?:we will|will be|will become|will make|will ensure|will provide|will launch|will create|will develop|will establish|will expand|will increase|will continue|aim to|target|goal|by\s+20\d{2})\b",
    re.I,
)
SUBJECTIVE_RE = re.compile(
    r"\b(?:i believe|i think|i feel|affection|love|trust|warmth|happiness|proud|great|wonderful|historic|immense|aspiration|hope|confidence)\b",
    re.I,
)
QUESTION_RE = re.compile(r"\?$")
BOILERPLATE_RE = re.compile(
    r"\b(?:click here|view more|share this|download|subscribe|follow us|copyright|privacy policy|news updates)\b",
    re.I,
)

YOUTUBE_CHANNELS = [
    ("Narendra Modi", "UC1NF71EwP41VdjAU1iXdLkw"),
    ("PMO India", "UCDS9hpqUEXsXUIcf0qDcBIA"),
    ("Bharatiya Janata Party", "UCrwE8kVqtIUVUzKui2WVpuQ"),
]


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


def sanitize_text(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]{1,500}>", " ", text)
    text = re.sub(r"&#?\w+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text):
    text = sanitize_text(text)
    rows = re.split(r"(?<=[.!?।])\s+", text)
    return [
        sentence.strip()
        for sentence in rows
        if 25 <= len(sentence.strip()) <= 1000
    ]


def classify_sentence(sentence):
    lower = sentence.lower()
    if "data-mce-type" in lower or "mce_selres" in lower or "65279" in lower:
        return "ignore", []

    numbers = NUMBER_RE.findall(sentence)
    has_number = bool(numbers)
    has_rank = bool(RANK_RE.search(sentence))
    has_comparison = bool(COMPARISON_RE.search(sentence))
    has_accomplishment = bool(ACCOMPLISHMENT_RE.search(sentence))
    has_future = bool(FUTURE_RE.search(sentence))
    subjective = bool(SUBJECTIVE_RE.search(sentence))
    boilerplate = bool(BOILERPLATE_RE.search(sentence))
    question = bool(QUESTION_RE.search(sentence))

    if boilerplate or question:
        return "ignore", []

    reasons = []
    if has_number:
        reasons.append("number")
    if has_rank:
        reasons.append("rank")
    if has_comparison:
        reasons.append("comparison")
    if has_accomplishment:
        reasons.append("accomplishment")

    # Explicit commitments are routed to the promise matcher, not the factual
    # truth checker, unless the same sentence also contains a current/past fact.
    if has_future and not (has_accomplishment or has_comparison):
        return "promise", reasons + ["future_commitment"]

    # Require at least one objective hook. A date/goal alone is not enough.
    objective = has_number or has_rank or has_comparison or has_accomplishment
    if not objective:
        return "ignore", []

    # Subjective rhetoric is excluded unless there is an independently
    # checkable quantitative/accomplishment proposition.
    if subjective and not (has_number or has_accomplishment or has_rank):
        return "ignore", []

    # "2047 goal" and similar aspirations are not factual truth claims.
    if has_future and ("goal" in lower or "aim" in lower or "target" in lower):
        return "promise", reasons + ["future_commitment"]

    return "claim", reasons


def extract_candidates(text, claim_limit=25, promise_limit=12):
    claims, promises = [], []

    for sentence in split_sentences(text):
        kind, reasons = classify_sentence(sentence)
        row = {
            "text": sentence[:700],
            "reasons": reasons,
            "numbers": NUMBER_RE.findall(sentence),
        }
        if kind == "claim" and len(claims) < claim_limit:
            claims.append(row)
        elif kind == "promise" and len(promises) < promise_limit:
            promises.append(row)

    return claims, promises


def parse_date(value):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def get_feed(url, params=None):
    if params:
        url = f"{url}?{urlencode(params)}"
    response = SESSION.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return feedparser.parse(response.content), response.url


def with_query(url, **params):
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query))
    query.update({k: v for k, v in params.items() if v is not None})
    return urlunparse(parsed._replace(query=urlencode(query)))


def extract_page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"]):
        tag.decompose()

    candidates = []
    selectors = [
        "article",
        ".entry-content",
        ".news-content",
        ".news-detail",
        ".news-bg",
        ".content-area",
        "main",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            text = sanitize_text(node.get_text(" ", strip=True))
            if 300 <= len(text) <= 100000:
                candidates.append(text)

    if candidates:
        # The largest meaningful article/main block usually contains the full
        # speech and avoids short cards from the same page.
        return max(candidates, key=len)

    if soup.body:
        return sanitize_text(soup.body.get_text(" ", strip=True))
    return ""


def fetch_pmindia_transcript(url):
    # PMIndia exposes an English rendering with this query flag on speech pages.
    candidate_urls = [
        with_query(url, comment="disable"),
        url,
    ]
    last_error = None

    for target in candidate_urls:
        try:
            response = SESSION.get(target, timeout=TIMEOUT)
            if response.status_code != 200:
                last_error = f"http_{response.status_code}"
                continue
            text = extract_page_text(response.text)
            if len(text) >= 500:
                return text, target, "full_page"
            last_error = "page_text_too_short"
        except Exception as exc:
            last_error = type(exc).__name__

    return "", url, last_error or "unavailable"


def entry_text(entry):
    chunks = []
    for content in entry.get("content", []) or []:
        chunks.append(content.get("value", ""))
    chunks.append(entry.get("summary", ""))
    chunks.append(entry.get("description", ""))
    return clean_html(" ".join(filter(None, chunks)))


def scan_pmindia():
    feed_url = "https://www.pmindia.gov.in/en/tag/pmspeech/feed/"
    try:
        feed, _ = get_feed(feed_url)
    except Exception as exc:
        return [], [{"source": "PMIndia RSS", "error": type(exc).__name__}]

    rows, errors = [], []

    # Limit full-page fetches so each hourly run is cheap and respectful.
    for entry in feed.entries[:12]:
        url = entry.get("link")
        if not url:
            continue

        rss_text = entry_text(entry)
        transcript, transcript_url, transcript_status = fetch_pmindia_transcript(url)
        source_text = transcript or rss_text
        claims, promises = extract_candidates(source_text)

        if transcript_status not in {"full_page"}:
            errors.append({
                "source": "PMIndia transcript",
                "url": url,
                "error": transcript_status,
            })

        rows.append({
            "id": stable_id("pmindia", url),
            "kind": "official_speech_or_video",
            "source": "PMIndia",
            "title": clean_html(entry.get("title", "PM speech")),
            "url": url,
            "published_at": parse_date(entry.get("published")),
            "candidate_claims": claims,
            "candidate_promises": promises,
            "transcript_status": transcript_status,
            "transcript_url": transcript_url,
            "transcript_strategy": "publisher_text_first_else_local_audio_transcription",
        })

    return rows, errors


def scan_google_news():
    queries = [
        '"Narendra Modi"',
        '"Bharatiya Janata Party" OR BJP',
    ]

    rows, errors = [], []
    for query in queries:
        try:
            feed, _ = get_feed(
                "https://news.google.com/rss/search",
                {
                    "q": query,
                    "hl": "en-IN",
                    "gl": "IN",
                    "ceid": "IN:en",
                },
            )
        except Exception as exc:
            errors.append({
                "source": "Google News RSS",
                "query": query,
                "error": type(exc).__name__,
            })
            continue

        for entry in feed.entries[:30]:
            url = entry.get("link")
            if not url:
                continue
            source = "Google News"
            if entry.get("source") and entry.source.get("title"):
                source = entry.source.get("title")

            text = " ".join([
                clean_html(entry.get("title", "")),
                entry_text(entry),
            ]).strip()
            claims, promises = extract_candidates(text, claim_limit=4, promise_limit=2)

            rows.append({
                "id": stable_id("news", url),
                "kind": "news",
                "source": source,
                "title": clean_html(entry.get("title", "News result")),
                "url": url,
                "published_at": parse_date(entry.get("published")),
                "candidate_claims": claims,
                "candidate_promises": promises,
                "read_status": "rss_discovery",
            })

    return rows, errors


def scan_youtube_channel_feeds():
    rows, errors = [], []

    for channel_title, channel_id in YOUTUBE_CHANNELS:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        try:
            feed, _ = get_feed(url)
        except Exception as exc:
            errors.append({
                "source": f"YouTube RSS: {channel_title}",
                "error": type(exc).__name__,
            })
            continue

        for entry in feed.entries[:20]:
            video_id = entry.get("yt_videoid") or entry.get("id", "").split(":")[-1]
            watch_url = entry.get("link") or (
                f"https://www.youtube.com/watch?v={video_id}" if video_id else None
            )
            if not watch_url:
                continue

            description = entry_text(entry)
            claims, promises = extract_candidates(description, claim_limit=3, promise_limit=2)
            rows.append({
                "id": stable_id("youtube", video_id or watch_url),
                "kind": "youtube_video",
                "source": "YouTube",
                "video_id": video_id,
                "channel_id": channel_id,
                "channel_title": channel_title,
                "title": clean_html(entry.get("title", "YouTube video")),
                "description": description[:500],
                "url": watch_url,
                "published_at": parse_date(entry.get("published")),
                "candidate_claims": claims,
                "candidate_promises": promises,
                "transcript_status": "match_to_publisher_text_or_transcribe_locally",
                "transcript_strategy": "official_publisher_text_else_browser_local_whisper",
            })

    return rows, errors


def scan_optional_youtube_search():
    key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not key:
        return [], []

    rows, errors = [], []
    queries = ["Narendra Modi speech rally", "BJP rally speech"]

    for query in queries:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "maxResults": 20,
            "q": query,
            "key": key,
        }
        try:
            response = SESSION.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params,
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            errors.append({
                "source": "YouTube search API",
                "query": query,
                "error": type(exc).__name__,
            })
            continue

        for row in data.get("items", []):
            video_id = row.get("id", {}).get("videoId")
            snippet = row.get("snippet", {})
            if not video_id:
                continue
            rows.append({
                "id": stable_id("youtube", video_id),
                "kind": "youtube_video",
                "source": "YouTube",
                "video_id": video_id,
                "channel_id": snippet.get("channelId"),
                "channel_title": snippet.get("channelTitle"),
                "title": clean_html(snippet.get("title", "")),
                "description": clean_html(snippet.get("description", ""))[:500],
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": snippet.get("publishedAt"),
                "candidate_claims": [],
                "candidate_promises": [],
                "transcript_status": "match_to_publisher_text_or_transcribe_locally",
                "transcript_strategy": "official_publisher_text_else_browser_local_whisper",
            })

    return rows, errors


def dedupe(items):
    seen, out = set(), []
    for item in items:
        key = item.get("url") or item.get("id")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def sort_key(item):
    return item.get("published_at") or ""


def main():
    items, errors = [], []

    for scanner in (
        scan_pmindia,
        scan_google_news,
        scan_youtube_channel_feeds,
        scan_optional_youtube_search,
    ):
        rows, notices = scanner()
        items.extend(rows)
        errors.extend(notices)

    items = dedupe(items)
    items.sort(key=sort_key, reverse=True)

    payload = {
        "generated_at": now_iso(),
        "scope": "Initial research scope: Narendra Modi / BJP public statements and commitments",
        "items": items[:120],
        "errors": errors,
        "notes": [
            "Discovery is not a truth verdict.",
            "PMIndia full publisher text is preferred over generated transcription when available.",
            "Core YouTube discovery uses public channel feeds and requires no API key.",
            "A YouTube API key is optional and only expands discovery beyond the core channels.",
            "News RSS is discovery/context evidence, not proof of a political claim by itself.",
        ],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {OUT} with {len(payload['items'])} items "
        f"and {len(errors)} scanner notices."
    )


if __name__ == "__main__":
    main()
