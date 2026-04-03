from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
POPULAR_SITES_FILE = ROOT.parent / "popular_sites.json"
OUTPUT_FILE = ROOT / "master_articles.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 12
MAX_ARTICLES_PER_SITE = 20
MAX_CANDIDATES_PER_SITE = 180
DEFAULT_TARGET_SITE_IDS = [
    "goldennews",
    "hatima",
    "oreteki",
    "itainews",
    "hamusoku",
]

JST = timezone(timedelta(hours=9))

EXTRA_FEED_CANDIDATES = {
    "itainews.com": ["index.rdf", "rss.xml", "feed"],
    "blog.esuteru.com": ["index.rdf", "atom.xml", "rss.xml"],
    "jin115.com": ["feed", "rss.xml", "index.rdf", "atom.xml"],
    "blog.livedoor.jp": ["index.rdf", "atom.xml", "rss.xml"],
    "livedoor.biz": ["index.rdf", "atom.xml", "rss.xml"],
    "blog.jp": ["index.rdf", "atom.xml", "rss.xml"],
}

DATE_PATTERNS = [
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M %z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
]

STRICT_ARTICLE_PATHS_BY_SITE = {
    "oreteki": re.compile(r"/archives/\d+\.html$", re.IGNORECASE),
    "hatima": re.compile(r"/archives/\d+\.html$", re.IGNORECASE),
    "goldennews": re.compile(r"/archives/\d+\.html$", re.IGNORECASE),
    "itainews": re.compile(r"/archives/\d+\.html$", re.IGNORECASE),
    "hamusoku": re.compile(r"/archives/\d+\.html$", re.IGNORECASE),
}

NOISE_TITLES = {
    "このブログについて",
    "お問い合わせ・サイトについて",
    "広告掲載について",
    "連絡先（ネタ投稿）",
}

SITE_SUFFIX_PATTERNS = {
    "oreteki": [
        r"\s*[:：]\s*オレ的ゲーム速報＠刃$",
        r"\s*[|｜]\s*オレ的ゲーム速報＠刃$",
    ],
    "hatima": [
        r"\s*[|｜]\s*はちま起稿$",
        r"\s*[:：]\s*はちま起稿$",
    ],
    "goldennews": [
        r"\s*[|｜]\s*ゴールデンタイムズ$",
        r"\s*[:：]\s*ゴールデンタイムズ$",
    ],
    "itainews": [
        r"\s*[|｜]\s*痛いニュース\([^)]*\)$",
        r"\s*[:：]\s*痛いニュース\([^)]*\)$",
    ],
    "hamusoku": [
        r"\s*[|｜]\s*ハムスター速報$",
        r"\s*[:：]\s*ハムスター速報$",
    ],
}

JAPANESE_DATE_PATTERNS = [
    re.compile(r"((?:20)?\d{2})[./年-]\s*(\d{1,2})[./月-]\s*(\d{1,2})日?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?"),
    re.compile(r"((?:20)?\d{2})[./年-]\s*(\d{1,2})[./月-]\s*(\d{1,2})日?"),
]

GENERIC_DATE_PATTERNS = [
    re.compile(r"((?:20)?\d{2})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"),
    re.compile(r"((?:20)?\d{2})/(\d{2})/(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?"),
    re.compile(r"((?:20)?\d{2})-(\d{2})-(\d{2})"),
    re.compile(r"((?:20)?\d{2})/(\d{2})/(\d{2})"),
]

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif")

ARTICLE_DATETIME_MAX_FUTURE_MS = 6 * 60 * 60 * 1000
ARTICLE_DATETIME_MAX_AGE_DAYS = 180
ARTICLE_DATETIME_HINT_DRIFT_DAYS = 7
ARTICLE_DATETIME_STRONG_DRIFT_DAYS = 30


class FetchError(Exception):
    pass


def now_millis() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)


def load_sites() -> list[dict]:
    with POPULAR_SITES_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def fetch_response(url: str) -> tuple[bytes, str, str]:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
            content_type = resp.headers.get("Content-Type", "")
            final_url = resp.geturl() or url
            return raw, content_type, final_url
    except Exception as e:
        raise FetchError(type(e).__name__) from e


def extract_charset_from_content_type(content_type: str) -> Optional[str]:
    match = re.search(r"charset=([A-Za-z0-9_\-]+)", content_type or "", re.IGNORECASE)
    return match.group(1) if match else None


def sniff_xml_encoding(raw: bytes, content_type: str = "") -> str:
    header_charset = extract_charset_from_content_type(content_type)
    if header_charset:
        return header_charset
    head = raw[:200].decode("ascii", errors="ignore")
    match = re.search(r'encoding=["\']([^"\']+)["\']', head, re.IGNORECASE)
    if match:
        return match.group(1)
    return "utf-8"


