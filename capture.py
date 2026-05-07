"""Capture daemon — write a snapshot of current state into data/.

Run this on a Task Scheduler trigger:
  - At log on
  - Every 15 minutes
  - At log off (best-effort; Windows event "Workstation lock" works)

Each run writes one JSON file: data/session_YYYY-MM-DD_HHMMSS.json
and appends a line to log/capture_YYYY-MM-DD.log.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from core import browsers, processes, storage

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "log"


def _log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    with (LOG_DIR / f"capture_{today}.log").open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {line}\n")


def main() -> int:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _log("config.json missing — aborting")
        return 1

    now = datetime.now()
    lookback_hours = int(config.get("history_lookback_hours", 18))
    since = now - timedelta(hours=lookback_hours)
    min_visits = int(config.get("min_visit_count", 1))

    visits = browsers.read_all(
        browsers=config.get("browsers", []),
        since=since,
        min_visit_count=min_visits,
    )
    apps = processes.snapshot(ignore_processes=config.get("ignore_processes", []))

    payload = {
        "captured_at": now.isoformat(timespec="seconds"),
        "lookback_since": since.isoformat(timespec="seconds"),
        "apps": [a.as_dict() for a in apps],
        "visits": [v.as_dict() for v in visits],
    }
    out = storage.write_session(DATA_DIR, payload, now=now)
    _log(f"wrote {out.name} — {len(apps)} apps, {len(visits)} visits")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _log("CRASH: " + traceback.format_exc().replace("\n", " | "))
        raise
