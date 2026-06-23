# Otishi Time Tracker — notes for Claude sessions

## The database (READ THIS FIRST)

- **The one and only live database is `timetracker.db`** in this folder. Always read/write this file.
- **Ignore everything in `backups/`.** Files like `backups/timetracker.db.pre-*` and
  `backups/timetracker.db.live-snapshot` are point-in-time backups, NOT the current data.
  Never read them as the source of truth and never present their contents as "the latest."
- `timetracker.db-wal` and `timetracker.db-shm` are SQLite WAL sidecar files for the live
  DB. Leave them alone; do not open or copy them as standalone databases.

## Reading the live DB safely

The tracker runs as a background process and writes every ~15 min. The DB is in **WAL mode**,
so concurrent reads do NOT block and will NOT hit "database is locked." Just open
`timetracker.db` normally:

```python
import sqlite3
conn = sqlite3.connect("timetracker.db")          # fine while the tracker runs
# or strictly read-only:
conn = sqlite3.connect("file:timetracker.db?mode=ro", uri=True)
rows = conn.execute("SELECT * FROM entries ORDER BY timestamp DESC LIMIT 20").fetchall()
```

If you ever still see a lock, the tracker is mid-write — just retry; WAL clears it in ms.

## NEVER destructively test against the live DB

Do not run `DELETE`/`DROP`/`UPDATE` against `timetracker.db` for testing. A real session
once wiped a day of data this way. To test code that touches the DB, use the wrapper, which
snapshots to `backups/` and restores afterward:

```
py -3 scripts/safe_test.py -c "import main; ..."
py -3 scripts/safe_test.py path/to/test_script.py
```

## WAL-safe snapshots (IMPORTANT — a plain file copy can corrupt the DB)

The DB is in **WAL mode**. Do **NOT** snapshot with `copy timetracker.db ...` /
`shutil.copy()` while the tracker is running — recent commits live in the
`-wal` sidecar, so a bare copy of the main file can be torn/incomplete, and
copying a file *over* `timetracker.db` while a process has it open corrupts it.
(This actually happened on 2026-06-23 and required a row-by-row salvage.)

For intentional one-off edits (recovery, migrations), snapshot with the SQLite
**backup API**, which is consistent under WAL:

```python
import sqlite3
s = sqlite3.connect("timetracker.db"); d = sqlite3.connect("backups/timetracker.db.pre-<reason>")
with d: s.backup(d)
s.close(); d.close()
```

To restore, back up the other direction (snapshot -> live) the same way — never
file-copy over a live DB. `scripts/safe_test.py` already uses this method.

## Schema quick reference

- `entries(id, timestamp, category, subcategory, description, confidence, duration_minutes, source, screenshot_path)`
  — `source` is one of: `auto`, `manual`, `timer`, `idle`, `backfill`, `recovered`, `reclassified`, `api`.
- `categories(name, parent, created_at, is_user)` — parent is NULL for top-level categories.
- `active_timer(id=1, ...)` — at most one running start/stop timer.
- `clock_state(id=1, clocked_in, since, source)` — clock in/out state; auto-screenshots only run while clocked in.
- `pending_prompts(...)` — unresolved idle-backfill prompts.

## Running the app

- Start: `start_tracker.bat` (Windows) / `./start_tracker.sh` (Mac). Dashboard on `localhost:5555`.
- The Flask server binds `0.0.0.0` (LAN/Tailscale reachable); `/api/log` is localhost-gated.
- Config is `config.json` (contains the API key — don't commit it or paste it into zips).
