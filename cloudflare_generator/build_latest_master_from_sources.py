from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import build_master_from_sources as full

ROOT = Path(__file__).resolve().parent
MASTER_FILE = ROOT / "master_articles.json"

# latest 専用の軽量ルート。
# 競合アプリの「latest は鮮度重視、固定寄りデータは別レーン」の考え方に寄せて、
# 取得件数と探索候補数を絞って master_articles.json の先頭側だけを素早く更新する。
LATEST_MAX_ARTICLES_PER_SITE = 8
LATEST_MAX_CANDIDATES_PER_SITE = 72
DEFAULT_TARGET_SITE_IDS = list(full.DEFAULT_TARGET_SITE_IDS)


@contextmanager
def patched_fetch_limits(max_articles_per_site: int, max_candidates_per_site: int):
    old_max_articles = full.MAX_ARTICLES_PER_SITE
    old_max_candidates = full.MAX_CANDIDATES_PER_SITE
    full.MAX_ARTICLES_PER_SITE = max_articles_per_site
    full.MAX_CANDIDATES_PER_SITE = max_candidates_per_site
    try:
        yield
    finally:
        full.MAX_ARTICLES_PER_SITE = old_max_articles
        full.MAX_CANDIDATES_PER_SITE = old_max_candidates


def load_existing_master() -> dict:
    if not MASTER_FILE.exists():
        return {"version": 1, "generatedAt": 0, "articles": []}
    with MASTER_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {"version": 1, "generatedAt": 0, "articles": []}
    articles = data.get("articles")
    if not isinstance(articles, list):
        data["articles"] = []
    return data


def strip_internal_fields(article: dict) -> dict:
    return {k: v for k, v in article.items() if not str(k).startswith("_")}


def dedupe_articles_by_url(articles: Iterable[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    results: list[dict] = []
    for article in articles:
        url = full.canonicalize_article_url(article.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        normalized = dict(article)
        normalized["url"] = url
        results.append(strip_internal_fields(normalized))
    return results


def sort_articles(articles: Iterable[dict]) -> list[dict]:
    return sorted(articles, key=lambda x: int(x.get("publishedAt") or 0), reverse=True)


def merge_articles(refreshed_articles: list[dict], existing_articles: list[dict]) -> list[dict]:
    # refreshed 側を優先しつつ、今回取り切れなかった古い記事は既存 master から残す。
    preferred = sort_articles(refreshed_articles)
    fallback = sort_articles(existing_articles)
    merged = dedupe_articles_by_url([*preferred, *fallback])
    return sort_articles(merged)


def fetch_latest_articles_for_targets(target_sites: list[dict]) -> list[dict]:
    collected: list[dict] = []
    with patched_fetch_limits(
        max_articles_per_site=LATEST_MAX_ARTICLES_PER_SITE,
        max_candidates_per_site=LATEST_MAX_CANDIDATES_PER_SITE,
    ):
        for site in target_sites:
            articles = full.fetch_articles_for_site(site)
            if articles:
                collected.extend(articles)
    return sort_articles(collected)


def main() -> int:
    requested_ids = sys.argv[1:] or DEFAULT_TARGET_SITE_IDS
    all_sites = full.load_sites()
    target_sites = full.select_target_sites(all_sites, requested_ids)
    if not target_sites:
        print("対象サイトが見つかりませんでした。siteId を確認してください。")
        return 1

    existing_master = load_existing_master()
    existing_articles = existing_master.get("articles") or []

    refreshed_articles = fetch_latest_articles_for_targets(target_sites)
    if not refreshed_articles:
        print("[WARN] latest 軽量更新では新しい記事を取得できませんでした。既存 master を維持します。")
        return 1

    merged_articles = merge_articles(refreshed_articles, existing_articles)
    payload = {
        "version": 1,
        "generatedAt": full.now_millis(),
        "articles": merged_articles,
    }

    with MASTER_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"Saved: {MASTER_FILE}")
    print(f"Articles: {len(merged_articles)}")
    print(
        "Note: latest 専用の軽量更新です。標準サイトの先頭側を優先取得し、"
        "既存 master とマージして鮮度を上げています。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
