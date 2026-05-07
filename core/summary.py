"""Build a human-readable summary of yesterday's session.

Pure formatting — takes a session dict (as written by capture.py) and returns
plain text the launcher can drop into a label or text widget.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from urllib.parse import urlparse


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


def build(session: dict) -> str:
    captured_at = session.get("captured_at", "?")
    apps = session.get("apps", [])
    visits = session.get("visits", [])

    domains = Counter(_domain(v["url"]) for v in visits if v.get("url"))
    top_domains = domains.most_common(5)

    lines = [
        f"Yesterday's last snapshot — {captured_at}",
        "",
        f"Apps with windows: {len(apps)}",
        f"URLs in history (filtered): {len(visits)}",
        "",
        "Top sites:",
    ]
    if top_domains:
        for d, n in top_domains:
            lines.append(f"  • {d} ({n})")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Apps:")
    for a in apps[:10]:
        title = a["window_titles"][0] if a.get("window_titles") else ""
        lines.append(f"  • {a['name']}{' — ' + title if title else ''}")
    if len(apps) > 10:
        lines.append(f"  …and {len(apps) - 10} more")

    return "\n".join(lines)
