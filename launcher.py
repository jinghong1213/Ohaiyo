"""Morning launcher — Tk GUI mockup.

Loads the most recent session captured before today started, shows a summary,
and lets the user tick which apps and URLs to relaunch.

Phase 1 deliberately keeps the UI minimal so we can iterate on the data model
once you've actually used it for a few mornings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import Tk, ttk, StringVar, BooleanVar, messagebox, scrolledtext
from urllib.parse import urlparse

from core import storage, summary

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "log"

# Cap how many URLs we show — history is noisy, top-N keeps the UI scannable.
MAX_URLS_SHOWN = 25


def _log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    with (LOG_DIR / f"launcher_{today}.log").open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {line}\n")


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


def _pick_top_urls(visits: list[dict], n: int) -> list[dict]:
    """Pick the most likely 'open tab' candidates from raw history.

    Easy-path heuristic: keep one URL per domain (highest visit_count), then
    sort by recency. Replaces the 'I-glanced-at-this-once' noise with the
    pages you actually returned to.
    """
    by_domain: dict[str, dict] = {}
    for v in visits:
        d = _domain(v["url"])
        if not d:
            continue
        cur = by_domain.get(d)
        if cur is None or v.get("visit_count", 0) > cur.get("visit_count", 0):
            by_domain[d] = v
    ranked = sorted(by_domain.values(), key=lambda v: v.get("last_visit", ""), reverse=True)
    return ranked[:n]


def _load_yesterday_session() -> tuple[Path, dict] | None:
    today_start = datetime.combine(datetime.now().date(), datetime.min.time())
    path = storage.latest_session_before(DATA_DIR, today_start)
    if path is None:
        return None
    return path, storage.load_session(path)


def _launch_url(url: str, browser_cmd: str) -> None:
    cmd = browser_cmd.format(url=url)
    subprocess.Popen(cmd, shell=True)


def _launch_app(exe_path: str | None, name: str) -> None:
    if exe_path and Path(exe_path).exists():
        subprocess.Popen([exe_path], shell=False)
    else:
        # Fall back to letting Windows resolve the name (e.g. "notepad.exe").
        subprocess.Popen(name, shell=True)


class LauncherApp:
    def __init__(self, root: Tk, session_path: Path, session: dict, config: dict) -> None:
        self.root = root
        self.session = session
        self.config = config
        self.session_path = session_path

        # Map browser-name -> launch command, fall back to Chrome.
        self._browser_cmd_by_name = {
            b["name"]: b.get("launch_command", 'start "" "{url}"')
            for b in config.get("browsers", [])
        }
        self._default_browser_cmd = next(
            iter(self._browser_cmd_by_name.values()),
            'start "" "{url}"',
        )

        self.url_vars: list[tuple[BooleanVar, dict]] = []
        self.app_vars: list[tuple[BooleanVar, dict]] = []

        root.title("Daily Resume — yesterday's session")
        root.geometry("960x640")

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Label(
            outer,
            text=f"Loaded snapshot: {self.session_path.name}",
            font=("Segoe UI", 11, "bold"),
        )
        header.pack(anchor="w", pady=(0, 6))

        sm = scrolledtext.ScrolledText(outer, height=8, wrap="word")
        sm.insert("1.0", summary.build(self.session))
        sm.configure(state="disabled")
        sm.pack(fill="x", pady=(0, 10))

        cols = ttk.Frame(outer)
        cols.pack(fill="both", expand=True)

        # Left — URLs
        left = ttk.LabelFrame(cols, text="Sites to reopen", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self._fill_urls(left)

        # Right — Apps
        right = ttk.LabelFrame(cols, text="Apps to reopen", padding=8)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self._fill_apps(right)

        # Footer
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        self.status = StringVar(value="Tick what you want, untick what was noise.")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Button(footer, text="Skip all", command=self._uncheck_all).pack(side="right", padx=4)
        ttk.Button(footer, text="Select all", command=self._check_all).pack(side="right", padx=4)
        ttk.Button(footer, text="Launch selected", command=self._launch).pack(side="right", padx=4)

    def _scrollable(self, parent: ttk.Frame) -> ttk.Frame:
        canvas = ttk.Frame(parent)
        canvas.pack(fill="both", expand=True)
        from tkinter import Canvas, Scrollbar
        cv = Canvas(canvas, highlightthickness=0)
        sb = Scrollbar(canvas, orient="vertical", command=cv.yview)
        inner = ttk.Frame(cv)
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Mouse-wheel scroll inside this canvas only.
        def _on_wheel(e):
            cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
        inner.bind("<Enter>", lambda _: cv.bind_all("<MouseWheel>", _on_wheel))
        inner.bind("<Leave>", lambda _: cv.unbind_all("<MouseWheel>"))
        return inner

    def _fill_urls(self, parent: ttk.Frame) -> None:
        urls = _pick_top_urls(self.session.get("visits", []), MAX_URLS_SHOWN)
        if not urls:
            ttk.Label(parent, text="(no history found)").pack()
            return
        body = self._scrollable(parent)
        for v in urls:
            var = BooleanVar(value=True)
            label = f"{_domain(v['url'])} — {v.get('title') or v['url'][:80]}"
            ttk.Checkbutton(body, text=label, variable=var).pack(anchor="w", pady=1)
            self.url_vars.append((var, v))

    def _fill_apps(self, parent: ttk.Frame) -> None:
        apps = self.session.get("apps", [])
        if not apps:
            ttk.Label(parent, text="(no apps captured)").pack()
            return
        body = self._scrollable(parent)
        for a in apps:
            var = BooleanVar(value=True)
            title_hint = f" — {a['window_titles'][0]}" if a.get("window_titles") else ""
            ttk.Checkbutton(body, text=f"{a['name']}{title_hint}", variable=var).pack(anchor="w", pady=1)
            self.app_vars.append((var, a))

    # ---------- actions ----------
    def _check_all(self) -> None:
        for v, _ in self.url_vars + self.app_vars:
            v.set(True)

    def _uncheck_all(self) -> None:
        for v, _ in self.url_vars + self.app_vars:
            v.set(False)

    def _launch(self) -> None:
        chosen_urls = [d for v, d in self.url_vars if v.get()]
        chosen_apps = [d for v, d in self.app_vars if v.get()]

        for visit in chosen_urls:
            cmd = self._browser_cmd_by_name.get(visit.get("browser"), self._default_browser_cmd)
            try:
                _launch_url(visit["url"], cmd)
                _log(f"launched url: {visit['url']}")
            except Exception as e:
                _log(f"failed url {visit['url']}: {e}")

        for app in chosen_apps:
            try:
                _launch_app(app.get("exe_path"), app["name"])
                _log(f"launched app: {app['name']}")
            except Exception as e:
                _log(f"failed app {app['name']}: {e}")

        self.status.set(f"Launched {len(chosen_urls)} URL(s) and {len(chosen_apps)} app(s).")
        # Don't auto-close — user might want to see what happened, or relaunch more.


def main() -> int:
    if not CONFIG_PATH.exists():
        print("config.json missing", file=sys.stderr)
        return 1
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    loaded = _load_yesterday_session()
    if loaded is None:
        # Show a tiny error window rather than crashing silently.
        root = Tk()
        root.withdraw()
        messagebox.showinfo(
            "Daily Resume",
            f"No session snapshots found in {DATA_DIR}.\n\n"
            "Run capture.py first (or wait for the scheduled task to fire).",
        )
        return 0

    path, session = loaded
    root = Tk()
    LauncherApp(root, path, session, config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
