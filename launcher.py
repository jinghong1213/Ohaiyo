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
import tkinter as tk
from tkinter import Tk, ttk, StringVar, BooleanVar, IntVar, messagebox, scrolledtext
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


# ---------------------------------------------------------------------------
# Design system
# ---------------------------------------------------------------------------
#
# Centralizing the palette + typography here means anywhere in the UI we can
# refer to PALETTE["accent"] instead of remembering "#5B8DEF". When the brand
# evolves we change one map.
#
PALETTE = {
    "bg":          "#FAF7F2",  # window background — warm off-white
    "surface":     "#FFFFFF",  # cards
    "border":      "#E8DFD3",  # 1px hairlines around cards
    "text":        "#2A2522",  # primary text — warm near-black
    "muted":       "#7A6657",  # secondary text
    "subtle":      "#9E8E7C",  # tertiary / labels
    "accent":      "#5B8DEF",  # brand blue — the italic "ai"
    "accent_dim":  "#4A7BDE",  # accent on hover
    "accent_soft": "#E8F0FE",  # accent tint for fills
    "hover":       "#F4EFE7",  # row hover background
    "sites":       "#5B8DEF",  # left column accent (sites)
    "apps":        "#E89146",  # right column accent (apps)
    "cat":         "#5A3E2E",  # kaomoji color
    "white":       "#FFFFFF",
}

FONT_BASE = "Segoe UI"


