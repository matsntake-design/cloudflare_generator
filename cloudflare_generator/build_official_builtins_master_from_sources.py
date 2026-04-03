from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "build_safe_builtins_master_from_sources.py"

if not TARGET.exists():
    raise SystemExit(f"{TARGET} が見つかりません。")

runpy.run_path(str(TARGET), run_name="__main__")
