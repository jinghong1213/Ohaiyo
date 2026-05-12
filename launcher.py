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
from typing import Callable
from urllib.parse import urlparse

from core import storage, summary, splash

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "log"
ASSETS_DIR = ROOT / "assets"
CAT_GIF = ASSETS_DIR / "cat.gif"  # optional asset — splash falls back to ASCII

# Cap how many URLs we show — history is noisy, top-N keeps the UI scannable.
MAX_URLS_SHOWN = 25

# Cat frames used during launch — the kaomoji "purrs" while apps open.
_LAUNCH_CAT_BUSY = "( =^o^= )"   # excited / paws on keyboard
_LAUNCH_CAT_IDLE = "( =^.^= )"   # default
_LAUNCH_STEP_MS = 300            # delay between each launch — visual punch


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


def _load_session(latest: bool = False) -> tuple[Path, dict] | None:
    """Pick which snapshot to show.

    Default: the newest snapshot captured strictly before midnight today
    (i.e. "yesterday's last save"). With latest=True the time filter is
    skipped and we just take the newest file in data/ — handy for demos
    where you capture, close some apps, and relaunch in the same session.
    """
    if latest:
        all_sessions = storage.list_sessions(DATA_DIR)
        if not all_sessions:
            return None
        path = all_sessions[-1]
    else:
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

        root.title("Ohaiyo — yesterday's session")
        root.geometry("960x640")

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        # Brand row: "Oh" + italic "ai" + "yo". We pack three Labels side-by-side
        # because Tk Labels can't carry inline styling; this is the workaround.
        brand = ttk.Frame(outer)
        brand.pack(anchor="w", pady=(0, 2))
        brand_regular = ("Segoe UI", 22, "bold")
        brand_italic = ("Segoe UI", 22, "bold italic")
        ttk.Label(brand, text="Oh", font=brand_regular).pack(side="left")
        ttk.Label(brand, text="ai", font=brand_italic, foreground="#5b8def").pack(side="left")
        ttk.Label(brand, text="yo", font=brand_regular).pack(side="left")

        header = ttk.Label(
            outer,
            text=f"Loaded snapshot: {self.session_path.name}",
            font=("Segoe UI", 10),
            foreground="#666",
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
        self.status = StringVar(
            value=f"{_LAUNCH_CAT_IDLE}  tick what you want, untick what was noise."
        )
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
        # Build a single queue of (label, action) so we can step through one
        # item per tick. The "purring" cat animation in the status bar is
        # what makes the launch feel responsive.
        queue: list[tuple[str, Callable[[], None]]] = []

        for visit in [d for v, d in self.url_vars if v.get()]:
            cmd = self._browser_cmd_by_name.get(visit.get("browser"), self._default_browser_cmd)
            label = visit.get("title") or visit["url"]

            def make_url_action(v=visit, c=cmd):
                def _do():
                    try:
                        _launch_url(v["url"], c)
                        _log(f"launched url: {v['url']}")
                    except Exception as e:
                        _log(f"failed url {v['url']}: {e}")
                return _do

            queue.append((str(label)[:40], make_url_action()))

        for app in [d for v, d in self.app_vars if v.get()]:
            def make_app_action(a=app):
                def _do():
                    try:
                        _launch_app(a.get("exe_path"), a["name"])
                        _log(f"launched app: {a['name']}")
                    except Exception as e:
                        _log(f"failed app {a['name']}: {e}")
                return _do

            queue.append((app["name"], make_app_action()))

        # Run the queue with a small delay between each so the user (and any
        # interviewer watching) can see the cat working through the list.
        total = len(queue)
        self._launch_step(queue, 0, total)

    def _launch_step(self, queue: list, idx: int, total: int) -> None:
        if idx >= total:
            self.status.set(f"{_LAUNCH_CAT_IDLE}  done — launched {total} item(s).")
            return
        label, action = queue[idx]
        self.status.set(f"{_LAUNCH_CAT_BUSY}  launching {label}…  ({idx + 1}/{total})")
        action()
        self.root.after(_LAUNCH_STEP_MS, lambda: self._launch_step(queue, idx + 1, total))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="launcher.py",
        description="Ohaiyo — open the morning launcher GUI.",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Load the newest snapshot regardless of date "
             "(default loads only snapshots from before midnight today).",
    )
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        print("config.json missing", file=sys.stderr)
        return 1
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    loaded = _load_session(latest=args.latest)
    if loaded is None:
        # Show a tiny error window rather than crashing silently.
        root = Tk()
        root.withdraw()
        messagebox.showinfo(
            "Ohaiyo",
            f"No session snapshots found in {DATA_DIR}.\n\n"
            "Run capture.py first (or wait for the scheduled task to fire).",
        )
        return 0

    path, session = loaded

    # Build the root, hide it, show splash, then reveal LauncherApp when the
    # splash finishes. Sequencing this on Tk's event loop keeps everything on
    # one thread — Tk is not thread-safe, so no threading.Timer here.
    root = Tk()
    root.withdraw()

    def build_main() -> None:
        root.deiconify()
        LauncherApp(root, path, session, config)

    splash.Splash(root, on_close=build_main, gif_path=CAT_GIF)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
