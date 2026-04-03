from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
MASTER = ROOT / "master_articles.json"

IMAGE_RE = re.compile(r"^https?://.+?(?:\.jpg|\.jpeg|\.png|\.webp|\.gif|\.bmp|\.avif)(?:\?.*)?$", re.IGNORECASE)
MAX_FUTURE_MS = 10 * 60 * 1000
JST = timezone(timedelta(hours=9))
MIDNIGHT_CLUSTER_WARN_COUNT = 3


def main() -> int:
    if not MASTER.exists():
        print("master_articles.json が見つかりません。")
        return 1

    data = json.loads(MASTER.read_text(encoding="utf-8"))
    articles = data.get("articles", [])
    generated_at = int(data.get("generatedAt") or 0)

    problems = defaultdict(list)
    url_counter = Counter()
    midnight_clusters = defaultdict(Counter)

    for index, article in enumerate(articles, start=1):
        url = (article.get("url") or "").strip()
        title = (article.get("title") or "").strip()
        thumb = article.get("thumbnailUrl")
        published_at = int(article.get("publishedAt") or 0)
        site_id = article.get("siteId") or ""

        if not url:
            problems[site_id].append(f"#{index}: URLなし")
        else:
            url_counter[url] += 1
            if "#comment" in url or "#comment_list" in url:
                problems[site_id].append(f"#{index}: コメントURL混入 -> {url}")

        if len(title) < 8:
            problems[site_id].append(f"#{index}: タイトル短すぎ -> {title}")
        if title.endswith("の記事ページ"):
            problems[site_id].append(f"#{index}: サムネ誤抽出の疑いが強いタイトル -> {title}")

        if thumb:
            thumb_text = str(thumb).strip()
            parsed = urlparse(thumb_text)
            if parsed.scheme not in {"http", "https"}:
                problems[site_id].append(f"#{index}: サムネがURLではない -> {thumb_text}")
            elif not (IMAGE_RE.match(thumb_text) or any(k in thumb_text.lower() for k in ["/imgs/", "/img/"])):
                problems[site_id].append(f"#{index}: サムネURLが怪しい -> {thumb_text}")

        if published_at <= 0:
            problems[site_id].append(f"#{index}: publishedAtなし")
        elif generated_at and published_at > generated_at + MAX_FUTURE_MS:
            problems[site_id].append(f"#{index}: publishedAtが未来すぎる -> {published_at}")
        elif published_at > 10_000_000_000:
            dt = datetime.fromtimestamp(published_at / 1000, JST)
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                midnight_clusters[site_id][dt.strftime("%Y-%m-%d")] += 1

    duplicates = [url for url, count in url_counter.items() if count > 1]
    for dup in duplicates:
        print(f"[DUP] {dup}")

    for site_id, counter in midnight_clusters.items():
        for day, count in sorted(counter.items()):
            if count >= MIDNIGHT_CLUSTER_WARN_COUNT:
                problems[site_id].append(
                    f"publishedAt が {day} 00:00 に {count}件集中しています（時刻取得失敗の疑い）"
                )

    if not problems and not duplicates:
        print("問題は見つかりませんでした。")
        return 0

    for site_id, items in sorted(problems.items()):
        print(f"\n[{site_id}] {len(items)}件")
        for item in items[:20]:
            print("-", item)
        if len(items) > 20:
            print(f"... ほか {len(items)-20} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
