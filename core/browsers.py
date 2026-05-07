"""Read browser history from Chromium-family browsers (Chrome, Edge).

Phase 1 — easy path: we only read the History SQLite DB.
The DB is locked while the browser runs, so we copy it to a temp file first.

Chrome stores last_visit_time as microseconds since 1601-01-01 UTC. We convert
to an ISO timestamp before handing data back.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Chrome's epoch is 1601-01-01 UTC; values are microseconds.
_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _chrome_time_to_dt(chrome_us: int) -> datetime:
    return _CHROME_EPOCH + timedelta(microseconds=chrome_us)


def _dt_to_chrome_time(dt: datetime) -> int:
    # Naive datetimes (the typical datetime.now() case) are treated as local time.
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return int((dt - _CHROME_EPOCH).total_seconds() * 1_000_000)


@dataclass
class Visit:
    browser: str
    url: str
    title: str
    visit_count: int
    last_visit: str  # ISO timestamp

    def as_dict(self) -> dict:
        return asdict(self)


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(path))


def read_history(
    browser_name: str,
    history_path: str,
    since: datetime,
    min_visit_count: int = 1,
) -> list[Visit]:
    src = _expand(history_path)
    if not src.exists():
        return []

    # Copy to temp because the file is locked while the browser is open.
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(src, tmp_path)
        conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                """
                SELECT url, COALESCE(title, ''), visit_count, last_visit_time
                FROM urls
                WHERE last_visit_time >= ?
                  AND visit_count >= ?
                ORDER BY last_visit_time DESC
                """,
                (_dt_to_chrome_time(since), min_visit_count),
            )
            visits = [
                Visit(
                    browser=browser_name,
                    url=url,
                    title=title,
                    visit_count=count,
                    last_visit=_chrome_time_to_dt(ts).isoformat(),
                )
                for url, title, count, ts in cur.fetchall()
            ]
        finally:
            conn.close()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return visits


def read_all(browsers: list[dict], since: datetime, min_visit_count: int = 1) -> list[Visit]:
    out: list[Visit] = []
    for b in browsers:
        try:
            out.extend(
                read_history(
                    browser_name=b["name"],
                    history_path=b["history_path"],
                    since=since,
                    min_visit_count=min_visit_count,
                )
            )
        except Exception as e:
            # Capture is best-effort; one broken browser shouldn't kill the run.
            print(f"[browsers] failed to read {b.get('name')}: {e}")
    return out
