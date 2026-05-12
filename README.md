# Oh*ai*yo

> Pronounced like *ohayō* (おはよう), Japanese for "good morning." The
> stylized "*ai*" is a nod to the AI-assist that helps build it.

A local-only Windows tool that helps you "resume" yesterday's work in the morning.

It runs two pieces:

1. **Capture daemon** (`capture.py`) — snapshots open processes + browser history into
   `data/session_YYYY-MM-DD_HHMMSS.json`. Run it on a schedule (every 15 min and at logoff
   is a good start).
2. **Morning launcher** (`launcher.py`) — a small GUI you run when you log in. It shows
   yesterday's summary, lets you tick which apps and sites to relaunch, then opens them.

## Phase 1 — Easy path (this version)

- Pulls browser **history** from Chrome and Edge (their SQLite `History` files)
- Lists running processes that own a visible window
- Stores everything as JSON; no extension, no native hooks

This is enough to see "what I was looking at yesterday" with one caveat: history is noisier
than actual open tabs (it includes pages you only glanced at).

## Phase 2 — Clean path (later)

- Read each browser's `Sessions/` folder to get the exact tabs that were open at last close
- Optional browser extension for live tab tracking
- Time-spent estimates per app (via Windows event log or polling)

## Layout

```
Ohaiyo/
├── capture.py          # daemon entry — run on a schedule
├── launcher.py         # morning GUI entry — run at logon
├── config.json         # which browsers to read, ignore lists, etc.
├── core/
│   ├── browsers.py     # Chrome/Edge history readers
│   ├── processes.py    # window + process snapshot
│   ├── storage.py      # JSON read/write
│   └── summary.py      # build "yesterday at a glance" text
├── data/               # session snapshots, one JSON per capture
├── log/                # activity logs, one file per day
└── scripts/
    └── run_capture.bat # entry-point used by Task Scheduler
```

## Install

```
pip install -r requirements.txt
```

## Run

Manually:

```
python capture.py        # take a snapshot now
python launcher.py       # open the morning GUI
```

Scheduled (recommended):
- Task Scheduler → trigger "At log on" and "Every 15 minutes" → action `scripts\run_capture.bat`
- Task Scheduler → trigger "At log on" → action `python launcher.py`

## Demo / same-day testing

By default the launcher only shows snapshots from **before midnight today**
(the "yesterday" definition). For demos or quick iteration, pass `--latest`
to load whatever's newest:

```
python capture.py
# close a few apps to simulate "shutting down"
python launcher.py --latest
# tick what to bring back, click Launch selected
```

## The cat 🐱

The launcher opens with a small splash window: a blinking ASCII cat
`( =^.^= )` and the *Oh*ai*yo!* greeting. Click it (or wait ~2 seconds) and
the main window appears. As you launch apps, the same cat "purrs" through
the status bar one item at a time so you can see what's happening.

**Want a real animated cat?** Drop a GIF at `assets/cat.gif` and the splash
will use it instead — Tk reads each frame natively via
`PhotoImage(format="gif -index N")`. Suggested sources are linked in
`assets/README.md`.
