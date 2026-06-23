r"""
Run a Python snippet against a SNAPSHOT of the live timetracker.db so
smoke tests can never corrupt real data.

Usage from the command line:
    py -3 scripts\safe_test.py path\to\test_script.py
    py -3 scripts\safe_test.py -c "import main; print(main.DB_INSTANCE.list_categories())"

What it does:
1. Snapshots timetracker.db -> backups/timetracker.db.live-snapshot using SQLite's
   online BACKUP API (NOT a file copy — the DB is in WAL mode, and a plain
   shutil.copy of a live WAL DB can produce a torn/malformed file).
2. Runs the test against the live DB file.
3. ALWAYS restores from the snapshot on exit — even on crash/Ctrl+C — via the
   backup API again (page-level, consistent), via atexit + signal handlers.
4. Deletes the snapshot only after a successful restore.

WAL safety: never shutil.copy the live DB and never copy a file *over* the live
DB while a tracker has it open — both corrupt it. The backup API coordinates with
any live writer. If this aborts before restore, restore manually:
    py -3 -c "import sqlite3; s=sqlite3.connect('backups/timetracker.db.live-snapshot'); d=sqlite3.connect('timetracker.db'); s.backup(d)"
"""

from __future__ import annotations

import argparse
import atexit
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "timetracker.db"
BACKUPS = ROOT / "backups"
BACKUPS.mkdir(exist_ok=True)
SNAPSHOT = BACKUPS / "timetracker.db.live-snapshot"

_restored = False


def _sqlite_backup(src: Path, dst: Path):
    """Consistent, WAL-safe copy of an SQLite DB from src to dst."""
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        with d:
            s.backup(d)
    finally:
        s.close()
        d.close()


def _cleanup(p: Path):
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(p) + suffix)
        if f.exists():
            f.unlink()


def snapshot():
    if not LIVE.exists():
        print(f"[safe_test] no live DB at {LIVE}, nothing to snapshot")
        return
    _cleanup(SNAPSHOT)
    _sqlite_backup(LIVE, SNAPSHOT)
    print(f"[safe_test] snapshotted {LIVE.name} -> {SNAPSHOT.name} (backup API)")


def restore():
    global _restored
    if _restored:
        return
    if SNAPSHOT.exists():
        _sqlite_backup(SNAPSHOT, LIVE)   # page-level restore INTO the live db (safe under WAL)
        _cleanup(SNAPSHOT)
        print(f"[safe_test] restored live DB from snapshot")
    _restored = True


def _signal_handler(signum, frame):
    restore()
    sys.exit(128 + signum)


def main():
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("script", nargs="?", help="path to a python file to run")
    g.add_argument("-c", "--command", help="inline python source to run")
    args = parser.parse_args()

    snapshot()
    atexit.register(restore)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _signal_handler)

    try:
        if args.command:
            cmd = [sys.executable, "-c", args.command]
        else:
            cmd = [sys.executable, str(Path(args.script).resolve())]
        rc = subprocess.call(cmd, cwd=str(ROOT))
        sys.exit(rc)
    finally:
        restore()


if __name__ == "__main__":
    main()
