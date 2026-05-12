"""Splash window — a small greeting that pops before the main launcher.

By default the splash shows an ASCII cat that "blinks" across 4 frames.
If you drop ``assets/cat.gif`` into the project, the splash will play that
animated GIF instead (Tk's PhotoImage handles GIFs natively, one frame at a
time — we cycle indexes to animate).

The window is:
  - undecorated (no titlebar) for a clean splash look
  - centered on the primary screen
  - always-on-top while it's alive
  - click-to-dismiss
  - auto-closing after ``duration_ms`` milliseconds
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional
import tkinter as tk
from tkinter import ttk

# ---- look & feel ----------------------------------------------------------
_CREAM = "#FFF6E5"   # warm pastel background
_BORDER = "#F0CFA0"  # subtle peach border
_BRAND = "#3A2E2A"   # deep brown for "Oh" + "yo!"
_ACCENT = "#5B8DEF"  # blue used for the italic "ai" — same as main UI
_SUBTLE = "#7A6657"  # tagline color
_CAT = "#5A3E2E"     # cat face color

# Four ASCII frames — pure ASCII so it renders identically on every machine.
# Frame 1 + 3 = neutral, frame 2 = blink, frame 4 = excited.
_CAT_FRAMES = [
    r"( =^.^= )",
    r"( =-.-= )",
    r"( =^.^= )",
    r"( =^o^= )",
]


class Splash:
    """A short-lived borderless window.

    Parameters
    ----------
    parent : tk.Misc
        The root Tk instance. We need a parent because Toplevel windows
        attach to one.
    on_close : callable, optional
        Called after the splash is destroyed. Use this to chain into
        building the main launcher window.
    duration_ms : int
        How long the splash stays up before auto-closing.
    gif_path : Path | None
        Optional path to a GIF asset. If it exists and Tk can read it,
        we play it instead of the ASCII cat.
    """

    def __init__(
        self,
        parent: tk.Misc,
        on_close: Optional[Callable[[], None]] = None,
        duration_ms: int = 1800,
        gif_path: Optional[Path] = None,
    ) -> None:
        self._on_close = on_close
        self._closed = False

        # Toplevel + remove titlebar.
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=_CREAM)

        # Center on the primary screen.
        w, h = 360, 220
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = (sw - w) // 2, (sh - h) // 2
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        # Soft border via an inner frame with a highlight ring.
        inner = tk.Frame(
            self.win,
            bg=_CREAM,
            highlightthickness=2,
            highlightbackground=_BORDER,
        )
        inner.pack(fill="both", expand=True)

        # ----- cat (GIF if available, else ASCII fallback) -----
        self._gif_frames: list[tk.PhotoImage] = []
        self._frame_idx = 0
        self.cat_widget: tk.Widget

        if gif_path and gif_path.exists():
            self._load_gif_frames(gif_path)

        if self._gif_frames:
            self.cat_widget = tk.Label(inner, image=self._gif_frames[0], bg=_CREAM)
        else:
            self.cat_widget = tk.Label(
                inner,
                text=_CAT_FRAMES[0],
                font=("Consolas", 28, "bold"),
                bg=_CREAM,
                fg=_CAT,
            )
        self.cat_widget.pack(pady=(26, 6))

        # ----- brand row: "Oh" + italic "ai" + "yo!" -----
        brand = tk.Frame(inner, bg=_CREAM)
        brand.pack()
        f_reg = ("Segoe UI", 22, "bold")
        f_ital = ("Segoe UI", 22, "bold italic")
        tk.Label(brand, text="Oh", font=f_reg, bg=_CREAM, fg=_BRAND).pack(side="left")
        tk.Label(brand, text="ai", font=f_ital, bg=_CREAM, fg=_ACCENT).pack(side="left")
        tk.Label(brand, text="yo!", font=f_reg, bg=_CREAM, fg=_BRAND).pack(side="left")

        # ----- subtitle -----
        tk.Label(
            inner,
            text="Welcome back. Let's pick up where you left off.",
            font=("Segoe UI", 9, "italic"),
            bg=_CREAM,
            fg=_SUBTLE,
        ).pack(pady=(8, 0))

        # Click anywhere to dismiss early.
        for w_ in (self.win, inner, self.cat_widget):
            w_.bind("<Button-1>", lambda _e: self.close())

        # Kick off the animation + auto-close.
        self._tick()
        self.win.after(duration_ms, self.close)

    # ---- helpers ----------------------------------------------------------
    def _load_gif_frames(self, path: Path) -> None:
        """Pull every frame out of an animated GIF using Tk's native loader.

        Tk's PhotoImage exposes individual GIF frames via the
        ``format='gif -index N'`` syntax. We keep loading until it errors,
        which signals "no more frames".
        """
        i = 0
        while True:
            try:
                frame = tk.PhotoImage(file=str(path), format=f"gif -index {i}")
            except tk.TclError:
                break
            self._gif_frames.append(frame)
            i += 1

    def _tick(self) -> None:
        """Advance one animation frame, schedule the next."""
        if self._closed or not self.win.winfo_exists():
            return
        if self._gif_frames:
            self._frame_idx = (self._frame_idx + 1) % len(self._gif_frames)
            self.cat_widget.configure(image=self._gif_frames[self._frame_idx])
            delay = 100  # GIFs usually look smoother at ~100ms/frame
        else:
            self._frame_idx = (self._frame_idx + 1) % len(_CAT_FRAMES)
            self.cat_widget.configure(text=_CAT_FRAMES[self._frame_idx])
            delay = 240
        self.win.after(delay, self._tick)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.win.winfo_exists():
            self.win.destroy()
        if self._on_close is not None:
            self._on_close()
