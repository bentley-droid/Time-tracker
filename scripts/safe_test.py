"""
Run a Python snippet against a SNAPSHOT of the live timetracker.db so
smoke tests can never corrupt real data.

Usage from the command line:
    py -3 scripts\safe_test.py path\to\test_script.py
    py -3 scripts\safe_test.py -c "import main; print(main.DB_INSTANCE.list_categories())"

What it does:
1. Copies   timetracker.db   ->   timetracker.db.live-snapshot   (the safe backup)
2. Runs the test against the live DB file (in-place modifications are fine,
   the snapshot is the rollback target).
3. ALWAYS restores from the snapshot on exit — even on crash, Ctrl+C, or
   uncaught exception — via atexit + signal handlers.
4. Deletes the snapshot only after a successful restore.

If the script aborts before restore (e.g. power loss), the snapshot stays
on disk and you can restore manually:
    copy timetracker.db.live-snapshot timetracker.db
"""

from __future__ import annotations

import argparse
import atexit
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "timetracker.db"
BACKUPS = ROOT / "backups"
BACKUPS.mkdir(exist_ok=True)
SNAPSHOT = BACKUPS / "timetracker.db.live-snapshot"

_restored = False


def snapshot():
    if not LIVE.exists():
        print(f"[safe_test] no live DB at {LIVE}, nothing to snapshot")
        return
    shutil.copy2(LIVE, SNAPSHOT)
    print(f"[safe_test] snapshotted {LIVE.name} -> {SNAPSHOT.name} ({LIVE.stat().st_size} bytes)")


def restore():
    global _restored
    if _restored:
        return
    if SNAPSHOT.exists():
        shutil.copy2(SNAPSHOT, LIVE)
        SNAPSHOT.unlink()
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
