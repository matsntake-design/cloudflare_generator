import json
import math
from collections import defaultdict
from pathlib import Path

PAGE_SIZE = 30
ROOT = Path(__file__).resolve().parent
PRIMARY_SOURCE_FILE = ROOT / 'master_articles.json'
FALLBACK_SOURCE_FILE = ROOT / 'master_articles.sample.json'
OUT_DIR = ROOT / 'output'
SITES_DIR = OUT_DIR / 'v1' / 'sites'
META_FILE = OUT_DIR / 'v1' / 'meta.json'
HEADERS_FILE = OUT_DIR / '_headers'


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_master() -> dict:
    source = PRIMARY_SOURCE_FILE if PRIMARY_SOURCE_FILE.exists() else FALLBACK_SOURCE_FILE
    with source.open('r', encoding='utf-8') as f:
        return json.load(f)


def unique_articles(articles: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for article in articles:
        key = article.get('url') or article.get('id')
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def sort_articles(articles: list[dict]) -> list[dict]:
    return sorted(articles, key=lambda x: int(x.get('publishedAt', 0)), reverse=True)


def to_page_payload(articles: list[dict], page: int, generated_at: int) -> dict:
    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    return {
        'version': 1,
        'generatedAt': generated_at,
        'page': page,
        'hasNext': end < len(articles),
        'articles': articles[start:end],
    }


def write_json(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    with path.open('w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def clear_site_pages() -> None:
    if not SITES_DIR.exists():
        return
    for site_dir in SITES_DIR.iterdir():
        if site_dir.is_dir():
            for page_file in site_dir.glob('page-*.json'):
                page_file.unlink()


def ensure_headers() -> None:
    if HEADERS_FILE.exists():
        return
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
"""
    ensure_dir(OUT_DIR)
    HEADERS_FILE.write_text(headers, encoding='utf-8')


def main() -> None:
    source = load_master()
    generated_at = int(source.get('generatedAt', 0))
    articles = sort_articles(unique_articles(source.get('articles', [])))

    clear_site_pages()
    ensure_dir(SITES_DIR)

    site_groups: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        site_id = (article.get('siteId') or '').strip()
        if site_id:
            site_groups[site_id].append(article)

    for site_id, site_articles in site_groups.items():
        page_count = max(1, math.ceil(len(site_articles) / PAGE_SIZE)) if site_articles else 1
        for page in range(1, page_count + 1):
            payload = to_page_payload(site_articles, page, generated_at)
            write_json(SITES_DIR / site_id / f'page-{page}.json', payload)

    meta = {
        'version': 1,
        'generatedAt': generated_at,
        'siteCount': len(site_groups),
    }
    if META_FILE.exists():
        try:
            current_meta = json.loads(META_FILE.read_text(encoding='utf-8'))
            current_meta.update(meta)
            meta = current_meta
        except Exception:
            pass
    write_json(META_FILE, meta)
    ensure_headers()

    print('Generated site pages under:', SITES_DIR)


if __name__ == '__main__':
    main()
