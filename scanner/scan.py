import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl
from urllib.robotparser import RobotFileParser

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
    r"\b(?:we will|bjp will|government will|our government will|"
    r"will make|will ensure|will provide|will launch|will create|will develop|"
    r"will establish|will expand|will increase|will continue|will implement|"
    r"will introduce|will set up|aim to|target|goal|by\s+20\d{2})\b",
    re.I,
)
SUBJECTIVE_RE = re.compile(
    r"\b(?:i believe|i think|i feel|affection|love|trust|warmth|happiness|proud|great|wonderful|historic|immense|aspiration|hope|confidence)\b",
    re.I,
)
QUESTION_RE = re.compile(r"\?$")
BOILERPLATE_RE = re.compile(
    r"\b(?:click here|view more|share this|download|subscribe|follow us|copyright|privacy policy|news updates|updated at|updated -|published at|read later|see all)\b",
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
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", " ", text)
    text = re.sub(r"\b\d{3,4}[\s-]+\d{3}[\s-]+\d{4}\b", " ", text)
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
    sentences = split_sentences(text)
    anaphora = re.compile(
        r"\b(?:this scheme|this programme|this program|this initiative|"
        r"under this|through this|with its help|under it|through it|"
        r"this mission|this yojana|this policy)\b",
        re.I,
    )

    for index, sentence in enumerate(sentences):
        kind, reasons = classify_sentence(sentence)
        context_before = ""
        if index > 0 and anaphora.search(sentence):
            context_before = sentences[index - 1][-500:]

        row = {
            "text": sentence[:700],
            "reasons": reasons,
            "numbers": NUMBER_RE.findall(sentence),
            "context_before": context_before,
        }
        if kind == "claim" and len(claims) < claim_limit:
            claims.append(row)
        elif kind == "promise" and len(promises) < promise_limit:
            promises.append(row)

    return claims, promises


def candidate_claims(text, limit=12):
    """Backward-compatible string view used by tests and small callers."""
    claims, _ = extract_candidates(text, claim_limit=max(limit * 2, limit), promise_limit=0)
    out = []
    seen = set()
    for row in claims:
        key = re.sub(r"\W+", " ", row["text"].lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row["text"])
        if len(out) >= limit:
            break
    return out


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


def robots_allowed(url):
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    try:
        response = SESSION.get(
            f"{parsed.scheme}://{parsed.netloc}/robots.txt",
            timeout=7,
        )
        if response.status_code >= 400:
            return True
        robot = RobotFileParser()
        robot.parse(response.text.splitlines())
        return robot.can_fetch(USER_AGENT, url)
    except Exception:
        return True


def fetch_news_article(url):
    if not robots_allowed(url):
        return "", "robots_disallowed"
    try:
        response = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            return "", f"http_{response.status_code}"
        ctype = response.headers.get("content-type", "").lower()
        if "html" not in ctype:
            return "", "non_html"
        text = extract_page_text(response.text[:4_000_000])
        if len(text) < 400:
            return "", "page_text_too_short"
        return text, "full_page"
    except Exception as exc:
        return "", f"fetch_error:{type(exc).__name__}"


def scan_bing_news():
    queries = [
        '"Narendra Modi"',
        '"Bharatiya Janata Party" BJP',
    ]
    rows, errors = [], []
    read_budget = 8

    for query in queries:
        try:
            response = SESSION.get(
                "https://www.bing.com/news/search",
                params={"q": query, "format": "rss"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            feed = feedparser.parse(response.content)
        except Exception as exc:
            errors.append({
                "source": "Bing News RSS",
                "query": query,
                "error": type(exc).__name__,
            })
            continue

        for entry in feed.entries[:20]:
            url = entry.get("link")
            if not url:
                continue

            metadata_text = " ".join([
                clean_html(entry.get("title", "")),
                entry_text(entry),
            ]).strip()

            article_text = ""
            read_status = "rss_discovery"
            if read_budget > 0:
                article_text, read_status = fetch_news_article(url)
                read_budget -= 1

            source_text = article_text or metadata_text
            claims, promises = extract_candidates(
                source_text,
                claim_limit=5,
                promise_limit=2,
            )
            rows.append({
                "id": stable_id("bing_news", url),
                "kind": "news",
                "source": urlparse(url).netloc.replace("www.", "") or "Bing News",
                "title": clean_html(entry.get("title", "News result")),
                "url": url,
                "published_at": parse_date(entry.get("published")),
                "candidate_claims": claims,
                "candidate_promises": promises,
                "read_status": read_status,
                "body_read": bool(article_text),
            })

    return rows, errors


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


def resolve_news_article(title, publisher_url=""):
    clean_title = clean_html(title)
    if not clean_title:
        return ""

    # Strip the common " - Publisher" suffix before exact-title search.
    search_title = re.sub(r"\s+-\s+[^-]{2,80}$", "", clean_title).strip()
    publisher_host = urlparse(publisher_url or "").netloc.replace("www.", "")
    query = f'"{search_title[:180]}"'
    if publisher_host:
        query += f" site:{publisher_host}"

    try:
        response = SESSION.get(
            "https://www.bing.com/search",
            params={"q": query, "format": "rss", "count": "5"},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except Exception:
        return ""

    for entry in feed.entries[:5]:
        url = entry.get("link") or ""
        if not url:
            continue

        parsed = urlparse(url)
        host = parsed.netloc.replace("www.", "")
        if host.endswith("bing.com"):
            target = dict(parse_qsl(parsed.query)).get("url") or ""
            if target:
                url = target
                host = urlparse(url).netloc.replace("www.", "")

        if publisher_host and not (
            host == publisher_host
            or host.endswith("." + publisher_host)
            or publisher_host.endswith("." + host)
        ):
            continue
        return url

    return ""


def scan_google_news():
    queries = [
        '"Narendra Modi"',
        '"Bharatiya Janata Party" OR BJP',
    ]

    rows, errors = [], []
    article_read_budget = 8
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
            aggregator_url = entry.get("link")
            if not aggregator_url:
                continue

            source = "Google News"
            publisher_url = ""
            if entry.get("source"):
                source = entry.source.get("title") or source
                publisher_url = entry.source.get("href") or ""

            title = clean_html(entry.get("title", "News result"))
            metadata_text = " ".join([
                title,
                entry_text(entry),
            ]).strip()

            direct_url = ""
            article_text = ""
            read_status = "rss_discovery"

            if article_read_budget > 0:
                direct_url = resolve_news_article(title, publisher_url)
                if direct_url:
                    article_text, read_status = fetch_news_article(direct_url)
                article_read_budget -= 1

            source_text = article_text or metadata_text
            claims, promises = extract_candidates(
                source_text,
                claim_limit=5,
                promise_limit=2,
            )

            rows.append({
                "id": stable_id("news", direct_url or aggregator_url),
                "kind": "news",
                "source": source,
                "title": title,
                "url": direct_url or aggregator_url,
                "aggregator_url": aggregator_url,
                "publisher_url": publisher_url,
                "published_at": parse_date(entry.get("published")),
                "candidate_claims": claims,
                "candidate_promises": promises,
                "read_status": read_status,
                "body_read": bool(article_text),
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
        scan_bing_news,
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
            "Recent news article bodies are read only where publisher robots/access rules allow; stored output contains short extracted claim/context fragments, not article copies.",
            "News is discovery/context evidence, not proof of a political claim by itself.",
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
