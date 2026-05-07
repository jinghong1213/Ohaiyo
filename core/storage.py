"""Read/write session snapshot files in data/."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

SESSION_PREFIX = "session_"


def session_filename(now: datetime) -> str:
    return f"{SESSION_PREFIX}{now.strftime('%Y-%m-%d_%H%M%S')}.json"


def write_session(data_dir: Path, payload: dict, now: datetime | None = None) -> Path:
    now = now or datetime.now()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / session_filename(now)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def list_sessions(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        return []
    return sorted(p for p in data_dir.iterdir() if p.name.startswith(SESSION_PREFIX) and p.suffix == ".json")


def load_session(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_session_before(data_dir: Path, before: datetime) -> Path | None:
    """Return the newest session captured strictly before `before`. Used by the
    morning launcher to find 'yesterday's last snapshot'."""
    candidates = []
    for p in list_sessions(data_dir):
        try:
            stamp = datetime.strptime(p.stem[len(SESSION_PREFIX):], "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        if stamp < before:
            candidates.append((stamp, p))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]
