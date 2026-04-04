import json
import math
import os
from collections import defaultdict
from pathlib import Path

PAGE_SIZE = 30
POPULAR_WINDOW_MS = 24 * 60 * 60 * 1000

ROOT = Path(__file__).resolve().parent
PRIMARY_SOURCE_FILE = ROOT / "master_articles.json"
FALLBACK_SOURCE_FILE = ROOT / "master_articles.sample.json"
OUT_DIR = ROOT / "output"
SITE_API_DIR = OUT_DIR / "v1" / "site-api"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_master() -> dict:
    source = PRIMARY_SOURCE_FILE if PRIMARY_SOURCE_FILE.exists() else FALLBACK_SOURCE_FILE
    with source.open("r", encoding="utf-8") as f:
        return json.load(f)


def unique_articles(articles: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for article in articles:
        key = article.get("url") or article.get("id")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def sort_articles(articles: list[dict]) -> list[dict]:
    return sorted(articles, key=lambda x: int(x.get("publishedAt", 0)), reverse=True)


def to_page_payload(articles: list[dict], page: int, generated_at: int) -> dict:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_items = articles[start:end]
    return {
        "version": 1,
        "generatedAt": generated_at,
        "page": page,
        "hasNext": end < len(articles),
        "articles": page_items,
    }


def write_json(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def write_pages(base_dir: Path, articles: list[dict], generated_at: int) -> int:
    page_count = max(1, math.ceil(len(articles) / PAGE_SIZE)) if articles else 1
    for page in range(1, page_count + 1):
        payload = to_page_payload(articles, page, generated_at)
        write_json(base_dir / f"page-{page}.json", payload)
    return page_count


def format_compact_datetime(millis: int) -> str:
    if millis <= 10_000_000_000:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_compact_article(article: dict) -> dict | None:
    published_at = int(article.get("publishedAt", 0) or 0)
    raw_date = format_compact_datetime(published_at)
    if not raw_date:
        return None
    return {
        "o": article.get("id") or "",
        "p": article.get("title") or "",
        "q": article.get("url") or "",
        "r": article.get("thumbnailUrl") or "",
        "s": raw_date,
        "u": article.get("siteName") or "",
    }


def to_compact_page_payload(articles: list[dict], page: int, generated_at: int) -> dict:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    compact_items = [item for item in (to_compact_article(article) for article in articles[start:end]) if item]
    return {
        "version": 1,
        "generatedAt": generated_at,
        "page": page,
        "hasNext": end < len(articles),
        "d": compact_items,
    }


def write_compact_pages(base_dir: Path, articles: list[dict], generated_at: int) -> int:
    page_count = max(1, math.ceil(len(articles) / PAGE_SIZE)) if articles else 1
    for page in range(1, page_count + 1):
        payload = to_compact_page_payload(articles, page, generated_at)
        write_json(base_dir / f"page-{page}.json", payload)
    return page_count


def build_popular(articles: list[dict], generated_at: int) -> list[dict]:
    threshold = generated_at - POPULAR_WINDOW_MS
    recent = [a for a in articles if int(a.get("publishedAt", 0)) >= threshold]
    return recent if recent else articles


def clear_out_dir(target_dir: Path) -> None:
    if not target_dir.exists():
        return
    for root, dirs, files in os.walk(target_dir, topdown=False):
        for name in files:
            Path(root, name).unlink()
        for name in dirs:
            Path(root, name).rmdir()


def write_headers() -> None:
    headers = """/v1/latest/*
  Content-Type: application/json; charset=utf-8
  Cache-Control: public, max-age=300
  Access-Control-Allow-Origin: *

/v1/meta.json
  Content-Type: application/json; charset=utf-8
  Cache-Control: public, max-age=300
  Access-Control-Allow-Origin: *

/v1/popular/*
  Content-Type: application/json; charset=utf-8
  Cache-Control: public, max-age=900
  Access-Control-Allow-Origin: *

/v1/sites/*
  Content-Type: application/json; charset=utf-8
  Cache-Control: public, max-age=900
  Access-Control-Allow-Origin: *

/v1/site-api/*
  Content-Type: application/json; charset=utf-8
  Cache-Control: public, max-age=900
  Access-Control-Allow-Origin: *
"""
    (OUT_DIR / "_headers").write_text(headers, encoding="utf-8")


def main() -> None:
    source = load_master()
    generated_at = int(source.get("generatedAt", 0))
    articles = sort_articles(unique_articles(source.get("articles", [])))

    clear_out_dir(OUT_DIR)
    ensure_dir(OUT_DIR / "v1")

    latest_page_count = write_pages(OUT_DIR / "v1" / "latest", articles, generated_at)
    popular_articles = build_popular(articles, generated_at)
    popular_page_count = write_pages(OUT_DIR / "v1" / "popular", popular_articles, generated_at)

    site_groups: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        site_id = (article.get("siteId") or "").strip()
        if site_id:
            site_groups[site_id].append(article)

    for site_id, site_articles in site_groups.items():
        write_pages(OUT_DIR / "v1" / "sites" / site_id, site_articles, generated_at)
        write_compact_pages(SITE_API_DIR / site_id, site_articles, generated_at)

    meta = {
        "version": 1,
        "generatedAt": generated_at,
        "latestPageCount": latest_page_count,
        "popularPageCount": popular_page_count,
        "siteCount": len(site_groups),
    }
    write_json(OUT_DIR / "v1" / "meta.json", meta)
    write_headers()

    print("Generated files under:", OUT_DIR)


if __name__ == "__main__":
    main()
