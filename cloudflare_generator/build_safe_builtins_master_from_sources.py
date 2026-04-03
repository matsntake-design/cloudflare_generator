from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import build_master_from_sources as full

ROOT = Path(__file__).resolve().parent
MASTER_FILE = ROOT / "master_articles.json"
REPORT_FILE = ROOT / "safe_builtin_fetch_report.json"
SITE_ID_FILE = ROOT / "safe_builtin_site_ids.json"

PHASE3_MAX_ARTICLES_PER_SITE = 12
PHASE3_MAX_CANDIDATES_PER_SITE = 96


def load_safe_site_ids() -> list[str]:
    if not SITE_ID_FILE.exists():
        raise FileNotFoundError(f"{SITE_ID_FILE} が見つかりません。")
    data = json.loads(SITE_ID_FILE.read_text(encoding="utf-8"))
    site_ids = data.get("safeSiteIds") or []
    if not isinstance(site_ids, list) or not site_ids:
        raise ValueError("safe_builtin_site_ids.json に safeSiteIds がありません。")
    return [str(x) for x in site_ids]


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


def merge_articles(refreshed_articles: list[dict], existing_articles: list[dict], target_site_ids: set[str]) -> list[dict]:
    preferred = sort_articles(refreshed_articles)
    untouched_existing = [
        article for article in existing_articles
        if (article.get("siteId") or "") not in target_site_ids
    ]
    merged = dedupe_articles_by_url([*preferred, *untouched_existing])
    return sort_articles(merged)


def fetch_safe_articles_for_targets(target_sites: list[dict]) -> tuple[list[dict], list[dict]]:
    collected: list[dict] = []
    report_rows: list[dict] = []
    with patched_fetch_limits(
        max_articles_per_site=PHASE3_MAX_ARTICLES_PER_SITE,
        max_candidates_per_site=PHASE3_MAX_CANDIDATES_PER_SITE,
    ):
        for site in target_sites:
            articles = full.fetch_articles_for_site(site)
            if articles:
                collected.extend(articles)
                report_rows.append({
                    "siteId": site.get("id"),
                    "siteName": site.get("name"),
                    "baseUrl": site.get("baseUrl"),
                    "status": "ok",
                    "articleCount": len(articles),
                })
            else:
                report_rows.append({
                    "siteId": site.get("id"),
                    "siteName": site.get("name"),
                    "baseUrl": site.get("baseUrl"),
                    "status": "no_articles",
                    "articleCount": 0,
                })
    return sort_articles(collected), report_rows


def save_report(target_sites: list[dict], report_rows: list[dict], merged_articles: list[dict]) -> None:
    payload = {
        "version": 1,
        "generatedAt": full.now_millis(),
        "targetSiteCount": len(target_sites),
        "successSiteCount": sum(1 for row in report_rows if row.get("status") == "ok"),
        "failedSiteCount": sum(1 for row in report_rows if row.get("status") != "ok"),
        "mergedArticleCount": len(merged_articles),
        "targetSiteIds": [site.get("id") for site in target_sites],
        "rows": report_rows,
    }
    with REPORT_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def print_summary(target_sites: list[dict], report_rows: list[dict]) -> None:
    success = [row for row in report_rows if row.get("status") == "ok"]
    failed = [row for row in report_rows if row.get("status") != "ok"]
    print(f"Safe built-in target sites: {len(target_sites)}")
    print(f"Success: {len(success)} / Failed: {len(failed)}")
    if failed:
        print("Failed sites:")
        for row in failed[:20]:
            print(f"- {row['siteName']} ({row['siteId']})")
        if len(failed) > 20:
            print(f"... and {len(failed) - 20} more")


def resolve_target_sites(all_sites: list[dict], requested_ids: list[str]) -> list[dict]:
    if requested_ids:
        return full.select_target_sites(all_sites, requested_ids)
    safe_ids = set(load_safe_site_ids())
    return [site for site in all_sites if (site.get("id") or "") in safe_ids]


def main() -> int:
    requested_ids = sys.argv[1:]
    all_sites = full.load_sites()
    target_sites = resolve_target_sites(all_sites, requested_ids)
    if not target_sites:
        print("対象サイトが見つかりませんでした。siteId を確認してください。")
        return 1

    existing_master = load_existing_master()
    existing_articles = existing_master.get("articles") or []

    refreshed_articles, report_rows = fetch_safe_articles_for_targets(target_sites)
    if not refreshed_articles:
        print("[WARN] 高信頼 built-in 群の更新では新しい記事を取得できませんでした。既存 master を維持します。")
        save_report(target_sites, report_rows, existing_articles)
        return 1

    merged_articles = merge_articles(
        refreshed_articles=refreshed_articles,
        existing_articles=existing_articles,
        target_site_ids={site.get("id") for site in target_sites},
    )

    payload = {
        "version": 1,
        "generatedAt": full.now_millis(),
        "articles": merged_articles,
    }

    with MASTER_FILE.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    save_report(target_sites, report_rows, merged_articles)
    print(f"Saved: {MASTER_FILE}")
    print(f"Articles: {len(merged_articles)}")
    print_summary(target_sites, report_rows)
    print("Note: 監査済みの高信頼 built-in 群だけを対象にするフェーズ3更新です。")
    print(f"Report: {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
