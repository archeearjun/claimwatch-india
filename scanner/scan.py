import hashlib
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

import feedparser
import requests
from bs4 import BeautifulSoup

OUT = Path("data/discovery/latest.json")
TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 ClaimWatchIndia/0.3"
)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

CHECKABLE_RE = re.compile(
    r"(\b\d+(?:\.\d+)?\s*(?:%|percent|crore|lakh|million|billion|km|years?|months?|days?)?\b"
    r"|\b(?:19|20)\d{2}\b"
    r"|\b(?:doubled|tripled|increased|decreased|reduced|highest|lowest|more than|less than|only|never|always)\b"
    r"|\b(?:created|built|provided|delivered|achieved|completed|launched|opened|closed|added|removed)\b)",
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


def split_sentences(text):
    text = re.sub(r"\s+", " ", text or "").strip()
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?।])\s+", text)
        if 20 <= len(sentence.strip()) <= 900
    ]


BOILERPLATE_RE = re.compile(
    r"(subscribe|follow\s+(?:us|pm)|social\s+media|facebook\.com|twitter\.com|x\.com|"
    r"instagram\.com|linkedin\.com|youtube\.com/@|whatsapp\.com|download\s+the\s+app)",
    re.I,
)

def candidate_claims(text, limit=10):
    rows = []
    seen = set()
    for sentence in split_sentences(text):
        # Remove link-heavy channel boilerplate before claim extraction.
        clean = re.sub(r"https?://\S+|www\.\S+", " ", sentence)
        clean = re.sub(r"\s+", " ", clean).strip(" -–—|►👉🔔")
        if len(clean) < 25 or BOILERPLATE_RE.search(clean):
            continue
        if len(re.findall(r"[A-Za-z\u0900-\u097F]{2,}", clean)) < 5:
            continue
        if not CHECKABLE_RE.search(clean):
            continue
        key = re.sub(r"\W+", " ", clean.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        rows.append(clean[:320])
        if len(rows) >= limit:
            break
    return rows


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

    rows = []
    for entry in feed.entries[:30]:
        url = entry.get("link")
        if not url:
            continue
        text = entry_text(entry)
        rows.append({
            "id": stable_id("pmindia", url),
            "kind": "official_speech_or_video",
            "source": "PMIndia",
            "title": clean_html(entry.get("title", "PM speech")),
            "url": url,
            "published_at": parse_date(entry.get("published")),
            "candidate_claims": candidate_claims(text),
            "transcript_status": "publisher_text_available" if text else "publisher_text_missing",
            "transcript_strategy": "publisher_text_first_else_local_audio_transcription",
        })

    return rows, []


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
            rows.append({
                "id": stable_id("news", url),
                "kind": "news",
                "source": source,
                "title": clean_html(entry.get("title", "News result")),
                "url": url,
                "published_at": parse_date(entry.get("published")),
                "candidate_claims": candidate_claims(text, limit=4),
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
                "candidate_claims": candidate_claims(description, limit=3),
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
    value = item.get("published_at") or ""
    return value


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
        "items": items[:100],
        "errors": errors,
        "notes": [
            "Discovery is not a truth verdict.",
            "PMIndia publisher text is preferred over generated transcription when available.",
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