def sniff_html_encoding(raw: bytes, content_type: str = "") -> str:
    header_charset = extract_charset_from_content_type(content_type)
    if header_charset:
        return header_charset

    head = raw[:4096].decode("ascii", errors="ignore")
    patterns = [
        r'<meta[^>]+charset=["\']?([A-Za-z0-9_\-]+)',
        r'<meta[^>]+content=["\'][^"\']*charset=([A-Za-z0-9_\-]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, head, re.IGNORECASE)
        if match:
            return match.group(1)

    for enc in ["utf-8", "cp932", "shift_jis", "euc_jp"]:
        try:
            raw.decode(enc)
            return enc
        except Exception:
            continue
    return "utf-8"


def decode_html_bytes(raw: bytes, content_type: str = "") -> str:
    preferred = sniff_html_encoding(raw, content_type)
    candidates = [preferred, "utf-8", "cp932", "shift_jis", "euc_jp"]
    tried: set[str] = set()
    for enc in candidates:
        if not enc or enc in tried:
            continue
        tried.add(enc)
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode(preferred or "utf-8", errors="replace")


def unique_keep_order(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_generic_candidates(base_url: str) -> list[str]:
    parsed = urlparse(base_url)
    root = base_url.rstrip("/")
    scheme = parsed.scheme
    host = parsed.netloc
    if not scheme or not host:
        return []

    candidates: list[str] = [
        f"{root}/feed",
        f"{root}/rss",
        f"{root}/rss.xml",
        f"{root}/index.rdf",
        f"{root}/atom.xml",
    ]

    host_lower = host.lower()
    for suffix, paths in EXTRA_FEED_CANDIDATES.items():
        if host_lower == suffix or host_lower.endswith(f".{suffix}"):
            for path in paths:
                candidates.append(f"{scheme}://{host}/{path}")

    return unique_keep_order(candidates)


def discover_feed_links(base_url: str) -> list[str]:
    try:
        raw, content_type, final_url = fetch_response(base_url)
        html = decode_html_bytes(raw, content_type)
    except Exception:
        return []

    pattern = re.compile(
        r'<link[^>]+rel=["\']alternate["\'][^>]*type=["\']application/(?:rss\+xml|atom\+xml)["\'][^>]*href=["\']([^"\']+)["\']|'
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]*type=["\']application/(?:rss\+xml|atom\+xml)["\'][^>]*rel=["\']alternate["\']',
        re.IGNORECASE,
    )
    results = []
    for match in pattern.finditer(html):
        href = match.group(1) or match.group(2)
        if href:
            results.append(urljoin(final_url, href))
    return unique_keep_order(results)


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", unescape(cleaned)).strip()


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def canonicalize_article_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    clean = parsed._replace(fragment="")
    return urlunparse(clean)


def extract_archive_numeric_id(url: str) -> int:
    match = re.search(r"/archives/(\d+)\.html$", canonicalize_article_url(url), re.IGNORECASE)
    return int(match.group(1)) if match else 0


def clean_article_title(site: dict, title: str) -> str:
    cleaned = normalize_spaces(strip_html(title))
    site_id = site.get("id", "")
    if site_id == "hatima":
        cleaned = re.sub(r"\s+20\d{2}[./]\d{1,2}[./]\d{1,2}\s+\d{1,2}:\d{2}$", "", cleaned).strip()
    if site_id == "oreteki":
        cleaned = re.sub(r"^\d+\s+", "", cleaned).strip()
    for pattern in SITE_SUFFIX_PATTERNS.get(site_id, []):
        cleaned = re.sub(pattern, "", cleaned).strip()
    cleaned = cleaned.strip("-｜|:： ")
    return cleaned


def extract_meta_content(html: str, property_name: str) -> Optional[str]:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(property_name)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(property_name)}["\']',
        rf'<meta[^>]+itemprop=["\']{re.escape(property_name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']{re.escape(property_name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return unescape(match.group(1)).strip()
    return None


def extract_title_tag(html: str) -> Optional[str]:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return strip_html(match.group(1))


def is_valid_thumbnail_url(url: Optional[str]) -> bool:
    if not url:
        return False
    value = url.strip()
    if not (value.startswith("http://") or value.startswith("https://")):
        return False
    lowered = value.lower()
    if any(lowered.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True
    return any(token in lowered for token in [".jpg", ".jpeg", ".png", ".webp", "img", "image", "thumbnail"])


def normalize_thumbnail_url(url: Optional[str], base_url: str) -> Optional[str]:
    if not url:
        return None
    normalized = urljoin(base_url, url.strip())
    return normalized if is_valid_thumbnail_url(normalized) else None


def extract_image_from_fragment(fragment: str, base_url: str) -> Optional[str]:
    if not fragment:
        return None
    for pattern in [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<img[^>]+(?:src|data-src)=["\']([^"\']+)["\']',
        r'background-image\s*:\s*url\(["\']?([^"\')]+)',
    ]:
        match = re.search(pattern, fragment, re.IGNORECASE)
        if match:
            return normalize_thumbnail_url(match.group(1).strip(), base_url)
    return None


def first_text(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return "".join(elem.itertext()).strip()


def pick_rss_link(item: ET.Element, feed_url: str) -> str:
    text_link = first_text(item.find("link"))
    if text_link:
        return canonicalize_article_url(urljoin(feed_url, text_link))
    for tag in ["{http://www.w3.org/2005/Atom}link", "link"]:
        for link_elem in item.findall(tag):
            href = (link_elem.attrib.get("href") or "").strip()
            rel = (link_elem.attrib.get("rel") or "alternate").strip().lower()
            if href and rel in {"alternate", ""}:
                return canonicalize_article_url(urljoin(feed_url, href))
    return ""


def pick_rss_thumb(item: ET.Element, feed_url: str, description_html: str) -> Optional[str]:
    for tag in [
        "{http://search.yahoo.com/mrss/}thumbnail",
        "{http://search.yahoo.com/mrss/}content",
        "enclosure",
    ]:
        for elem in item.findall(tag):
            url = (elem.attrib.get("url") or elem.attrib.get("href") or "").strip()
            media_type = (elem.attrib.get("type") or "").strip().lower()
            if not url:
                continue
            if tag.endswith("enclosure") and media_type and not media_type.startswith("image/"):
                continue
            thumb = normalize_thumbnail_url(url, feed_url)
            if thumb:
                return thumb
    return extract_image_from_fragment(description_html, feed_url)


def normalize_year(year: int) -> int:
    if year < 100:
        return 2000 + year
    return year




def parse_datetime_detailed(value: str) -> tuple[int, bool]:
    raw = (value or "").strip()
    if not raw:
        return 0, False

    def finalize(dt, has_time: bool):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return int(dt.timestamp() * 1000), has_time

    try:
        dt = parsedate_to_datetime(raw)
        has_time = bool(re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", raw))
        return finalize(dt, has_time)
    except Exception:
        pass

    iso_value = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_value)
        has_time = "T" in raw or bool(re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", raw))
        return finalize(dt, has_time)
    except Exception:
        pass

    for pattern in DATE_PATTERNS:
        try:
            dt = datetime.strptime(raw, pattern)
            has_time = "%H" in pattern or bool(re.search(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", raw))
            return finalize(dt, has_time)
        except Exception:
            continue

    for pattern in JAPANESE_DATE_PATTERNS + GENERIC_DATE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        groups = list(match.groups())
        year = normalize_year(int(groups[0]))
        month = int(groups[1])
        day = int(groups[2])
        has_time = len(groups) >= 5 and groups[3] is not None and groups[4] is not None
        hour = int(groups[3]) if has_time else 0
        minute = int(groups[4]) if has_time else 0
        second = int(groups[5]) if len(groups) >= 6 and groups[5] is not None else 0
        try:
            dt = datetime(year, month, day, hour, minute, second, tzinfo=JST)
            return int(dt.timestamp() * 1000), has_time
        except Exception:
            continue
    return 0, False

def parse_datetime_to_millis(value: str) -> int:
    raw = (value or "").strip()
    if not raw:
        return 0

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return int(dt.timestamp() * 1000)
    except Exception:
        pass

    iso_value = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return int(dt.timestamp() * 1000)
    except Exception:
        pass

    for pattern in DATE_PATTERNS:
        try:
            dt = datetime.strptime(raw, pattern)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue

    for pattern in JAPANESE_DATE_PATTERNS + GENERIC_DATE_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        groups = list(match.groups())
        year = normalize_year(int(groups[0]))
        month = int(groups[1])
        day = int(groups[2])
        hour = int(groups[3]) if len(groups) >= 5 and groups[3] is not None else 0
        minute = int(groups[4]) if len(groups) >= 5 and groups[4] is not None else 0
        second = int(groups[5]) if len(groups) >= 6 and groups[5] is not None else 0
        try:
            dt = datetime(year, month, day, hour, minute, second, tzinfo=JST)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue
    return 0


def article_id(site_id: str, url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return f"{site_id}_{digest}"


def looks_like_article_url(site: dict, candidate: str) -> bool:
    try:
        normalized = canonicalize_article_url(candidate)
        base_host = (urlparse(site["baseUrl"]).hostname or "").removeprefix("www.")
        cand = urlparse(normalized)
        candidate_host = (cand.hostname or "").removeprefix("www.")
        if base_host and candidate_host and candidate_host != base_host:
            return False
        path = cand.path or ""
        strict = STRICT_ARTICLE_PATHS_BY_SITE.get(site.get("id", ""))
        if strict is not None:
            return bool(strict.search(path))
        return "/archives/" in path or path.endswith(".html") or bool(re.search(r"\d{4,}", path))
    except Exception:
        return False


def is_probably_noise_article(site: dict, url: str, title: str) -> bool:
    normalized_url = canonicalize_article_url(url)
    parsed = urlparse(normalized_url)
    path = parsed.path or ""
    normalized_title = normalize_spaces(title)
    if "/archives/cat_" in path:
        return True
    if normalized_title in NOISE_TITLES:
        return True
    if re.fullmatch(r".+\(\d+\)", normalized_title):
        return True
    if any(x in normalized_title for x in ["広告掲載について", "お問い合わせ・サイトについて"]):
        return True
    if normalized_title.startswith("カテゴリ"):
        return True
    if path.endswith("/51675327.html"):
        return True
    return False


def sanitize_html_for_date_search(html: str) -> str:
    head = html[:20000]
    head = re.sub(r"<script.*?</script>", " ", head, flags=re.IGNORECASE | re.DOTALL)
    head = re.sub(r"<style.*?</style>", " ", head, flags=re.IGNORECASE | re.DOTALL)
    head = re.sub(r"<!--.*?-->", " ", head, flags=re.DOTALL)
    head = re.sub(r"comment(?:_list|s)?", " ", head, flags=re.IGNORECASE)
    return head


def extract_json_ld_dates(html: str) -> list[str]:
    results: list[str] = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.IGNORECASE | re.DOTALL):
        block = match.group(1)
        for key in ["datePublished", "dateModified", "uploadDate"]:
            for m in re.finditer(rf'"{key}"\s*:\s*"([^"]+)"', block):
                results.append(unescape(m.group(1)).strip())
    return results


def extract_time_tag_dates(html: str) -> list[str]:
    results: list[str] = []
    for match in re.finditer(r'<time[^>]+datetime=["\']([^"\']+)["\']', html, re.IGNORECASE):
        results.append(unescape(match.group(1)).strip())
    for match in re.finditer(r'<time[^>]*>(.*?)</time>', html, re.IGNORECASE | re.DOTALL):
        results.append(strip_html(match.group(1)))
    return results


def extract_class_based_date_candidates(html: str) -> list[str]:
    candidates: list[str] = []
    patterns = [
        re.compile(r"<[^>]+(?:class|id)=[\"']([^\"']*)[\"'][^>]*>(.*?)</[^>]+>", re.IGNORECASE | re.DOTALL),
        re.compile(r"<span[^>]+itemprop=[\"']datePublished[\"'][^>]*>(.*?)</span>", re.IGNORECASE | re.DOTALL),
        re.compile(r"<abbr[^>]*>(.*?)</abbr>", re.IGNORECASE | re.DOTALL),
    ]
    for pattern in patterns:
        for match in pattern.finditer(html):
            if pattern.pattern.startswith(r"<[^>]+(?:class|id)"):
                class_or_id = (match.group(1) or "").lower()
                if not re.search(r"(?:date|time|posted|publish|update|entry)", class_or_id):
                    continue
                body = match.group(2)
            else:
                body = match.group(1)
            candidates.append(strip_html(body))
    return candidates


def extract_attribute_based_date_candidates(html: str) -> list[str]:
    candidates: list[str] = []
    attr_patterns = [
        r"<[^>]+(?:datetime|content|title|data-date|data-time|data-datetime|data-pubdate|data-published|data-created)=[\"']([^\"']+)[\"']",
        r"<[^>]+(?:class|id)=[\"'][^\"']*(?:date|time|posted|publish|update|entry)[^\"']*[\"'][^>]+(?:datetime|content|title|data-date|data-time|data-datetime|data-pubdate|data-published|data-created)=[\"']([^\"']+)[\"']",
    ]
    for pattern in attr_patterns:
        for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
            candidates.append(unescape(match.group(1)).strip())
    return candidates


def extract_combined_split_datetime_candidates(html: str) -> list[str]:
    candidates: list[str] = []
    sanitized = sanitize_html_for_date_search(html)
    separators = r'(?:\s|　|&nbsp;|<[^>]+>){0,12}'
    combined_patterns = [
        re.compile(
            rf'((?:20)?\d{{2}}[./年-]\s*\d{{1,2}}[./月-]\s*\d{{1,2}}日?){separators}(\d{{1,2}}:\d{{2}}(?::\d{{2}})?)',
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            rf'((?:20)?\d{{2}}[-/]\d{{2}}[-/]\d{{2}}){separators}(\d{{1,2}}:\d{{2}}(?::\d{{2}})?)',
            re.IGNORECASE | re.DOTALL,
        ),
    ]
    for pattern in combined_patterns:
        for match in pattern.finditer(sanitized):
            date_text = normalize_spaces(strip_html(unescape(match.group(1))))
            time_text = normalize_spaces(strip_html(unescape(match.group(2))))
            if date_text and time_text:
                candidates.append(f"{date_text} {time_text}")
    return candidates


def extract_visible_date_candidates(html: str) -> list[str]:
    candidates: list[str] = []
    sanitized = sanitize_html_for_date_search(html)
    for pattern in JAPANESE_DATE_PATTERNS + GENERIC_DATE_PATTERNS:
        for match in pattern.finditer(sanitized):
            candidates.append(match.group(0))
    return candidates


def build_datetime_candidate(millis: int, has_time: bool, source: str, raw: str = "") -> dict:
    return {
        "millis": int(millis),
        "has_time": bool(has_time),
        "source": source,
        "raw": raw,
    }



def pick_best_datetime(values: list[dict], strategy: str = "first") -> Optional[dict]:
    if not values:
        return None
    timed = [item for item in values if item.get("has_time")]
    source = timed if timed else values
    if not source:
        return None
    if strategy == "max":
        return max(source, key=lambda item: int(item.get("millis") or 0))
    if strategy == "min":
        return min(source, key=lambda item: int(item.get("millis") or 0))
    return source[0]



def filter_plausible_datetimes(values: list[str], source: str) -> tuple[list[dict], list[dict]]:
    now_ms = now_millis()
    plausible: list[dict] = []
    fallback: list[dict] = []
    seen = set()
    for value in values:
        normalized = normalize_spaces(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        millis, has_time = parse_datetime_detailed(normalized)
        if not millis:
            continue
        target = plausible if now_ms - 365 * 24 * 60 * 60 * 1000 <= millis <= now_ms + 10 * 60 * 1000 else fallback
        target.append(build_datetime_candidate(millis, has_time, source, normalized))
    return plausible, fallback



def datetime_candidate_from_hint(millis: int, has_time: bool, source: str) -> Optional[dict]:
    if not millis:
        return None
    return build_datetime_candidate(millis, has_time, source)



def local_date_tuple(millis: int) -> Optional[tuple[int, int, int]]:
    if not millis or millis <= 0:
        return None
    dt = datetime.fromtimestamp(millis / 1000, tz=JST)
    return dt.year, dt.month, dt.day



def is_recent_article_datetime(millis: int) -> bool:
    if not millis:
        return False
    now_ms = now_millis()
    oldest_ms = now_ms - ARTICLE_DATETIME_MAX_AGE_DAYS * 24 * 60 * 60 * 1000
    newest_ms = now_ms + ARTICLE_DATETIME_MAX_FUTURE_MS
    return oldest_ms <= millis <= newest_ms


def choose_best_published_candidate(page_candidate: Optional[dict], hint_candidate: Optional[dict]) -> Optional[dict]:
    now_ms = now_millis()
    page = dict(page_candidate) if page_candidate else None
    hint = dict(hint_candidate) if hint_candidate else None

    if page:
        page_millis = int(page.get("millis") or 0)
        if page_millis <= 0 or page_millis > now_ms + ARTICLE_DATETIME_MAX_FUTURE_MS:
            page = None
        elif hint:
            hint_millis = int(hint.get("millis") or 0)
            if hint_millis > 0:
                drift_ms = abs(page_millis - hint_millis)
                allowed_days = ARTICLE_DATETIME_STRONG_DRIFT_DAYS if page.get("source") == "strong" else ARTICLE_DATETIME_HINT_DRIFT_DAYS
                if drift_ms > allowed_days * 24 * 60 * 60 * 1000 and (not page.get("has_time") or page.get("source") != "strong"):
                    page = None
        elif not is_recent_article_datetime(page_millis) and page.get("source") != "strong":
            page = None

    if hint:
        hint_millis = int(hint.get("millis") or 0)
        if hint_millis <= 0 or hint_millis > now_ms + ARTICLE_DATETIME_MAX_FUTURE_MS:
            hint = None

    if page and page.get("has_time"):
        return page

    if hint and hint.get("has_time"):
        if page is None:
            return hint
        page_millis = int(page.get("millis") or 0)
        hint_millis = int(hint.get("millis") or 0)
        if local_date_tuple(page_millis) == local_date_tuple(hint_millis):
            return hint
        if page_millis and hint_millis and abs(page_millis - hint_millis) <= 36 * 60 * 60 * 1000:
            return hint

    if page and is_recent_article_datetime(int(page.get("millis") or 0)):
        return page
    if hint:
        return hint
    return page


def extract_published_at_details_from_article_html(html: str) -> Optional[dict]:
    strong_candidates: list[str] = []
    for key in [
        "article:published_time",
        "datePublished",
        "pubdate",
        "publish-date",
        "timestamp",
    ]:
        value = extract_meta_content(html, key)
        if value:
            strong_candidates.append(value)
    strong_candidates.extend(extract_json_ld_dates(html))

    medium_candidates: list[str] = []
    medium_candidates.extend(extract_time_tag_dates(html))
    medium_candidates.extend(extract_attribute_based_date_candidates(html))
    medium_candidates.extend(extract_combined_split_datetime_candidates(html))
    medium_candidates.extend(extract_class_based_date_candidates(html))

    weak_candidates = extract_visible_date_candidates(html)

    plausible, fallback = filter_plausible_datetimes(strong_candidates, "strong")
    best = pick_best_datetime(plausible, "first") or pick_best_datetime(fallback, "first")
    if best:
        return best

    plausible, fallback = filter_plausible_datetimes(medium_candidates, "medium")
    best = pick_best_datetime(plausible, "first")
    if best:
        return best

    plausible, fallback = filter_plausible_datetimes(weak_candidates, "weak")
    best = pick_best_datetime(plausible, "first")
    if best:
        return best

    return None


def extract_published_at_from_article_html(html: str) -> int:
    result = extract_published_at_details_from_article_html(html)
    return int(result.get("millis") or 0) if result else 0


def rank_candidate(candidate: dict) -> tuple:
    article_no = int(candidate.get("archive_no") or 0)
    published_hint = int(candidate.get("published_hint") or 0)
    source_order = int(candidate.get("source_order") or 0)
    return (article_no, published_hint, -source_order)


def extract_article_candidates_from_homepage(site: dict, html: str, final_url: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    base_url = final_url or site["baseUrl"]
    block_pattern = re.compile(r"(?is)<(article|li|div|section)\b[^>]*>(.*?)</\1>")
    anchor_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)

    source_order = 0
    for match in block_pattern.finditer(html):
        block_html = match.group(0)
        for anchor_match in anchor_pattern.finditer(block_html):
            href = canonicalize_article_url(urljoin(base_url, anchor_match.group(1).strip()))
            if not href or href in seen or not looks_like_article_url(site, href):
                continue
            title_hint = clean_article_title(site, strip_html(anchor_match.group(2)))
            if len(title_hint) < 8:
                title_hint = ""
            if is_probably_noise_article(site, href, title_hint):
                continue
            seen.add(href)
            published_hint = extract_published_at_details_from_article_html(block_html)
            items.append({
                "url": href,
                "title_hint": title_hint,
                "thumbnail_hint": extract_image_from_fragment(block_html, base_url),
                "published_hint": int(published_hint.get("millis") or 0) if published_hint else 0,
                "published_hint_has_time": bool(published_hint.get("has_time")) if published_hint else False,
                "archive_no": extract_archive_numeric_id(href),
                "source_order": source_order,
            })
            source_order += 1
            if len(items) >= MAX_CANDIDATES_PER_SITE:
                break
        if len(items) >= MAX_CANDIDATES_PER_SITE:
            break

    if len(items) < MAX_ARTICLES_PER_SITE:
        for anchor_match in anchor_pattern.finditer(html):
            href = canonicalize_article_url(urljoin(base_url, anchor_match.group(1).strip()))
            if not href or href in seen or not looks_like_article_url(site, href):
                continue
            title_hint = clean_article_title(site, strip_html(anchor_match.group(2)))
            if len(title_hint) < 8:
                continue
            if is_probably_noise_article(site, href, title_hint):
                continue
            context_start = max(0, anchor_match.start() - 2400)
            context_end = min(len(html), anchor_match.end() + 2400)
            context_html = html[context_start:context_end]
            seen.add(href)
            published_hint = extract_published_at_details_from_article_html(context_html)
            items.append({
                "url": href,
                "title_hint": title_hint,
                "thumbnail_hint": extract_image_from_fragment(context_html, base_url),
                "published_hint": int(published_hint.get("millis") or 0) if published_hint else 0,
                "published_hint_has_time": bool(published_hint.get("has_time")) if published_hint else False,
                "archive_no": extract_archive_numeric_id(href),
                "source_order": source_order,
            })
            source_order += 1
            if len(items) >= MAX_CANDIDATES_PER_SITE:
                break

    items.sort(key=rank_candidate, reverse=True)
    return items[:MAX_CANDIDATES_PER_SITE]


def build_article_from_page(site: dict, candidate: dict, index: int) -> Optional[dict]:
    article_url = canonicalize_article_url(candidate["url"])
    try:
        raw, content_type, final_url = fetch_response(article_url)
        article_html = decode_html_bytes(raw, content_type)
    except Exception:
        return None

    final_url = canonicalize_article_url(final_url or article_url)
    title = (
        extract_meta_content(article_html, "og:title")
        or extract_meta_content(article_html, "twitter:title")
        or extract_title_tag(article_html)
        or candidate.get("title_hint")
        or ""
    )
    title = clean_article_title(site, title)
    if len(title) < 8 or is_probably_noise_article(site, final_url, title):
        return None

    thumb = (
        normalize_thumbnail_url(extract_meta_content(article_html, "og:image"), final_url)
        or normalize_thumbnail_url(extract_meta_content(article_html, "twitter:image"), final_url)
        or normalize_thumbnail_url(candidate.get("thumbnail_hint"), final_url)
        or normalize_thumbnail_url(extract_image_from_fragment(article_html[:12000], final_url), final_url)
    )

    page_published = extract_published_at_details_from_article_html(article_html)
    homepage_hint = datetime_candidate_from_hint(
        int(candidate.get("published_hint") or 0),
        bool(candidate.get("published_hint_has_time")),
        "homepage",
    )
    chosen_published = choose_best_published_candidate(page_published, homepage_hint)
    published_at = int(chosen_published.get("millis") or 0) if chosen_published else 0
    published_at_has_time = bool(chosen_published.get("has_time")) if chosen_published else False

    # 競合方式に寄せるため、built-in 側は「時刻が取れないものを今時刻で埋める」ことはしない。
    if not published_at:
        archive_no = int(candidate.get("archive_no") or 0)
        if archive_no > 0:
            # 最終保険: 同一サイト内の順序維持用。公開時刻ではないので、時刻が取れない記事だけに限定。
            published_at = archive_no
            published_at_has_time = False
        else:
            return None

    return {
        "id": article_id(site["id"], final_url),
        "siteId": site["id"],
        "siteName": site["name"],
        "title": title,
        "url": final_url,
        "publishedAt": int(published_at),
        "thumbnailUrl": thumb,
        "_publishedAtHasTime": published_at_has_time,
    }


def parse_feed(feed_raw: bytes, site: dict, feed_url: str, content_type: str = "") -> list[dict]:
    try:
        root = ET.fromstring(feed_raw)
    except ET.ParseError:
        try:
            xml_text = feed_raw.decode(sniff_xml_encoding(feed_raw, content_type), errors="replace")
            root = ET.fromstring(xml_text)
        except Exception:
            return []

    items: list[dict] = []
    lower_root = root.tag.lower()
    is_atom = lower_root.endswith("feed")
    entries = root.findall("{http://www.w3.org/2005/Atom}entry") or root.findall("entry") if is_atom else root.findall(".//item")

    for entry in entries:
        if len(items) >= MAX_ARTICLES_PER_SITE:
            break
        title = first_text(entry.find("title")) or first_text(entry.find("{http://www.w3.org/2005/Atom}title"))
        url = pick_rss_link(entry, feed_url)
        if not title or not url:
            continue
        date_candidates = [
            first_text(entry.find("pubDate")),
            first_text(entry.find("{http://purl.org/dc/elements/1.1/}date")),
            first_text(entry.find("updated")),
            first_text(entry.find("published")),
            first_text(entry.find("{http://www.w3.org/2005/Atom}updated")),
            first_text(entry.find("{http://www.w3.org/2005/Atom}published")),
        ]
        published_at = 0
        published_at_has_time = False
        for value in date_candidates:
            published_at, published_at_has_time = parse_datetime_detailed(value)
            if published_at:
                break
        description_html = (
            first_text(entry.find("description"))
            or first_text(entry.find("{http://purl.org/rss/1.0/modules/content/}encoded"))
            or first_text(entry.find("content"))
            or first_text(entry.find("summary"))
            or first_text(entry.find("{http://www.w3.org/2005/Atom}content"))
            or first_text(entry.find("{http://www.w3.org/2005/Atom}summary"))
        )
        cleaned_title = clean_article_title(site, title)
        url = canonicalize_article_url(url)
        if len(cleaned_title) < 8 or is_probably_noise_article(site, url, cleaned_title):
            continue
        items.append({
            "id": article_id(site["id"], url),
            "siteId": site["id"],
            "siteName": site["name"],
            "title": cleaned_title,
            "url": url,
            "publishedAt": int(published_at),
            "thumbnailUrl": pick_rss_thumb(entry, feed_url, description_html),
            "_publishedAtHasTime": published_at_has_time,
        })
    items.sort(key=lambda x: int(x.get("publishedAt") or 0), reverse=True)
    return items


def sanitize_articles(site: dict, articles: list[dict]) -> list[dict]:
    results: list[dict] = []
    seen_urls: set[str] = set()
    for article in articles:
        url = canonicalize_article_url(article.get("url") or "")
        title = clean_article_title(site, article.get("title") or "")
        published_at = int(article.get("publishedAt") or 0)
        thumb = normalize_thumbnail_url(article.get("thumbnailUrl"), url or site["baseUrl"])
        if not url or url in seen_urls:
            continue
        if len(title) < 8 or is_probably_noise_article(site, url, title):
            continue
        if "#comment" in url or "#comment_list" in url:
            continue
        if published_at <= 0:
            continue
        seen_urls.add(url)
        results.append({
            "id": article_id(site["id"], url),
            "siteId": site["id"],
            "siteName": site["name"],
            "title": title,
            "url": url,
            "publishedAt": published_at,
            "thumbnailUrl": thumb,
            "_publishedAtHasTime": bool(article.get("_publishedAtHasTime")),
        })
    results.sort(key=lambda x: int(x.get("publishedAt") or 0), reverse=True)
    return results[:MAX_ARTICLES_PER_SITE]


def fetch_articles_from_homepage_and_article_pages(site: dict) -> list[dict]:
    try:
        raw, content_type, final_url = fetch_response(site["baseUrl"])
        homepage_html = decode_html_bytes(raw, content_type)
    except Exception as e:
        print(f"[WARN] homepage failed: {site['name']} ({type(e).__name__})")
        return []

    candidates = extract_article_candidates_from_homepage(site, homepage_html, final_url)
    items: list[dict] = []
    for index, candidate in enumerate(candidates):
        article = build_article_from_page(site, candidate, index)
        if article is not None:
            items.append(article)
        if len(items) >= MAX_ARTICLES_PER_SITE:
            break
    items = sanitize_articles(site, items)
    if items:
        print(f"[OK] page-enrich: {site['name']} <- {site['baseUrl']} ({len(items)}件)")
    return items


def fetch_articles_for_site(site: dict) -> list[dict]:
    base_url = site["baseUrl"]
    candidates: list[str] = []
    rss_url = (site.get("rssUrl") or "").strip()
    if rss_url:
        candidates.append(rss_url)
    candidates.extend(discover_feed_links(base_url))
    candidates.extend(build_generic_candidates(base_url))
    candidates = unique_keep_order(candidates)

    best_feed_articles: list[dict] = []
    for candidate in candidates:
        try:
            raw, content_type, final_url = fetch_response(candidate)
            articles = sanitize_articles(site, parse_feed(raw, site, final_url, content_type))
            if articles:
                best_feed_articles = articles
                print(f"[OK] feed: {site['name']} <- {candidate} ({len(articles)}件)")
                break
            else:
                print(f"[SKIP] no articles: {site['name']} <- {candidate}")
        except Exception as e:
            print(f"[SKIP] {site['name']} <- {candidate} ({type(e).__name__})")

    homepage_articles = fetch_articles_from_homepage_and_article_pages(site)

    def score(items: list[dict]) -> tuple[int, int, int, int]:
        precise_dates = sum(1 for item in items if item.get("_publishedAtHasTime"))
        valid_thumbs = sum(1 for item in items if item.get("thumbnailUrl"))
        actual_dates = sum(1 for item in items if int(item.get("publishedAt") or 0) > 10_000_000_000)
        newest = max((int(item.get("publishedAt") or 0) for item in items), default=0)
        return (precise_dates, actual_dates, valid_thumbs, newest)

    chosen = homepage_articles if score(homepage_articles) >= score(best_feed_articles) else best_feed_articles
    if not chosen:
        print(f"[WARN] source fetch failed: {site['name']}")
        return []
    return sanitize_articles(site, chosen)


def select_target_sites(all_sites: list[dict], site_ids: list[str]) -> list[dict]:
    wanted = set(site_ids)
    return [site for site in all_sites if site.get("id") in wanted]


def main() -> int:
    requested_ids = sys.argv[1:] or DEFAULT_TARGET_SITE_IDS
    all_sites = load_sites()
    target_sites = select_target_sites(all_sites, requested_ids)
    if not target_sites:
        print("対象サイトが見つかりませんでした。siteId を確認してください。")
        return 1

    articles: list[dict] = []
    for site in target_sites:
        articles.extend(fetch_articles_for_site(site))

    # URL 重複は最後に全体で除去
    final_articles: list[dict] = []
    seen_urls: set[str] = set()
    for article in sorted(articles, key=lambda x: int(x.get("publishedAt") or 0), reverse=True):
        url = canonicalize_article_url(article.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        final_articles.append({k: v for k, v in article.items() if not str(k).startswith("_")})

    payload = {
        "version": 1,
        "generatedAt": now_millis(),
        "articles": final_articles,
    }
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Saved: {OUTPUT_FILE}")
    print(f"Articles: {len(final_articles)}")
    print("Note: 競合アプリの骨子に寄せて、built-in は Cloudflare 側で整形済みJSON化。URLの #comment 系と壊れたサムネは出力前に除外します。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
