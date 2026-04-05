from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import build_safe_builtins_master_from_sources as safe
import build_master_from_sources as full

ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "fast_state.json"
REPORT_FILE = ROOT / "safe_builtin_fetch_report.json"
MASTER_FILE = ROOT / "master_articles.json"

MAX_TARGET_SITES_PER_RUN = 8
MIN_TARGET_SITES_PER_RUN = 3
PRIORITY_REFRESH_INTERVAL_MS = 15 * 60 * 1000
NORMAL_REFRESH_INTERVAL_MS = 55 * 60 * 1000

# build_master_from_sources.py に既定ターゲットとして入っている本命群。
PRIORITY_SITE_IDS = set(full.DEFAULT_TARGET_SITE_IDS)

# 失敗したサイトに対する backoff（分）
FAILURE_BACKOFF_MINUTES = {
    1: 10,
    2: 20,
    3: 40,
    4: 60,
}
DEFAULT_FAILURE_BACKOFF_MINUTES = 120


def now_millis() -> int:
    return int(time.time() * 1000)


def load_json_file(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback



def save_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def load_state() -> dict[str, dict[str, Any]]:
    payload = load_json_file(STATE_FILE, {"version": 1, "sites": {}})
    sites = payload.get("sites") if isinstance(payload, dict) else None
    if not isinstance(sites, dict):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for site_id, row in sites.items():
        if not isinstance(site_id, str) or not isinstance(row, dict):
            continue
        normalized[site_id] = {
            "checkedAt": int(row.get("checkedAt") or 0),
            "lastStatus": str(row.get("lastStatus") or "unknown"),
            "failureCount": int(row.get("failureCount") or 0),
            "backoffUntil": int(row.get("backoffUntil") or 0),
            "articleCount": int(row.get("articleCount") or 0),
            "lastSiteName": str(row.get("lastSiteName") or ""),
        }
    return normalized



def save_state(state: dict[str, dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "generatedAt": now_millis(),
        "sites": state,
    }
    save_json_file(STATE_FILE, payload)



def get_interval_for_site(site_id: str) -> int:
    return PRIORITY_REFRESH_INTERVAL_MS if site_id in PRIORITY_SITE_IDS else NORMAL_REFRESH_INTERVAL_MS



def get_backoff_minutes(failure_count: int) -> int:
    return FAILURE_BACKOFF_MINUTES.get(failure_count, DEFAULT_FAILURE_BACKOFF_MINUTES)



def should_refresh_site(site_id: str, state_row: dict[str, Any], now_ms: int) -> bool:
    backoff_until = int(state_row.get("backoffUntil") or 0)
    if backoff_until > now_ms:
        return False

    checked_at = int(state_row.get("checkedAt") or 0)
    if checked_at <= 0:
        return True

    interval_ms = get_interval_for_site(site_id)
    return now_ms - checked_at >= interval_ms



def site_sort_key(site: dict[str, Any], state_row: dict[str, Any], now_ms: int) -> tuple:
    site_id = str(site.get("id") or "")
    checked_at = int(state_row.get("checkedAt") or 0)
    last_status = str(state_row.get("lastStatus") or "unknown")
    failure_count = int(state_row.get("failureCount") or 0)
    is_priority = site_id in PRIORITY_SITE_IDS
    interval_ms = get_interval_for_site(site_id)
    overdue_ms = max(0, now_ms - checked_at - interval_ms) if checked_at > 0 else 10**15

    # 優先順位:
    # 1) 一度も見ていない
    # 2) 失敗・no_articles が続いている
    # 3) 優先サイト
    # 4) どれだけ更新期限を過ぎているか
    return (
        0 if checked_at <= 0 else 1,
        0 if last_status != "ok" else 1,
        -failure_count,
        0 if is_priority else 1,
        -overdue_ms,
        checked_at,
        site_id,
    )



def select_target_sites(all_sites: list[dict[str, Any]], state: dict[str, dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    safe_ids = set(safe.load_safe_site_ids())
    safe_sites = [site for site in all_sites if (site.get("id") or "") in safe_ids]

    due_sites: list[dict[str, Any]] = []
    for site in safe_sites:
        site_id = str(site.get("id") or "")
        state_row = state.get(site_id, {})
        if should_refresh_site(site_id, state_row, now_ms):
            due_sites.append(site)

    if not due_sites:
        # 全部まだ期限内でも、最低数だけ oldest から回す。
        ordered_all = sorted(
            safe_sites,
            key=lambda s: site_sort_key(s, state.get(str(s.get("id") or ""), {}), now_ms),
        )
        return ordered_all[:MIN_TARGET_SITES_PER_RUN]

    ordered_due = sorted(
        due_sites,
        key=lambda s: site_sort_key(s, state.get(str(s.get("id") or ""), {}), now_ms),
    )
    return ordered_due[:MAX_TARGET_SITES_PER_RUN]



def run_subprocess(command: list[str], allow_failure: bool = False) -> int:
    print("[RUN]", " ".join(command))
    result = subprocess.run(command)
    if result.returncode != 0 and not allow_failure:
        raise SystemExit(result.returncode)
    return result.returncode



def update_state_from_report(
    state: dict[str, dict[str, Any]],
    report_payload: dict[str, Any],
    targeted_sites: list[dict[str, Any]],
    now_ms: int,
) -> None:
    rows = report_payload.get("rows") if isinstance(report_payload, dict) else None
    row_map: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            site_id = str(row.get("siteId") or "")
            if site_id:
                row_map[site_id] = row

    for site in targeted_sites:
        site_id = str(site.get("id") or "")
        site_name = str(site.get("name") or "")
        previous = state.get(site_id, {})
        previous_failure_count = int(previous.get("failureCount") or 0)
        row = row_map.get(site_id)

        status = str((row or {}).get("status") or "runner_error")
        article_count = int((row or {}).get("articleCount") or 0)

        if status == "ok":
            failure_count = 0
            backoff_until = 0
        else:
            failure_count = previous_failure_count + 1
            backoff_minutes = get_backoff_minutes(failure_count)
            backoff_until = now_ms + backoff_minutes * 60 * 1000

        state[site_id] = {
            "checkedAt": now_ms,
            "lastStatus": status,
            "failureCount": failure_count,
            "backoffUntil": backoff_until,
            "articleCount": article_count,
            "lastSiteName": site_name,
        }



def validate_master_exists() -> None:
    if not MASTER_FILE.exists():
        raise FileNotFoundError(f"{MASTER_FILE} が見つかりません。")



def main() -> int:
    now_ms = now_millis()
    state = load_state()
    all_sites = full.load_sites()
    target_sites = select_target_sites(all_sites, state, now_ms)

    if not target_sites:
        print("対象サイトが選べませんでした。")
        return 1

    target_site_ids = [str(site.get("id") or "") for site in target_sites]
    print("Fast incremental target sites:")
    for site in target_sites:
        site_id = str(site.get("id") or "")
        site_name = str(site.get("name") or "")
        previous = state.get(site_id, {})
        print(
            f"- {site_name} ({site_id}) | "
            f"lastStatus={previous.get('lastStatus', 'unknown')} | "
            f"checkedAt={previous.get('checkedAt', 0)}"
        )

    # safe built-ins 更新。ここは 0 / 1 があり得るので allow_failure にする。
    safe_return_code = run_subprocess(
        [sys.executable, str(ROOT / "build_safe_builtins_master_from_sources.py"), *target_site_ids],
        allow_failure=True,
    )

    validate_master_exists()

    if REPORT_FILE.exists():
        report_payload = load_json_file(REPORT_FILE, {})
        update_state_from_report(state, report_payload, target_sites, now_ms)
        save_state(state)
    else:
        print(f"[WARN] {REPORT_FILE} が見つからないため state を更新できませんでした。")

    # master_articles.json をもとに sites / site-api / popular を軽く作り直す。
    run_subprocess([sys.executable, str(ROOT / "generate_sites_only.py")])
    run_subprocess([sys.executable, str(ROOT / "generate_popular_only.py")])

    # safe build が全滅でも、既存 master を維持してページ生成できていれば 0 で返す。
    if safe_return_code != 0:
        print("[WARN] safe built-ins refresh returned non-zero. Existing master was kept and pages were regenerated.")

    print(f"Saved state: {STATE_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