def _apply_design(root: Tk) -> None:
    """Configure ttk widget styles + root window to use the design system.

    The `clam` theme is the most customizable cross-platform. We override
    button styles for primary/secondary, and use direct tk widgets (not ttk)
    where we need precise color control over containers.
    """
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass  # fall through with current theme if `clam` unavailable

    root.configure(bg=PALETTE["bg"])

    # Primary action button — accent fill, white text, generous padding.
    style.configure(
        "Primary.TButton",
        background=PALETTE["accent"],
        foreground=PALETTE["white"],
        font=(FONT_BASE, 11, "bold"),
        padding=(18, 10),
        borderwidth=0,
        focusthickness=0,
    )
    style.map(
        "Primary.TButton",
        background=[("active", PALETTE["accent_dim"]),
                    ("pressed", PALETTE["accent_dim"])],
        foreground=[("disabled", "#CCCCCC")],
    )

    # Secondary button — outlined, neutral.
    style.configure(
        "Secondary.TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        font=(FONT_BASE, 10),
        padding=(14, 8),
        borderwidth=1,
        relief="solid",
        focusthickness=0,
    )
    style.map(
        "Secondary.TButton",
        background=[("active", PALETTE["hover"]), ("pressed", PALETTE["hover"])],
        bordercolor=[("active", PALETTE["accent"])],
    )


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

        # Counter labels — each card has a "N / M" indicator in its header bar.
        # We update these reactively via a trace on each BooleanVar.
        self._sites_count_var: StringVar | None = None
        self._apps_count_var: StringVar | None = None

        _apply_design(root)
        root.title("Ohaiyo — yesterday's session")
        root.geometry("1040x720")
        root.minsize(820, 560)

        self._build_ui()

    # ---------- UI construction --------------------------------------------
    #
    # The layout is a vertical stack:
    #
    #   ┌─────────────────────────────────────────────────────────┐
    #   │ HEADER  ( cat • brand • subtitle      | snapshot meta ) │
    #   ├─────────────────────────────────────────────────────────┤
    #   │ STATS   [ apps ]  [ visits ]  [ top site ]              │
    #   ├─────────────────────────────────────────────────────────┤
    #   │ COLUMNS ┌── Sites ──────────┐  ┌── Apps ──────────────┐ │
    #   │         │ ▍ stripe          │  │ ▍ stripe             │ │
    #   │         │ title    N / M    │  │ title    N / M       │ │
    #   │         │ ── rows ───────── │  │ ── rows ───────────  │ │
    #   │         └───────────────────┘  └──────────────────────┘ │
    #   ├─────────────────────────────────────────────────────────┤
    #   │ FOOTER  cat-status            [Skip][Select][Launch →]  │
    #   └─────────────────────────────────────────────────────────┘
    #
    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=PALETTE["bg"])
        outer.pack(fill="both", expand=True, padx=22, pady=20)

        self._build_header(outer)
        self._build_stats(outer)
        self._build_columns(outer)
        self._build_footer(outer)
        self._update_counts()  # initial counts (everything ticked)

    # ---- header -----------------------------------------------------------
    def _build_header(self, parent: tk.Frame) -> None:
        header = tk.Frame(parent, bg=PALETTE["bg"])
        header.pack(fill="x", pady=(0, 16))

        # Left: cat avatar + brand + subtitle stacked.
        left = tk.Frame(header, bg=PALETTE["bg"])
        left.pack(side="left", anchor="w")

        tk.Label(
            left, text="( =^.^= )", font=("Consolas", 14, "bold"),
            bg=PALETTE["bg"], fg=PALETTE["cat"],
        ).pack(side="left", padx=(0, 12), pady=(6, 0))

        brand_box = tk.Frame(left, bg=PALETTE["bg"])
        brand_box.pack(side="left", anchor="w")

        brand = tk.Frame(brand_box, bg=PALETTE["bg"])
        brand.pack(anchor="w")
        f_reg = (FONT_BASE, 26, "bold")
        f_ital = (FONT_BASE, 26, "bold italic")
        tk.Label(brand, text="Oh", font=f_reg, bg=PALETTE["bg"], fg=PALETTE["text"]).pack(side="left")
        tk.Label(brand, text="ai", font=f_ital, bg=PALETTE["bg"], fg=PALETTE["accent"]).pack(side="left")
        tk.Label(brand, text="yo", font=f_reg, bg=PALETTE["bg"], fg=PALETTE["text"]).pack(side="left")

        tk.Label(
            brand_box, text="Pick up where you left off.",
            font=(FONT_BASE, 10, "italic"),
            bg=PALETTE["bg"], fg=PALETTE["muted"],
        ).pack(anchor="w", pady=(0, 0))

        # Right: snapshot meta — small label + timestamp + filename.
        right = tk.Frame(header, bg=PALETTE["bg"])
        right.pack(side="right", anchor="e")

        tk.Label(
            right, text="SNAPSHOT",
            font=(FONT_BASE, 8, "bold"),
            bg=PALETTE["bg"], fg=PALETTE["subtle"],
        ).pack(anchor="e")
        tk.Label(
            right, text=self.session.get("captured_at", "—"),
            font=(FONT_BASE, 11, "bold"),
            bg=PALETTE["bg"], fg=PALETTE["text"],
        ).pack(anchor="e")
        tk.Label(
            right, text=self.session_path.name,
            font=(FONT_BASE, 9),
            bg=PALETTE["bg"], fg=PALETTE["muted"],
        ).pack(anchor="e")

    # ---- stats chips ------------------------------------------------------
    def _build_stats(self, parent: tk.Frame) -> None:
        """Three chips: APPS count, VISITS count, TOP site domain.

        Chips render the most-loud number on top of a small label, so a glance
        at the row tells you what kind of day yesterday was.
        """
        bar = tk.Frame(parent, bg=PALETTE["bg"])
        bar.pack(fill="x", pady=(0, 14))

        visits = self.session.get("visits", [])
        apps = self.session.get("apps", [])
        domains = Counter(_domain(v.get("url", "")) for v in visits if v.get("url"))
        top_domain = domains.most_common(1)[0][0] if domains else "—"

        def chip(label: str, value: str, accent: str) -> None:
            shell = tk.Frame(
                bar, bg=PALETTE["surface"],
                highlightthickness=1, highlightbackground=PALETTE["border"],
            )
            shell.pack(side="left", padx=(0, 10))

            # Tiny accent bar on the left side gives each chip a categorical hue.
            stripe = tk.Frame(shell, bg=accent, width=4)
            stripe.pack(side="left", fill="y")

            inner = tk.Frame(shell, bg=PALETTE["surface"], padx=14, pady=10)
            inner.pack(side="left")

            tk.Label(
                inner, text=label,
                font=(FONT_BASE, 8, "bold"),
                bg=PALETTE["surface"], fg=PALETTE["subtle"],
            ).pack(anchor="w")
            tk.Label(
                inner, text=value,
                font=(FONT_BASE, 15, "bold"),
                bg=PALETTE["surface"], fg=PALETTE["text"],
            ).pack(anchor="w")

        chip("APPS",   str(len(apps)),   PALETTE["apps"])
        chip("VISITS", str(len(visits)), PALETTE["sites"])
        chip("TOP",    top_domain,       PALETTE["accent"])

    # ---- columns ----------------------------------------------------------
    def _build_columns(self, parent: tk.Frame) -> None:
        body = tk.Frame(parent, bg=PALETTE["bg"])
        body.pack(fill="both", expand=True)

        sites_card, sites_content, self._sites_count_var = self._make_card(
            body, "SITES TO REOPEN", PALETTE["sites"]
        )
        sites_card.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self._fill_urls(sites_content)

        apps_card, apps_content, self._apps_count_var = self._make_card(
            body, "APPS TO REOPEN", PALETTE["apps"]
        )
        apps_card.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._fill_apps(apps_content)

    def _make_card(
        self, parent: tk.Frame, title: str, accent: str,
    ) -> tuple[tk.Frame, tk.Frame, StringVar]:
        """Build a card with a colored top stripe, title bar, and content area.

        Returns ``(card_frame, content_frame, counter_var)``. Counter_var feeds
        the "N / M" indicator in the header — update it via _update_counts.
        """
        wrapper = tk.Frame(
            parent, bg=PALETTE["surface"],
            highlightthickness=1, highlightbackground=PALETTE["border"],
        )

        # Top accent stripe — 3 px of brand color across the full width.
        tk.Frame(wrapper, bg=accent, height=3).pack(fill="x")

        # Header row: title on left, counter on right.
        head = tk.Frame(wrapper, bg=PALETTE["surface"])
        head.pack(fill="x", padx=14, pady=(10, 6))

        tk.Label(
            head, text=title,
            font=(FONT_BASE, 9, "bold"),
            bg=PALETTE["surface"], fg=accent,
        ).pack(side="left")

        counter = StringVar(value="0 / 0")
        tk.Label(
            head, textvariable=counter,
            font=(FONT_BASE, 9),
            bg=PALETTE["surface"], fg=PALETTE["muted"],
        ).pack(side="right")

        # Subtle divider under the header.
        tk.Frame(wrapper, bg=PALETTE["border"], height=1).pack(fill="x", padx=14)

        # Content area is itself scrollable.
        content = self._scrollable_surface(wrapper)

        return wrapper, content, counter

    def _scrollable_surface(self, parent: tk.Frame) -> tk.Frame:
        """Scrollable region whose background matches a card surface."""
        host = tk.Frame(parent, bg=PALETTE["surface"])
        host.pack(fill="both", expand=True, padx=2, pady=(4, 4))

        cv = tk.Canvas(host, bg=PALETTE["surface"], highlightthickness=0)
        sb = tk.Scrollbar(host, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, bg=PALETTE["surface"])

        inner.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=inner, anchor="nw",
                         width=0)  # will resize via bind below
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Make inner frame match canvas width so row labels stretch correctly.
        def _on_canvas_resize(e):
            cv.itemconfigure(cv.find_all()[0], width=e.width)
        cv.bind("<Configure>", _on_canvas_resize)

        # Mouse-wheel scroll, but only when the cursor is over this card.
        def _on_wheel(e):
            cv.yview_scroll(int(-1 * (e.delta / 120)), "units")
        inner.bind("<Enter>", lambda _e: cv.bind_all("<MouseWheel>", _on_wheel))
        inner.bind("<Leave>", lambda _e: cv.unbind_all("<MouseWheel>"))
        return inner

    def _item_row(
        self, parent: tk.Frame, primary: str, secondary: str, var: BooleanVar,
    ) -> None:
        """One clickable row with primary text, muted subtitle, and hover."""
        row = tk.Frame(parent, bg=PALETTE["surface"], padx=12, pady=6)
        row.pack(fill="x")

        def set_bg(color: str) -> None:
            row.configure(bg=color)
            for child in row.winfo_children():
                try:
                    child.configure(bg=color)
                except tk.TclError:
                    pass
                # text labels inside the right column also need recoloring
                for grand in child.winfo_children():
                    try:
                        grand.configure(bg=color)
                    except tk.TclError:
                        pass

        cb = tk.Checkbutton(
            row, variable=var,
            bg=PALETTE["surface"], activebackground=PALETTE["hover"],
            highlightthickness=0, bd=0, takefocus=0,
        )
        cb.pack(side="left", padx=(0, 8))

        text = tk.Frame(row, bg=PALETTE["surface"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(
            text, text=primary, anchor="w",
            font=(FONT_BASE, 10, "bold"),
            bg=PALETTE["surface"], fg=PALETTE["text"],
        ).pack(fill="x")
        if secondary:
            tk.Label(
                text, text=secondary, anchor="w",
                font=(FONT_BASE, 9),
                bg=PALETTE["surface"], fg=PALETTE["muted"],
            ).pack(fill="x")

        # Click anywhere on the row toggles the checkbox.
        def toggle(_e=None):
            var.set(not var.get())

        for w in (row, text, *text.winfo_children()):
            w.bind("<Button-1>", toggle)
            w.bind("<Enter>", lambda _e: set_bg(PALETTE["hover"]))
            w.bind("<Leave>", lambda _e: set_bg(PALETTE["surface"]))

    def _fill_urls(self, parent: tk.Frame) -> None:
        urls = _pick_top_urls(self.session.get("visits", []), MAX_URLS_SHOWN)
        if not urls:
            tk.Label(
                parent, text="(no history found)",
                font=(FONT_BASE, 10, "italic"),
                bg=PALETTE["surface"], fg=PALETTE["muted"],
            ).pack(pady=20)
            return
        for v in urls:
            var = BooleanVar(value=True)
            # Reactive: counter refreshes whenever any var changes.
            var.trace_add("write", lambda *_a: self._update_counts())
            primary = _domain(v["url"]) or v["url"][:40]
            secondary = (v.get("title") or v["url"])[:90]
            self._item_row(parent, primary, secondary, var)
            self.url_vars.append((var, v))

    def _fill_apps(self, parent: tk.Frame) -> None:
        apps = self.session.get("apps", [])
        if not apps:
            tk.Label(
                parent, text="(no apps captured)",
                font=(FONT_BASE, 10, "italic"),
                bg=PALETTE["surface"], fg=PALETTE["muted"],
            ).pack(pady=20)
            return
        for a in apps:
            var = BooleanVar(value=True)
            var.trace_add("write", lambda *_a: self._update_counts())
            primary = a["name"]
            secondary = a["window_titles"][0] if a.get("window_titles") else ""
            self._item_row(parent, primary, secondary, var)
            self.app_vars.append((var, a))

    # ---- footer -----------------------------------------------------------
    def _build_footer(self, parent: tk.Frame) -> None:
        footer = tk.Frame(parent, bg=PALETTE["bg"])
        footer.pack(fill="x", pady=(16, 0))

        # Subtle divider above the footer.
        tk.Frame(parent, bg=PALETTE["border"], height=1).pack(
            fill="x", before=footer, pady=(0, 12)
        )

        self.status = StringVar(
            value=f"{_LAUNCH_CAT_IDLE}  tick what you want, untick what was noise."
        )
        tk.Label(
            footer, textvariable=self.status,
            font=(FONT_BASE, 11),
            bg=PALETTE["bg"], fg=PALETTE["muted"], anchor="w",
        ).pack(side="left")

        btns = tk.Frame(footer, bg=PALETTE["bg"])
        btns.pack(side="right")

        ttk.Button(
            btns, text="Skip all", style="Secondary.TButton",
            command=self._uncheck_all,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            btns, text="Select all", style="Secondary.TButton",
            command=self._check_all,
        ).pack(side="left", padx=(0, 12))
        ttk.Button(
            btns, text="✦  Launch selected", style="Primary.TButton",
            command=self._launch,
        ).pack(side="left")

    # ---- counters ---------------------------------------------------------
    def _update_counts(self) -> None:
        """Refresh the "N / M" indicators in each card header."""
        if self._sites_count_var is not None:
            sel = sum(1 for v, _ in self.url_vars if v.get())
            self._sites_count_var.set(f"{sel} / {len(self.url_vars)}")
        if self._apps_count_var is not None:
            sel = sum(1 for v, _ in self.app_vars if v.get())
            self._apps_count_var.set(f"{sel} / {len(self.app_vars)}")

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
