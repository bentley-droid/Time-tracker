"""
One-time recovery for 2026-05-27 entries that were wiped by careless smoke tests.

Strategy:
- 12 screenshots from 11:49 AM through 2:28 PM = real desk work. Re-classify
  each with Claude (same hierarchical prompt the live tracker uses) and insert
  with source='recovered' so the dashboard makes it obvious these were
  reconstructed.
- 14:43 PM onward = Bentley was at the FitCon booth setup until ~6:30 PM
  (he was tracking that time on his phone via a timer that never landed).
  Insert ONE entry covering 2:45 PM to 6:30 PM (225 min) as Operations / Events.
  Don't classify those screenshots — they'd just show his idle Klaviyo tab.

Idempotent: deletes any prior source='recovered' rows for 2026-05-27 before
running, so it's safe to re-run if a classification looks off.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main  # noqa: E402  imports DB_INSTANCE, classify_screenshot, CONFIG

SDIR = Path(main.CONFIG["screenshot_dir"])

# Boundary between "real work at desk" and "at FitCon booth"
FITCON_START = datetime(2026, 5, 27, 14, 45, 0)
FITCON_END = datetime(2026, 5, 27, 18, 30, 0)
FITCON_DURATION_MIN = int((FITCON_END - FITCON_START).total_seconds() / 60)  # 225


def parse_ts(name: str) -> datetime:
    return datetime.strptime(name[:15], "%Y%m%d_%H%M%S")


def main_run():
    files = sorted(SDIR.glob("20260527_*.jpg"))
    if not files:
        print("no 2026-05-27 screenshots to recover")
        return

    # Clean up any prior partial recovery so this is idempotent
    with main.DB_INSTANCE.lock, main.DB_INSTANCE._connect() as conn:
        deleted = conn.execute(
            "DELETE FROM entries WHERE source = 'recovered' AND timestamp LIKE '2026-05-27%'"
        ).rowcount
        conn.commit()
        if deleted:
            print(f"[recover] removed {deleted} previous recovered rows")

    interval = int(main.CONFIG.get("screenshot_interval_minutes", 15))
    tree = main.DB_INSTANCE.get_tree()

    desk_files = [f for f in files if parse_ts(f.name) < FITCON_START]
    booth_files = [f for f in files if parse_ts(f.name) >= FITCON_START]

    print(f"[recover] {len(desk_files)} desk screenshots to classify, "
          f"{len(booth_files)} booth screenshots will be folded into one FitCon entry")
    print()

    # Re-classify desk screenshots
    for f in desk_files:
        ts = parse_ts(f.name)
        jpeg = f.read_bytes()
        try:
            result = main.classify_screenshot(jpeg, tree)
        except Exception as e:
            print(f"  {ts.strftime('%I:%M %p')}  ERROR classifying {f.name}: {e}")
            continue

        parent = (result.get("category") or "Admin/Other").strip()
        sub = (result.get("subcategory") or parent).strip()
        desc = result.get("description", "")

        main.DB_INSTANCE.add_category(parent, parent=None)
        if sub != parent and sub not in tree.get(parent, []):
            main.DB_INSTANCE.add_category(sub, parent=parent)
            tree = main.DB_INSTANCE.get_tree()  # refresh so we keep proposing under the new sub

        eid = main.DB_INSTANCE.log_entry(
            category=parent, subcategory=sub, description=desc,
            confidence=float(result.get("confidence", 0.5)),
            duration_minutes=interval, source="recovered",
            screenshot_path=str(f), timestamp=ts,
        )
        print(f"  {ts.strftime('%I:%M %p')}  #{eid}  {parent} / {sub}  ::  {desc[:70]}")
        time.sleep(0.4)  # gentle rate limit

    # Insert one big FitCon entry
    main.DB_INSTANCE.add_category("Operations", parent=None)
    main.DB_INSTANCE.add_category("Events", parent="Operations")
    fitcon_id = main.DB_INSTANCE.log_entry(
        category="Operations", subcategory="Events",
        description="Setting up FitCon booth (tracked on phone via timer; recovered from memory)",
        confidence=1.0, duration_minutes=FITCON_DURATION_MIN,
        source="recovered", screenshot_path=None, timestamp=FITCON_START,
    )
    print(f"\n  {FITCON_START.strftime('%I:%M %p')}  #{fitcon_id}  "
          f"Operations / Events  ::  FitCon booth setup ({FITCON_DURATION_MIN} min)")

    # Summary
    with main.DB_INSTANCE._connect() as conn:
        total = conn.execute(
            "SELECT SUM(duration_minutes) FROM entries WHERE timestamp LIKE '2026-05-27%'"
        ).fetchone()[0] or 0
    print(f"\n[recover] done. 2026-05-27 now totals {total} min ({total/60:.1f} h).")


if __name__ == "__main__":
    main_run()
