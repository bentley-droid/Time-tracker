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

For intentional one-off data edits (recovery, migrations), snapshot first:
`copy timetracker.db backups\timetracker.db.pre-<reason>` (Windows) then make the change.

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
