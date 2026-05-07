"""Snapshot running processes that own a visible window.

We list visible top-level windows via Win32, map each to its process, then
collapse to one entry per executable. The result is what a human would call
"apps that are open" — not raw processes.

Falls back to bare psutil if pywin32 isn't available, in which case we lose
window titles but still get app names.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path

import psutil

try:
    import win32gui  # type: ignore
    import win32process  # type: ignore
    HAVE_WIN32 = True
except ImportError:
    HAVE_WIN32 = False


@dataclass
class AppEntry:
    name: str            # executable name, e.g. "Code.exe"
    exe_path: str | None  # full path so we can relaunch it
    window_titles: list[str] = field(default_factory=list)
    pid: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _visible_window_pids() -> dict[int, list[str]]:
    """Return {pid: [window_title, ...]} for visible top-level windows."""
    if not HAVE_WIN32:
        return {}

    result: dict[int, list[str]] = {}

    def cb(hwnd: int, _) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return True
        result.setdefault(pid, []).append(title)
        return True

    win32gui.EnumWindows(cb, None)
    return result


def snapshot(ignore_processes: list[str] | None = None) -> list[AppEntry]:
    ignore = {p.lower() for p in (ignore_processes or [])}
    pid_to_titles = _visible_window_pids()

    if HAVE_WIN32:
        # Only include processes that own at least one visible window.
        candidate_pids = set(pid_to_titles.keys())
    else:
        # Without pywin32 we can't tell which processes have windows; include all.
        candidate_pids = {p.pid for p in psutil.process_iter(["pid"])}

    by_exe: dict[str, AppEntry] = {}
    for pid in candidate_pids:
        try:
            proc = psutil.Process(pid)
            name = proc.name()
            if name.lower() in ignore:
                continue
            try:
                exe_path = proc.exe()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                exe_path = None

            entry = by_exe.setdefault(
                name,
                AppEntry(name=name, exe_path=exe_path, pid=pid, window_titles=[]),
            )
            for t in pid_to_titles.get(pid, []):
                if t not in entry.window_titles:
                    entry.window_titles.append(t)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return sorted(by_exe.values(), key=lambda a: a.name.lower())
