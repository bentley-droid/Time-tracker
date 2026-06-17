"""
One-time reclassification of historical Admin/Other entries using the updated
classification prompt that looks THROUGH the AI tool to the underlying work.

Targets every entry currently categorized as 'Admin/Other' with a screenshot
still on disk. Re-classifies each with the live (new) prompt. If the model
returns a different category, the row is updated in place; source is set to
'reclassified' so you can spot the changes in the timeline / dashboard.
Entries that the model still considers Admin/Other are left untouched.

Idempotent: safe to re-run if the prompt changes again.
Snapshots the DB to timetracker.db.pre-reclassify before running, in case
you want to roll back.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main  # noqa: E402

(ROOT / "backups").mkdir(exist_ok=True)
SNAPSHOT = ROOT / "backups" / "timetracker.db.pre-reclassify"


def main_run():
    live = Path(main.CONFIG["database_path"])
    shutil.copy2(live, SNAPSHOT)
    print(f"[reclassify] DB snapshot saved: {SNAPSHOT.name}")
    print(f"[reclassify] (roll back any time with: copy {SNAPSHOT.name} {live.name})\n")

    # Pull candidates
    with main.DB_INSTANCE._connect() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT id, timestamp, category, subcategory, description, source, screenshot_path
            FROM entries
            WHERE category = 'Admin/Other'
              AND screenshot_path IS NOT NULL AND screenshot_path != ''
            ORDER BY timestamp
        """).fetchall()]

    rows = [r for r in rows if Path(r["screenshot_path"]).exists()]
    print(f"[reclassify] {len(rows)} candidate entries\n")

    tree = main.DB_INSTANCE.get_tree()
    changed = 0
    unchanged = 0
    failed = 0

    for r in rows:
        ts_label = r["timestamp"][:16].replace("T", " ")
        try:
            jpeg = Path(r["screenshot_path"]).read_bytes()
            result = main.classify_screenshot(jpeg, tree)
        except Exception as e:
            failed += 1
            print(f"  {ts_label}  #{r['id']:>3}  ERROR: {e}")
            continue

        new_parent = (result.get("category") or "Admin/Other").strip()
        new_sub = (result.get("subcategory") or new_parent).strip()
        new_desc = result.get("description", "")

        if new_parent == "Admin/Other":
            unchanged += 1
            print(f"  {ts_label}  #{r['id']:>3}  kept Admin/Other > {new_sub}")
            time.sleep(0.3)
            continue

        # Ensure category exists in tree, then update the row
        main.DB_INSTANCE.add_category(new_parent, parent=None)
        if new_sub != new_parent and new_sub not in tree.get(new_parent, []):
            main.DB_INSTANCE.add_category(new_sub, parent=new_parent)
            tree = main.DB_INSTANCE.get_tree()

        with main.DB_INSTANCE.lock, main.DB_INSTANCE._connect() as conn:
            conn.execute(
                """UPDATE entries
                   SET category=?, subcategory=?, description=?, source='reclassified'
                   WHERE id=?""",
                (new_parent, new_sub, new_desc, r["id"]),
            )
            conn.commit()

        changed += 1
        old = f"Admin/Other > {r['subcategory']}"
        print(f"  {ts_label}  #{r['id']:>3}  {old}  ->  {new_parent} > {new_sub}")
        time.sleep(0.3)

    print(f"\n[reclassify] done. changed={changed}  unchanged={unchanged}  failed={failed}")
    print(f"[reclassify] roll back any time with: copy {SNAPSHOT.name} {live.name}")


if __name__ == "__main__":
    main_run()
