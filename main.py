"""
Otishi Work Time Tracker
========================
Screenshots every N min -> Claude vision (parent + sub) -> SQLite -> Flask dashboard.
Idle detection skips classification on duplicate screens and prompts to backfill on resume.
Localhost JSON API for external tools (e.g. Cowork skills) to log entries directly.

Run:
    python main.py                       # tracker + dashboard
    python main.py --dashboard           # dashboard only
    python main.py --once                # capture one screenshot and exit (test)
    python main.py --idle-prompt <id>    # internal: opens Tk modal to backfill an idle range
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, time as dtime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    # First run after a fresh clone: seed config.json from the committed example.
    if not CONFIG_PATH.exists():
        example = ROOT / "config.example.json"
        if example.exists():
            import shutil
            shutil.copy2(example, CONFIG_PATH)
            print(f"[config] created config.json from config.example.json")
        else:
            print(f"[config] Missing {CONFIG_PATH}.")
            sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Key may be absent on a fresh install — the app boots and the /setup page
    # collects it. Fall back to the env var if the file has a placeholder/empty key.
    key = cfg.get("anthropic_api_key", "")
    if not key or key.startswith("PASTE_"):
        cfg["anthropic_api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")

    cfg["screenshot_dir"] = str((ROOT / cfg.get("screenshot_dir", "screenshots")).resolve())
    cfg["database_path"] = str((ROOT / cfg.get("database_path", "timetracker.db")).resolve())
    Path(cfg["screenshot_dir"]).mkdir(parents=True, exist_ok=True)

    if "category_tree" not in cfg and "categories" in cfg:
        cfg["category_tree"] = {c: [c] for c in cfg["categories"]}
    cfg.setdefault("category_tree", {})
    cfg.setdefault("idle_detection", {})
    return cfg


def load_profiles() -> dict:
    """Team profiles (Bentley/DJ/Jose + task areas). Committed content, not secret."""
    path = ROOT / "profiles.json"
    if not path.exists():
        return {"profiles": {}, "task_areas": []}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"profiles": {}, "task_areas": []}


CONFIG = load_config()
PROFILES = load_profiles()
IDLE_CFG = CONFIG.get("idle_detection", {})
IDLE_ENABLED = bool(IDLE_CFG.get("enabled", True))
IDLE_THRESHOLD = int(IDLE_CFG.get("similarity_threshold", 6))         # max Hamming distance to count as "same"
IDLE_FLAG_AFTER = int(IDLE_CFG.get("captures_to_flag_idle", 2))       # captures of similar screens before flagging
IDLE_SHOW_POPUP = bool(IDLE_CFG.get("show_popup", True))


def has_api_key() -> bool:
    k = CONFIG.get("anthropic_api_key", "")
    return bool(k) and not k.startswith("PASTE_")


def save_api_key(key: str) -> bool:
    """Persist the Anthropic key to the local (gitignored) config.json and update
    the in-memory CONFIG so capture works immediately, no restart."""
    key = (key or "").strip()
    if not key:
        return False
    try:
        raw = json.load(open(CONFIG_PATH, encoding="utf-8"))
        raw["anthropic_api_key"] = key
        json.dump(raw, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
    except Exception as e:
        print(f"[config] could not save API key: {e}")
        return False
    CONFIG["anthropic_api_key"] = key
    return True


# ---------------------------------------------------------------------------
# Screenshot + perceptual hash
# ---------------------------------------------------------------------------

def capture_screenshot() -> tuple[bytes, str, "Image.Image"]:
    """Grab all monitors, return (jpeg_bytes, saved_path, PIL Image)."""
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            raw = sct.grab(monitor)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except Exception:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True).convert("RGB")

    max_dim = 1600
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path(CONFIG["screenshot_dir"]) / f"{ts}.jpg"
    img.save(path, format="JPEG", quality=75, optimize=True)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75, optimize=True)
    return buf.getvalue(), str(path), img


def ahash(img) -> int:
    """64-bit average hash (perceptual). Pillow Image in, int out."""
    from PIL import Image as PImage
    small = img.convert("L").resize((8, 8), PImage.LANCZOS)
    pixels = list(small.getdata())
    avg = sum(pixels) / 64.0
    bits = 0
    for i, p in enumerate(pixels):
        if p > avg:
            bits |= (1 << i)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ---------------------------------------------------------------------------
# Claude vision classifier (hierarchical)
# ---------------------------------------------------------------------------

CLASSIFY_SYSTEM_PROMPT = """You analyze screenshots from a person's work computer and categorize what they are doing.

USER CONTEXT:
{user_context}

CATEGORY TREE (parent -> known subcategories):
{tree}

CRITICAL RULE — categorize by the WORK, not by the TOOL.
The user does a lot of their actual business work *through* AI tools (Claude Code, Claude Cowork, Cursor, ChatGPT). When you see those tools on screen, DO NOT default to "Admin/Other > AI Assistant Session". Instead, look at what the AI is being used FOR and categorize that underlying work.

How to identify the underlying work when an AI tool is on screen:
  • TASK/PROMPT TITLE — the AI's UI usually shows the current task name (e.g. "Clone sunset email template", "Build Redo bulk campaign script"). This is the strongest signal.
  • CONNECTED MCP SERVERS / CONNECTORS — Klaviyo → Email Marketing, Shopify → Website/Shopify, Meta/Google Ads → Paid Media, Notion → varies by content, etc.
  • PROJECT / FILE PATHS — `redo-bulk/` → Email Marketing, `otishi-creator-intel/` → Creator/UGC, `otishi-theme/` → Website/Shopify.
  • BROWSER TABS visible at edges of the screen — often reveal the real subject matter.
  • CONVERSATION CONTENT in the chat panel.

Examples:
  • Cowork session with Klaviyo connector cloning an email template → "Email Marketing" / "Klaviyo"
  • Cowork session running a script that scrapes CreatorClub TikToks → "Creator/UGC Management" / "Content Review"
  • Claude Code editing .liquid files in an Otishi theme repo → "Website/Shopify" / "Theme/Liquid Edits"
  • Claude analyzing a Meta ads CSV → "Paid Media" / "Meta Ads"
  • Claude Cowork with no clear business connector, configuring the AI itself, or troubleshooting tooling → "Admin/Other" / "AI Assistant Session"

ONLY use "Admin/Other > AI Assistant Session" when the work is genuinely meta (configuring connectors, debugging the AI tool, generic questions unrelated to a specific business area). Most AI-tool screenshots should be categorized by the underlying business work.

Other rules:
- Pick the single best PARENT category from the tree above. Only invent a new parent if NOTHING fits — that should be rare.
- Pick a SUBCATEGORY under that parent. If none of the listed subs fit, propose a new short subcategory name (2-4 words, Title Case).
- Write a one-sentence specific description that names BOTH the underlying work and (briefly) the tool — e.g. "Cloning the Sunset email template in Klaviyo via Claude Cowork".
- If the screen shows entertainment, an idle/lock screen, or clearly non-work content, use parent "Break / Idle".

Return ONLY valid JSON:
{{
  "category": "<parent category>",
  "subcategory": "<subcategory>",
  "is_new_category": <true|false>,
  "is_new_subcategory": <true|false>,
  "description": "<one specific sentence>",
  "confidence": <0.0-1.0>
}}"""


def _format_tree(tree: dict[str, list[str]]) -> str:
    lines = []
    for parent in sorted(tree.keys()):
        lines.append(f"- {parent}")
        for s in tree[parent] or []:
            lines.append(f"    - {s}")
    return "\n".join(lines) or "(no categories defined yet)"


def get_user_context() -> str:
    """Effective classification context: the selected profile's, else config default."""
    try:
        s = DB_INSTANCE.get_settings()
        if s.get("configured") and s.get("user_context"):
            return s["user_context"]
    except Exception:
        pass
    return CONFIG.get("user_context", "")


def classify_screenshot(jpeg_bytes: bytes, tree: dict[str, list[str]]) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=CONFIG["anthropic_api_key"])
    system = CLASSIFY_SYSTEM_PROMPT.format(
        user_context=get_user_context(),
        tree=_format_tree(tree),
    )
    b64 = base64.standard_b64encode(jpeg_bytes).decode()

    resp = client.messages.create(
        model=CONFIG.get("model", "claude-sonnet-4-6"),
        max_tokens=500,
        system=system,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": "Classify this screenshot."},
            ],
        }],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"category": "Admin/Other", "subcategory": "Misc",
                  "is_new_category": False, "is_new_subcategory": False,
                  "description": f"[unparseable: {raw[:120]}]", "confidence": 0.0}

    result.setdefault("category", "Admin/Other")
    result.setdefault("subcategory", result["category"])
    result.setdefault("description", "")
    result.setdefault("confidence", 0.5)
    return result


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class DB:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        # WAL = readers never block the writer (and vice-versa), so other
        # processes/Claude sessions can read the live DB while the tracker runs.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entries (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    category        TEXT NOT NULL,
                    subcategory     TEXT,
                    description     TEXT,
                    confidence      REAL,
                    duration_minutes INTEGER NOT NULL DEFAULT 15,
                    source          TEXT NOT NULL DEFAULT 'auto',
                    screenshot_path TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    name       TEXT PRIMARY KEY,
                    parent     TEXT,
                    created_at TEXT NOT NULL,
                    is_user    INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_timer (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    category    TEXT NOT NULL,
                    subcategory TEXT,
                    description TEXT,
                    started_at  TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    id           INTEGER PRIMARY KEY CHECK (id = 1),
                    profile_id   TEXT,
                    display_name TEXT,
                    user_context TEXT,
                    configured   INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS clock_state (
                    id         INTEGER PRIMARY KEY CHECK (id = 1),
                    clocked_in INTEGER NOT NULL DEFAULT 0,
                    since      TEXT,
                    source     TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_prompts (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    start_ts   TEXT NOT NULL,
                    end_ts     TEXT NOT NULL,
                    entry_ids  TEXT NOT NULL,
                    resolved   INTEGER NOT NULL DEFAULT 0
                )
            """)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
            if "subcategory" not in cols:
                conn.execute("ALTER TABLE entries ADD COLUMN subcategory TEXT")
            cat_cols = {r[1] for r in conn.execute("PRAGMA table_info(categories)").fetchall()}
            if "parent" not in cat_cols:
                conn.execute("ALTER TABLE categories ADD COLUMN parent TEXT")
            conn.commit()

        for parent, subs in CONFIG.get("category_tree", {}).items():
            self.add_category(parent, parent=None)
            for sub in subs:
                self.add_category(sub, parent=parent)

    def add_category(self, name: str, parent: str | None = None, user_added: bool = False):
        name = (name or "").strip()
        if not name:
            return
        with self.lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT name, parent FROM categories WHERE name = ?", (name,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO categories (name, parent, created_at, is_user) VALUES (?, ?, ?, ?)",
                    (name, parent, datetime.now().isoformat(), 1 if user_added else 0),
                )
            elif existing["parent"] is None and parent is not None:
                conn.execute("UPDATE categories SET parent = ? WHERE name = ?", (parent, name))
            conn.commit()

    def get_tree(self) -> dict[str, list[str]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT name, parent FROM categories").fetchall()
        parents = [r["name"] for r in rows if r["parent"] is None]
        tree: dict[str, list[str]] = {p: [] for p in parents}
        for r in rows:
            if r["parent"]:
                tree.setdefault(r["parent"], []).append(r["name"])
        for p in tree:
            tree[p].sort()
        return dict(sorted(tree.items()))

    def log_entry(self, category, subcategory=None, description="", confidence=1.0,
                  duration_minutes=15, source="auto", screenshot_path=None, timestamp=None) -> int:
        ts = (timestamp or datetime.now()).isoformat()
        with self.lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO entries
                   (timestamp, category, subcategory, description, confidence, duration_minutes, source, screenshot_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, category, subcategory, description, confidence, duration_minutes, source, screenshot_path),
            )
            conn.commit()
            return cur.lastrowid

    def query_range(self, start: datetime, end: datetime) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM entries WHERE timestamp >= ? AND timestamp <= ?
                   ORDER BY timestamp ASC""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_entry(self, entry_id: int):
        with self.lock, self._connect() as conn:
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            conn.commit()

    def update_entry(self, entry_id, category, subcategory, description, duration_minutes):
        with self.lock, self._connect() as conn:
            conn.execute(
                "UPDATE entries SET category=?, subcategory=?, description=?, duration_minutes=? WHERE id=?",
                (category, subcategory, description, duration_minutes, entry_id),
            )
            conn.commit()

    def get_entries_by_ids(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        qs = ",".join("?" * len(ids))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM entries WHERE id IN ({qs}) ORDER BY timestamp ASC", ids
            ).fetchall()
        return [dict(r) for r in rows]

    def set_entry_duration(self, entry_id: int, duration_minutes: int):
        with self.lock, self._connect() as conn:
            conn.execute(
                "UPDATE entries SET duration_minutes=? WHERE id=?",
                (duration_minutes, entry_id),
            )
            conn.commit()

    def shift_entries(self, ids: list[int], delta: timedelta):
        """Shift every listed entry's timestamp by the same delta (preserves spacing)."""
        with self.lock, self._connect() as conn:
            for eid in ids:
                row = conn.execute("SELECT timestamp FROM entries WHERE id=?", (eid,)).fetchone()
                if row:
                    new_ts = (datetime.fromisoformat(row["timestamp"]) + delta).isoformat()
                    conn.execute("UPDATE entries SET timestamp=? WHERE id=?", (new_ts, eid))
            conn.commit()

    # -- profile / settings --
    def get_settings(self) -> dict:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
        if not r:
            return {"profile_id": None, "display_name": None, "user_context": None, "configured": False}
        return {"profile_id": r["profile_id"], "display_name": r["display_name"],
                "user_context": r["user_context"], "configured": bool(r["configured"])}

    def set_profile(self, profile_id: str, display_name: str, user_context: str):
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (id, profile_id, display_name, user_context, configured) "
                "VALUES (1, ?, ?, ?, 1)",
                (profile_id, display_name, user_context),
            )
            conn.commit()
        return self.get_settings()

    # -- clock in / out --
    def get_clock_state(self) -> dict:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM clock_state WHERE id = 1").fetchone()
        if not r:
            return {"clocked_in": False, "since": None, "source": None}
        return {"clocked_in": bool(r["clocked_in"]), "since": r["since"], "source": r["source"]}

    def is_clocked_in(self) -> bool:
        return self.get_clock_state()["clocked_in"]

    def set_clock(self, clocked_in: bool, source: str = "manual") -> dict:
        cur = self.get_clock_state()
        if clocked_in:
            # preserve the original since if already clocked in (idempotent)
            since = cur["since"] if (cur["clocked_in"] and cur["since"]) else datetime.now().isoformat()
        else:
            since = None
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO clock_state (id, clocked_in, since, source) VALUES (1, ?, ?, ?)",
                (1 if clocked_in else 0, since, source),
            )
            conn.commit()
        return self.get_clock_state()

    # -- active timer (one at a time) --
    def get_active_timer(self) -> dict | None:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return dict(r) if r else None

    def start_timer(self, category: str, subcategory: str | None, description: str) -> dict:
        """Start a new timer. If one is already running, stop it first (logging the entry)."""
        stopped = self.stop_timer()
        now = datetime.now().isoformat()
        with self.lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_timer (id, category, subcategory, description, started_at) VALUES (1, ?, ?, ?, ?)",
                (category, subcategory or category, description or "", now),
            )
            conn.commit()
        return {"started_at": now, "category": category, "subcategory": subcategory or category,
                "description": description or "", "auto_stopped": stopped}

    def stop_timer(self) -> dict | None:
        """Stop the active timer, log an entry with the elapsed duration. Returns the logged entry, or None if no timer."""
        active = self.get_active_timer()
        if not active:
            return None
        started = datetime.fromisoformat(active["started_at"])
        ended = datetime.now()
        minutes = max(1, round((ended - started).total_seconds() / 60))
        with self.lock, self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO entries
                   (timestamp, category, subcategory, description, confidence, duration_minutes, source, screenshot_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (started.isoformat(), active["category"], active["subcategory"],
                 active["description"], 1.0, minutes, "timer", None),
            )
            # Sweep any auto/idle entries that landed inside the timer window — those would
            # double-count the same minutes against the timer entry we just inserted.
            swept = conn.execute(
                "DELETE FROM entries WHERE source IN ('auto','idle') AND timestamp >= ? AND timestamp < ?",
                (started.isoformat(), ended.isoformat()),
            ).rowcount
            # And resolve any pending idle prompts that fall within the timer window.
            conn.execute(
                "UPDATE pending_prompts SET resolved = 1 WHERE start_ts >= ? AND end_ts <= ?",
                (started.isoformat(), ended.isoformat()),
            )
            conn.execute("DELETE FROM active_timer WHERE id = 1")
            conn.commit()
            eid = cur.lastrowid
        if swept:
            print(f"[timer] swept {swept} overlapping auto/idle entries inside timer window")
        return {"id": eid, "category": active["category"], "subcategory": active["subcategory"],
                "description": active["description"], "duration_minutes": minutes,
                "started_at": active["started_at"], "ended_at": ended.isoformat(),
                "swept_overlap": swept}

    # -- pending prompts (idle backfills) --
    def create_prompt(self, start_ts: str, end_ts: str, entry_ids: list[int]) -> int:
        with self.lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO pending_prompts (created_at, start_ts, end_ts, entry_ids, resolved) VALUES (?, ?, ?, ?, 0)",
                (datetime.now().isoformat(), start_ts, end_ts, json.dumps(entry_ids)),
            )
            conn.commit()
            return cur.lastrowid

    def get_prompt(self, prompt_id: int) -> dict | None:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM pending_prompts WHERE id = ?", (prompt_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["entry_ids"] = json.loads(d["entry_ids"])
        return d

    def list_pending_prompts(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_prompts WHERE resolved = 0 ORDER BY id ASC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["entry_ids"] = json.loads(d["entry_ids"])
            out.append(d)
        return out

    def resolve_prompt(self, prompt_id: int, category: str, subcategory: str, description: str, keep_as_away: bool = False):
        prompt = self.get_prompt(prompt_id)
        if not prompt:
            return False
        with self.lock, self._connect() as conn:
            if not keep_as_away:
                for eid in prompt["entry_ids"]:
                    conn.execute(
                        "UPDATE entries SET category=?, subcategory=?, description=?, source='backfill' WHERE id=?",
                        (category, subcategory, description, eid),
                    )
            conn.execute("UPDATE pending_prompts SET resolved = 1 WHERE id = ?", (prompt_id,))
            conn.commit()
        return True


DB_INSTANCE = DB(CONFIG["database_path"])


# ---------------------------------------------------------------------------
# Idle detection state (thread-local-ish, accessed from capture loop only)
# ---------------------------------------------------------------------------

_idle_state = {
    "last_hash": None,
    "streak": 0,
    "idle_entry_ids": [],
    "idle_start_ts": None,
    "idle_end_ts": None,
}


def _spawn_idle_popup(prompt_id: int):
    """Spawn a separate process showing the Tkinter backfill modal."""
    if not IDLE_SHOW_POPUP:
        return
    try:
        # Use pythonw on Windows so no console flashes
        exe = sys.executable
        if os.name == "nt":
            pyw = Path(exe).with_name("pythonw.exe")
            if pyw.exists():
                exe = str(pyw)
        subprocess.Popen(
            [exe, str(Path(__file__).resolve()), "--idle-prompt", str(prompt_id)],
            close_fds=True,
        )
    except Exception as e:
        print(f"[idle] could not spawn popup: {e}")


# ---------------------------------------------------------------------------
# Capture loop
# ---------------------------------------------------------------------------

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _today_window(now: datetime | None = None):
    """Return (start_time, end_time) for today's scheduled work window, or None
    if the auto-scheduler is off / today has no enabled window."""
    now = now or datetime.now()
    sched = CONFIG.get("work_schedule", {}) or {}
    if not sched.get("auto", False):
        return None
    day = (sched.get("days", {}) or {}).get(WEEKDAY_KEYS[now.weekday()])
    if not day or not day.get("on"):
        return None
    try:
        return dtime.fromisoformat(day["start"]), dtime.fromisoformat(day["end"])
    except Exception:
        return None


def _inside_window(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    w = _today_window(now)
    if not w:
        return False
    start, end = w
    return start <= now.time() <= end


def scheduler_loop():
    """Auto clock-in at a scheduled window's start, auto clock-out at its end.
    Manual clock toggles inside a window are respected (we only act on edges).
    Runs every 30s so transitions are near-instant relative to the capture cadence."""
    # Startup reconcile: if we boot up inside an active window and aren't clocked
    # in, clock in (handles 'computer was asleep when the window started').
    prev_inside = _inside_window()
    if prev_inside and not DB_INSTANCE.is_clocked_in():
        DB_INSTANCE.set_clock(True, "schedule")
        print("[schedule] startup inside work window -> clocked in")
    while True:
        time.sleep(30)
        try:
            inside = _inside_window()
            if inside and not prev_inside:
                if not DB_INSTANCE.is_clocked_in():
                    DB_INSTANCE.set_clock(True, "schedule")
                    print("[schedule] window start -> clocked in")
            elif prev_inside and not inside:
                # falling edge = scheduled end = the 'forgot to clock out' safety
                if DB_INSTANCE.is_clocked_in():
                    DB_INSTANCE.set_clock(False, "schedule")
                    print("[schedule] window end -> clocked out")
            prev_inside = inside
        except Exception as e:
            print(f"[schedule] error: {e}")


def capture_once() -> dict | None:
    # No key yet (fresh install before setup) — don't screenshot or call the API.
    if not has_api_key():
        print("[tracker] no API key set yet — add it on the setup screen (http://localhost:5555)")
        return None

    # If a timer is running, the user is explicitly tracking this block of time.
    # Auto-capturing would (a) waste API calls on a screen they're not at and
    # (b) double-count minutes against the timer entry that will be logged on stop.
    if DB_INSTANCE.get_active_timer():
        active = DB_INSTANCE.get_active_timer()
        print(f"[tracker] timer running ({active.get('description','')[:50]}) — skipping auto-capture")
        # Also reset idle state so we don't accumulate a streak from the pre-timer captures
        _idle_state.update({"last_hash": None, "streak": 0, "idle_entry_ids": [], "idle_start_ts": None, "idle_end_ts": None})
        return None

    try:
        jpeg, path, img = capture_screenshot()
    except Exception as e:
        print(f"[capture] screenshot failed: {e}")
        return None

    interval = int(CONFIG.get("screenshot_interval_minutes", 15))
    now = datetime.now()

    # ---- Idle detection ----
    new_hash = ahash(img) if IDLE_ENABLED else None
    is_similar = False
    if IDLE_ENABLED and _idle_state["last_hash"] is not None:
        dist = hamming(_idle_state["last_hash"], new_hash)
        is_similar = dist <= IDLE_THRESHOLD

    if IDLE_ENABLED and is_similar:
        _idle_state["streak"] += 1
        if _idle_state["streak"] >= IDLE_FLAG_AFTER:
            # Log as Away/Idle instead of classifying
            if _idle_state["idle_start_ts"] is None:
                _idle_state["idle_start_ts"] = now.isoformat()
            _idle_state["idle_end_ts"] = now.isoformat()
            eid = DB_INSTANCE.log_entry(
                category="Away", subcategory="Idle",
                description="Screen unchanged — auto-flagged as idle (backfill to recategorize)",
                confidence=1.0, duration_minutes=interval,
                source="idle", screenshot_path=path, timestamp=now,
            )
            _idle_state["idle_entry_ids"].append(eid)
            print(f"[{now.strftime('%H:%M:%S')}] #{eid} IDLE (streak {_idle_state['streak']})")
            # Don't update last_hash — keep comparing to the *first* idle screen
            return {"id": eid, "idle": True, "screenshot_path": path}
        # streak below threshold: don't reset, just fall through to classify
    elif IDLE_ENABLED:
        # Screen changed. If we'd already flagged idle, fire the backfill prompt.
        if _idle_state["streak"] >= IDLE_FLAG_AFTER and _idle_state["idle_entry_ids"]:
            prompt_id = DB_INSTANCE.create_prompt(
                start_ts=_idle_state["idle_start_ts"],
                end_ts=_idle_state["idle_end_ts"],
                entry_ids=list(_idle_state["idle_entry_ids"]),
            )
            print(f"[idle] resumed after {len(_idle_state['idle_entry_ids'])} idle captures — prompt #{prompt_id}")
            _spawn_idle_popup(prompt_id)
        # Reset on any non-similar capture
        _idle_state["streak"] = 0
        _idle_state["idle_entry_ids"] = []
        _idle_state["idle_start_ts"] = None
        _idle_state["idle_end_ts"] = None

    if IDLE_ENABLED:
        _idle_state["last_hash"] = new_hash

    # ---- Normal classify path ----
    tree = DB_INSTANCE.get_tree()
    try:
        result = classify_screenshot(jpeg, tree)
    except Exception as e:
        print(f"[classify] API call failed: {e}")
        traceback.print_exc()
        return None

    parent = (result.get("category") or "Admin/Other").strip()
    sub = (result.get("subcategory") or parent).strip()

    DB_INSTANCE.add_category(parent, parent=None)
    if sub and sub != parent and sub not in tree.get(parent, []):
        print(f"[classify] new SUB: {parent} -> {sub}")
        DB_INSTANCE.add_category(sub, parent=parent)

    eid = DB_INSTANCE.log_entry(
        category=parent, subcategory=sub,
        description=result.get("description", ""),
        confidence=float(result.get("confidence", 0.5)),
        duration_minutes=interval, source="auto",
        screenshot_path=path, timestamp=now,
    )
    print(f"[{now.strftime('%H:%M:%S')}] #{eid} {parent} > {sub} :: {result.get('description', '')[:80]}")
    return {"id": eid, **result, "screenshot_path": path}


def _next_midnight() -> float:
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.timestamp()


def capture_loop():
    interval_sec = int(CONFIG.get("screenshot_interval_minutes", 15)) * 60
    print(f"[tracker] capturing every {interval_sec // 60} min. idle detection: {'on' if IDLE_ENABLED else 'off'}")
    cleanup_old_screenshots()  # one pass at startup
    next_cleanup_at = _next_midnight()
    while True:
        if DB_INSTANCE.is_clocked_in():
            capture_once()
        else:
            print("[tracker] clocked out, skipping screenshot.")
        if time.time() >= next_cleanup_at:
            cleanup_old_screenshots()
            next_cleanup_at = _next_midnight()
        time.sleep(interval_sec)


def cleanup_old_screenshots():
    """Delete screenshot JPEGs older than retention_days and null out their DB paths.
    The entry rows themselves are kept — only the image file goes away."""
    keep_days = int(CONFIG.get("retention_days", CONFIG.get("keep_screenshots_days", 30)))
    cutoff = datetime.now() - timedelta(days=keep_days)
    sdir = Path(CONFIG["screenshot_dir"])
    if not sdir.exists():
        return
    removed_paths: list[str] = []
    for f in sdir.glob("*.jpg"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                p = str(f)
                f.unlink()
                removed_paths.append(p)
        except Exception:
            pass

    # Also null out any DB rows whose screenshot file no longer exists (covers files
    # removed externally too — keeps the dashboard from rendering broken thumbnails).
    with DB_INSTANCE.lock, DB_INSTANCE._connect() as conn:
        rows = conn.execute(
            "SELECT id, screenshot_path FROM entries WHERE screenshot_path IS NOT NULL"
        ).fetchall()
        nulled = 0
        for r in rows:
            if not Path(r["screenshot_path"]).exists():
                conn.execute("UPDATE entries SET screenshot_path = NULL WHERE id = ?", (r["id"],))
                nulled += 1
        conn.commit()

    if removed_paths or nulled:
        print(f"[cleanup] removed {len(removed_paths)} screenshot files, nulled {nulled} DB paths (retention={keep_days}d)")


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def summarize(entries: list[dict]) -> dict:
    parents: dict[str, dict] = defaultdict(lambda: {"minutes": 0, "subs": defaultdict(lambda: {"minutes": 0, "entries": []})})
    total = 0
    for e in entries:
        parent = e["category"] or "Admin/Other"
        sub = e.get("subcategory") or parent
        mins = e["duration_minutes"]
        parents[parent]["minutes"] += mins
        parents[parent]["subs"][sub]["minutes"] += mins
        parents[parent]["subs"][sub]["entries"].append({
            "id": e["id"],
            "time": e["timestamp"][11:16],
            "description": e["description"] or "",
            "source": e["source"],
            "screenshot": Path(e["screenshot_path"]).name if e.get("screenshot_path") else None,
        })
        total += mins

    out_parents = []
    for parent_name, pdata in sorted(parents.items(), key=lambda kv: -kv[1]["minutes"]):
        subs_sorted = sorted(pdata["subs"].items(), key=lambda kv: -kv[1]["minutes"])
        out_parents.append({
            "category": parent_name,
            "minutes": pdata["minutes"],
            "hours": round(pdata["minutes"] / 60, 2),
            "percent": round(100 * pdata["minutes"] / total, 1) if total else 0,
            "subs": [{
                "name": sub_name,
                "minutes": sdata["minutes"],
                "hours": round(sdata["minutes"] / 60, 2),
                "percent": round(100 * sdata["minutes"] / pdata["minutes"], 1) if pdata["minutes"] else 0,
                "entries": sdata["entries"],
            } for sub_name, sdata in subs_sorted],
        })

    return {"total_minutes": total, "total_hours": round(total / 60, 2), "parents": out_parents}


# ---------------------------------------------------------------------------
# Flask dashboard + API
# ---------------------------------------------------------------------------

def _compute_window(earliest: int | None, latest: int | None) -> tuple[int, int]:
    """Display window for the timeline: floors at 7 AM-8 PM, expands if entries fall outside."""
    FLOOR_START, FLOOR_END = 7 * 60, 20 * 60
    if earliest is None:
        ws, we = FLOOR_START, FLOOR_END
    else:
        ws = min(FLOOR_START, earliest - 30)
        we = max(FLOOR_END, (latest or earliest) + 30)
    ws = max(0, ws - (ws % 60))                          # snap to hour
    we = min(24 * 60, we + (60 - we % 60) % 60)
    return ws, we


def _hour_markers(window_start: int, window_end: int) -> list[dict]:
    out = []
    for m in range(window_start, window_end + 1, 60):
        h = (m // 60) % 24
        ampm = "AM" if h < 12 else "PM"
        disp = 12 if h % 12 == 0 else h % 12
        out.append({"min": m, "label": f"{disp} {ampm}"})
    return out


def create_app():
    from flask import Flask, render_template, request, jsonify, redirect, url_for, send_from_directory, abort, Response
    import csv

    app = Flask(__name__, template_folder=str(ROOT / "templates"))

    @app.before_request
    def require_profile():
        """First run: if no profile has been chosen, send HTML page loads to /setup.
        API endpoints and the setup page itself are exempt so logging/automation keep working."""
        p = request.path
        if p == "/setup" or p.startswith("/api/") or p.startswith("/screenshot/") or p.startswith("/static/"):
            return None
        if not DB_INSTANCE.get_settings().get("configured"):
            return redirect(url_for("setup"))
        return None

    @app.route("/setup", methods=["GET"])
    def setup():
        return render_template(
            "setup.html",
            profiles=PROFILES.get("profiles", {}),
            task_areas=PROFILES.get("task_areas", []),
            current=DB_INSTANCE.get_settings(),
            has_key=has_api_key(),
        )

    @app.route("/api/profile", methods=["POST"])
    def api_profile():
        data = request.get_json(silent=True) or request.form.to_dict()
        # Save the API key first (if provided) so capture can start right away.
        api_key = (data.get("api_key") or "").strip()
        if api_key:
            save_api_key(api_key)

        profile_id = (data.get("profile_id") or "").strip()
        profiles = PROFILES.get("profiles", {})
        if profile_id in profiles:
            prof = profiles[profile_id]
            DB_INSTANCE.set_profile(profile_id, prof.get("label", profile_id), prof["user_context"])
        else:
            # Custom: "Someone else" + chosen task areas (+ optional name)
            name = (data.get("display_name") or "Team member").strip()
            areas = data.get("task_areas") or []
            if isinstance(areas, str):
                areas = [a.strip() for a in areas.split(",") if a.strip()]
            areas_txt = ", ".join(areas) if areas else "a mix of marketing, brand/product, operations, and admin work"
            ctx = (f"I'm {name}, on the Otishi team (a footwear brand). "
                   f"My work focuses on: {areas_txt}. A lot of work happens through AI tools "
                   f"(Claude Code, Claude Cowork) — categorize by the underlying work, not the tool.")
            DB_INSTANCE.set_profile("custom", name, ctx)
        if request.is_json:
            return jsonify({"ok": True, "has_key": has_api_key(), **DB_INSTANCE.get_settings()})
        return redirect(url_for("daily"))

    @app.route("/")
    def index():
        return redirect(url_for("daily"))

    def _render(view, day):
        if view == "day":
            start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            label = start.strftime("%A, %B %d, %Y")
            prev_d, next_d = start - timedelta(days=1), start + timedelta(days=1)
        else:
            start = (day - timedelta(days=day.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            label = f"Week of {start.strftime('%b %d, %Y')}"
            prev_d, next_d = start - timedelta(days=7), start + timedelta(days=7)

        return render_template(
            "dashboard.html",
            view=view,
            date_str=label,
            iso_date=start.strftime("%Y-%m-%d"),
            prev_date=prev_d.strftime("%Y-%m-%d"),
            next_date=next_d.strftime("%Y-%m-%d"),
            summary=summarize(DB_INSTANCE.query_range(start, end)),
            tree=DB_INSTANCE.get_tree(),
            pending=DB_INSTANCE.list_pending_prompts(),
            active_timer=DB_INSTANCE.get_active_timer(),
            clock=DB_INSTANCE.get_clock_state(),
        )

    @app.route("/today")
    @app.route("/day")
    def daily():
        date_str = request.args.get("date")
        day = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        return _render("day", day)

    @app.route("/week")
    def week():
        date_str = request.args.get("date")
        day = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        return _render("week", day)

    @app.route("/log")
    def quick_log():
        """Mobile-friendly tap UI. Submits to /manual."""
        return render_template("quick_log.html",
                               tree=DB_INSTANCE.get_tree(),
                               active_timer=DB_INSTANCE.get_active_timer(),
                               clock=DB_INSTANCE.get_clock_state())

    @app.route("/manual", methods=["POST"])
    def manual_entry():
        parent = request.form.get("category", "").strip()
        sub = request.form.get("subcategory", "").strip() or parent
        description = request.form.get("description", "").strip()
        duration = int(request.form.get("duration", 15))
        ts_str = request.form.get("timestamp", "").strip()
        ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
        if not parent:
            return "Parent required", 400
        DB_INSTANCE.add_category(parent, parent=None, user_added=True)
        if sub != parent:
            DB_INSTANCE.add_category(sub, parent=parent, user_added=True)
        DB_INSTANCE.log_entry(parent, sub, description, 1.0, duration, "manual", None, ts)
        return redirect(request.referrer or url_for("daily"))

    @app.route("/entry/<int:eid>/delete", methods=["POST"])
    def delete_entry(eid):
        DB_INSTANCE.delete_entry(eid)
        return redirect(request.referrer or url_for("daily"))

    @app.route("/entry/<int:eid>/edit", methods=["POST"])
    def edit_entry(eid):
        parent = request.form.get("category", "").strip()
        sub = request.form.get("subcategory", "").strip() or parent
        description = request.form.get("description", "").strip()
        duration = int(request.form.get("duration", 15))
        DB_INSTANCE.add_category(parent, parent=None, user_added=True)
        if sub != parent:
            DB_INSTANCE.add_category(sub, parent=parent, user_added=True)
        DB_INSTANCE.update_entry(eid, parent, sub, description, duration)
        return redirect(request.referrer or url_for("daily"))

    @app.route("/entries/move", methods=["POST"])
    def entries_move():
        """Drag/resize a timeline block. Handles single AND merged blocks.

        JSON:
          mode 'move'  : {entry_ids:[...], date:'YYYY-MM-DD', start_min:int}
                         -> shift the whole group so its earliest entry starts at
                            date+start_min (supports cross-day moves).
          mode 'resize': {entry_ids:[...], duration_minutes:int}
                         -> set the block's total span by adjusting the LAST entry.
        """
        data = request.get_json(silent=True) or {}
        try:
            ids = [int(i) for i in (data.get("entry_ids") or []) if str(i).strip()]
        except (TypeError, ValueError):
            return jsonify({"error": "bad entry_ids"}), 400
        if not ids:
            return jsonify({"error": "entry_ids required"}), 400

        rows = DB_INSTANCE.get_entries_by_ids(ids)
        if not rows:
            return jsonify({"error": "no matching entries"}), 404

        mode = data.get("mode", "move")

        if mode == "resize":
            try:
                new_total = int(round(float(data.get("duration_minutes"))))
            except (TypeError, ValueError):
                return jsonify({"error": "duration_minutes required"}), 400
            new_total = max(1, min(new_total, 24 * 60))
            first_start = datetime.fromisoformat(rows[0]["timestamp"])
            last = rows[-1]
            last_start = datetime.fromisoformat(last["timestamp"])
            last_end = last_start + timedelta(minutes=last["duration_minutes"])
            old_total = (last_end - first_start).total_seconds() / 60
            diff = new_total - old_total
            new_last_dur = max(1, int(round(last["duration_minutes"] + diff)))
            DB_INSTANCE.set_entry_duration(last["id"], new_last_dur)
            return jsonify({"ok": True, "mode": "resize", "last_id": last["id"], "duration_minutes": new_last_dur})

        # move
        date_str = (data.get("date") or "").strip()
        try:
            start_min = int(round(float(data.get("start_min"))))
        except (TypeError, ValueError):
            return jsonify({"error": "start_min required"}), 400
        if not date_str:
            return jsonify({"error": "date required"}), 400
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "bad date"}), 400
        start_min = max(0, min(start_min, 24 * 60 - 1))
        target = day + timedelta(minutes=start_min)
        earliest = datetime.fromisoformat(rows[0]["timestamp"])
        delta = target - earliest
        DB_INSTANCE.shift_entries(ids, delta)
        return jsonify({"ok": True, "mode": "move", "shifted": len(ids),
                        "new_start": target.isoformat()})

    @app.route("/screenshot/<name>")
    def screenshot(name):
        if "/" in name or "\\" in name or ".." in name:
            abort(404)
        return send_from_directory(Path(CONFIG["screenshot_dir"]), name)

    def _build_day_blocks(day_start: datetime, granularity: str):
        """Returns (blocks, total_lanes) for a single day.
        Blocks are merged per granularity ('parent', 'sub', or 'raw') and lane-assigned."""
        day_end = day_start + timedelta(days=1)
        entries = DB_INSTANCE.query_range(day_start, day_end)

        parsed = []
        for e in entries:
            ts = datetime.fromisoformat(e["timestamp"])
            dur = e["duration_minutes"]
            start_min = (ts - day_start).total_seconds() / 60
            parsed.append({
                "id": e["id"],
                "category": e["category"],
                "subcategory": e.get("subcategory") or e["category"],
                "description": e.get("description") or "",
                "source": e["source"],
                "start_min": start_min,
                "end_min": start_min + dur,
                "duration_minutes": dur,
                "ts": ts,
            })
        parsed.sort(key=lambda x: x["start_min"])

        # Merge consecutive same-category blocks
        def key_of(b):
            return (b["category"],) if granularity == "parent" else (b["category"], b["subcategory"])

        merged: list[dict] = []
        if granularity == "raw":
            for b in parsed:
                merged.append({
                    **b,
                    "count": 1,
                    "entry_ids": [b["id"]],
                    "descriptions": [b["description"]] if b["description"] else [],
                })
        else:
            current = None
            GAP_TOLERANCE = 1.0  # minutes — bridges sub-second timestamp drift
            for b in parsed:
                if (current and key_of(current) == key_of(b)
                        and b["start_min"] - current["end_min"] < GAP_TOLERANCE):
                    current["end_min"] = b["end_min"]
                    current["duration_minutes"] = current["end_min"] - current["start_min"]
                    current["count"] += 1
                    current["entry_ids"].append(b["id"])
                    if b["description"] and b["description"] not in current["descriptions"]:
                        current["descriptions"].append(b["description"])
                    # Bubble up sources: timer/manual override auto/idle
                    if current["source"] in ("auto", "idle") and b["source"] not in ("auto", "idle"):
                        current["source"] = b["source"]
                else:
                    if current:
                        merged.append(current)
                    current = {
                        **b,
                        "count": 1,
                        "entry_ids": [b["id"]],
                        "descriptions": [b["description"]] if b["description"] else [],
                    }
            if current:
                merged.append(current)

        # Label times + the "primary" id (used when there's only one underlying entry)
        for m in merged:
            ts = m["ts"]
            end_ts = ts + timedelta(minutes=m["duration_minutes"])
            fmt = "%#I:%M %p" if os.name == "nt" else "%-I:%M %p"
            m["start_label"] = ts.strftime(fmt)
            m["end_label"] = end_ts.strftime(fmt)
            m["primary_id"] = m["entry_ids"][0] if len(m["entry_ids"]) == 1 else None
            m.pop("ts", None)

        # Lane assignment (within this day)
        lane_ends: list[float] = []
        for m in merged:
            placed = False
            for i, le in enumerate(lane_ends):
                if le <= m["start_min"]:
                    lane_ends[i] = m["end_min"]
                    m["lane"] = i
                    placed = True
                    break
            if not placed:
                m["lane"] = len(lane_ends)
                lane_ends.append(m["end_min"])
        total_lanes = max(1, len(lane_ends))

        return merged, total_lanes

    @app.route("/timeline")
    def timeline():
        view = request.args.get("view", "day")
        granularity = request.args.get("granularity", "sub")
        if granularity not in ("parent", "sub", "raw"):
            granularity = "sub"

        date_str = request.args.get("date")
        anchor = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()

        if view == "week":
            week_start = (anchor - timedelta(days=anchor.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            days = []
            global_lane_count = 1
            min_start = 24 * 60
            max_end = 0
            for i in range(7):
                ds = week_start + timedelta(days=i)
                blocks, lanes = _build_day_blocks(ds, granularity)
                days.append({
                    "iso": ds.strftime("%Y-%m-%d"),
                    "label_dow": ds.strftime("%a"),
                    "label_date": ds.strftime("%b %d"),
                    "is_today": ds.date() == datetime.now().date(),
                    "blocks": blocks,
                    "total_lanes": lanes,
                })
                global_lane_count = max(global_lane_count, lanes)
                if blocks:
                    min_start = min(min_start, int(blocks[0]["start_min"]))
                    max_end = max(max_end, int(blocks[-1]["end_min"]))

            window = _compute_window(min_start if min_start < 24 * 60 else None,
                                     max_end if max_end > 0 else None)
            return render_template(
                "timeline.html",
                view="week",
                granularity=granularity,
                date_str=f"Week of {week_start.strftime('%b %d, %Y')}",
                iso_date=week_start.strftime("%Y-%m-%d"),
                prev_date=(week_start - timedelta(days=7)).strftime("%Y-%m-%d"),
                next_date=(week_start + timedelta(days=7)).strftime("%Y-%m-%d"),
                days=days,
                window_start=window[0],
                window_end=window[1],
                hours=_hour_markers(*window),
                now_min=None,  # week view doesn't get a now-line (per-day badges instead)
                tree=DB_INSTANCE.get_tree(),
            )

        # Day view
        day_start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        blocks, total_lanes = _build_day_blocks(day_start, granularity)
        window = _compute_window(
            int(blocks[0]["start_min"]) if blocks else None,
            int(blocks[-1]["end_min"]) if blocks else None,
        )
        now = datetime.now()
        now_min = (now - day_start).total_seconds() / 60 if day_start.date() == now.date() else None
        return render_template(
            "timeline.html",
            view="day",
            granularity=granularity,
            date_str=day_start.strftime("%A, %B %d, %Y"),
            iso_date=day_start.strftime("%Y-%m-%d"),
            prev_date=(day_start - timedelta(days=1)).strftime("%Y-%m-%d"),
            next_date=(day_start + timedelta(days=1)).strftime("%Y-%m-%d"),
            entries=blocks,
            total_lanes=total_lanes,
            window_start=window[0],
            window_end=window[1],
            hours=_hour_markers(*window),
            now_min=now_min,
            tree=DB_INSTANCE.get_tree(),
        )

    @app.route("/export.csv")
    def export_csv():
        view = request.args.get("view", "day")
        date_str = request.args.get("date")
        anchor = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        if view == "week":
            start = (anchor - timedelta(days=anchor.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=7)
            fname = f"timetracker_week_{start.strftime('%Y-%m-%d')}.csv"
        else:
            start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            fname = f"timetracker_day_{start.strftime('%Y-%m-%d')}.csv"

        entries = DB_INSTANCE.query_range(start, end)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["date", "time", "category", "subcategory", "description", "duration_minutes", "source"])
        for e in entries:
            ts = e["timestamp"]
            w.writerow([ts[:10], ts[11:16], e["category"], e.get("subcategory") or "",
                        e.get("description") or "", e["duration_minutes"], e["source"]])
        return Response(
            buf.getvalue(), mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    @app.route("/weekly-report")
    def weekly_report():
        date_str = request.args.get("date")
        anchor = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
        start = (anchor - timedelta(days=anchor.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        entries = DB_INSTANCE.query_range(start, end)
        summary = summarize(entries)
        per_day = []
        for i in range(7):
            ds = start + timedelta(days=i)
            de = ds + timedelta(days=1)
            day_entries = [e for e in entries if ds.isoformat() <= e["timestamp"] < de.isoformat()]
            per_day.append({"name": ds.strftime("%a %b %d"), "hours": round(sum(e["duration_minutes"] for e in day_entries) / 60, 2)})
        return render_template(
            "weekly_report.html",
            week_label=f"{start.strftime('%b %d')} – {(start + timedelta(days=6)).strftime('%b %d, %Y')}",
            iso_date=start.strftime("%Y-%m-%d"),
            summary=summary, per_day=per_day,
            generated_at=datetime.now().strftime("%b %d, %Y at %I:%M %p"),
        )

    @app.route("/capture-now", methods=["POST"])
    def capture_now():
        threading.Thread(target=capture_once, daemon=True).start()
        return redirect(request.referrer or url_for("daily"))

    # ---- JSON API (localhost only — Flask is bound to 127.0.0.1) ----

    @app.route("/api/tree")
    def api_tree():
        return jsonify(DB_INSTANCE.get_tree())

    LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}

    @app.route("/api/log", methods=["POST"])
    def api_log():
        """External entry point (Cowork skills, scripts). Localhost only — Flask
        listens on 0.0.0.0 so the dashboard is reachable via Tailscale, but this
        write endpoint stays restricted to the local machine."""
        if request.remote_addr not in LOCALHOST_IPS:
            return jsonify({"error": "this endpoint requires a localhost request"}), 403
        data = request.get_json(silent=True) or {}
        parent = (data.get("category") or "").strip()
        if not parent:
            return jsonify({"error": "category is required"}), 400
        sub = (data.get("subcategory") or parent).strip()
        description = (data.get("description") or "").strip()
        duration = int(data.get("duration_minutes") or 15)
        ts_str = data.get("timestamp")
        ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now()
        source = (data.get("source") or "api").strip()

        DB_INSTANCE.add_category(parent, parent=None, user_added=True)
        if sub != parent:
            DB_INSTANCE.add_category(sub, parent=parent, user_added=True)
        eid = DB_INSTANCE.log_entry(parent, sub, description, 1.0, duration, source, None, ts)
        return jsonify({"id": eid, "category": parent, "subcategory": sub,
                        "description": description, "duration_minutes": duration,
                        "timestamp": ts.isoformat(), "source": source}), 201

    @app.route("/api/shutdown", methods=["POST"])
    def api_shutdown():
        """Stop the tracker process. Used by the in-dashboard Stop button.
        Not localhost-gated on purpose — Bentley should be able to stop from his
        phone via Tailscale. Tailnet is private, single-user, low abuse risk."""
        import threading as _t
        import os as _os
        def _bye():
            time.sleep(0.4)  # let the JSON response flush to the browser
            _os._exit(0)
        _t.Thread(target=_bye, daemon=True).start()
        return jsonify({"ok": True, "message": "Tracker shutting down"})

    # ---- Clock in / out ----
    @app.route("/api/clock")
    def api_clock_get():
        st = DB_INSTANCE.get_clock_state()
        st["schedule"] = CONFIG.get("work_schedule", {})
        st["in_window"] = _inside_window()
        return jsonify(st)

    @app.route("/api/clock/in", methods=["POST"])
    def api_clock_in():
        return jsonify(DB_INSTANCE.set_clock(True, "manual"))

    @app.route("/api/clock/out", methods=["POST"])
    def api_clock_out():
        return jsonify(DB_INSTANCE.set_clock(False, "manual"))

    @app.route("/api/clock/toggle", methods=["POST"])
    def api_clock_toggle():
        st = DB_INSTANCE.get_clock_state()
        return jsonify(DB_INSTANCE.set_clock(not st["clocked_in"], "manual"))

    @app.route("/api/schedule", methods=["GET", "POST"])
    def api_schedule():
        if request.method == "GET":
            return jsonify(CONFIG.get("work_schedule", {}))
        data = request.get_json(silent=True) or {}
        days_in = data.get("days", {}) or {}
        sched = {"auto": bool(data.get("auto")), "days": {}}
        for k in WEEKDAY_KEYS:
            d = days_in.get(k, {}) or {}
            start = str(d.get("start") or "09:00")
            end = str(d.get("end") or "18:00")
            try:  # validate HH:MM
                dtime.fromisoformat(start); dtime.fromisoformat(end)
            except Exception:
                start, end = "09:00", "18:00"
            sched["days"][k] = {"on": bool(d.get("on")), "start": start, "end": end}
        # Persist to config.json without disturbing the rest (incl. the API key)
        try:
            raw = json.load(open(CONFIG_PATH, encoding="utf-8"))
            raw["work_schedule"] = sched
            json.dump(raw, open(CONFIG_PATH, "w", encoding="utf-8"), indent=2)
        except Exception as e:
            return jsonify({"error": f"could not save: {e}"}), 500
        CONFIG["work_schedule"] = sched  # live update so the scheduler picks it up
        return jsonify({"ok": True, "work_schedule": sched})

    @app.route("/api/timer")
    def api_timer_get():
        active = DB_INSTANCE.get_active_timer()
        if not active:
            return jsonify({"active": False})
        started = datetime.fromisoformat(active["started_at"])
        elapsed = int((datetime.now() - started).total_seconds())
        return jsonify({"active": True, "elapsed_seconds": elapsed, **active})

    @app.route("/api/timer/start", methods=["POST"])
    def api_timer_start():
        data = request.get_json(silent=True) or request.form.to_dict()
        parent = (data.get("category") or "").strip()
        if not parent:
            return jsonify({"error": "category is required"}), 400
        sub = (data.get("subcategory") or parent).strip()
        description = (data.get("description") or "").strip()
        DB_INSTANCE.add_category(parent, parent=None, user_added=True)
        if sub != parent:
            DB_INSTANCE.add_category(sub, parent=parent, user_added=True)
        result = DB_INSTANCE.start_timer(parent, sub, description)
        return jsonify({"ok": True, **result})

    @app.route("/api/timer/stop", methods=["POST"])
    def api_timer_stop():
        result = DB_INSTANCE.stop_timer()
        if not result:
            return jsonify({"ok": False, "error": "no active timer"}), 404
        return jsonify({"ok": True, "logged": result})

    @app.route("/api/prompts/pending")
    def api_prompts_pending():
        return jsonify(DB_INSTANCE.list_pending_prompts())

    @app.route("/api/prompts/<int:pid>")
    def api_prompt_get(pid):
        p = DB_INSTANCE.get_prompt(pid)
        if not p:
            return jsonify({"error": "not found"}), 404
        return jsonify(p)

    @app.route("/api/prompts/<int:pid>/resolve", methods=["POST"])
    def api_prompt_resolve(pid):
        data = request.get_json(silent=True) or request.form.to_dict()
        keep = str(data.get("keep_as_away", "")).lower() in ("1", "true", "yes", "on")
        parent = (data.get("category") or "Away").strip()
        sub = (data.get("subcategory") or "Idle").strip()
        description = (data.get("description") or "").strip()
        if not keep:
            DB_INSTANCE.add_category(parent, parent=None, user_added=True)
            if sub != parent:
                DB_INSTANCE.add_category(sub, parent=parent, user_added=True)
        ok = DB_INSTANCE.resolve_prompt(pid, parent, sub, description, keep_as_away=keep)
        if not ok:
            return jsonify({"error": "not found"}), 404
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(request.referrer or url_for("daily"))

    return app


# ---------------------------------------------------------------------------
# Idle backfill popup (Tkinter, runs as a separate process via --idle-prompt)
# ---------------------------------------------------------------------------

def _post_resolve(base: str, prompt_id: int, payload: dict):
    """POST a backfill resolution to the running tracker."""
    import urllib.request
    req = urllib.request.Request(
        f"{base}/api/prompts/{prompt_id}/resolve",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5).read()


def _run_idle_popup_macos(prompt_id: int, prompt: dict, tree: dict, base: str):
    """Native macOS backfill dialog via osascript (no Tkinter dependency)."""
    parents = sorted(tree.keys()) or ["Admin/Other"]
    start = datetime.fromisoformat(prompt["start_ts"]).strftime("%I:%M %p")
    end = datetime.fromisoformat(prompt["end_ts"]).strftime("%I:%M %p")
    span_min = len(prompt["entry_ids"]) * int(CONFIG.get("screenshot_interval_minutes", 15))

    def osa(script: str) -> str:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip()

    def esc(s: str) -> str:
        # Escape backslashes and double quotes for AppleScript string literals
        return s.replace("\\", "\\\\").replace('"', '\\"')

    AWAY = "I was actually away (keep as Away)"

    # Step 1 — pick the parent category (or choose to keep it as Away)
    items = parents + [AWAY]
    item_list = ", ".join('"%s"' % esc(p) for p in items)
    prompt_text = esc(f"You were away {start} -> {end}  (~{span_min} min). What were you working on?")
    choice = osa(
        f'choose from list {{{item_list}}} '
        f'with title "Welcome back" with prompt "{prompt_text}" '
        f'default items {{"{esc(parents[0])}"}}'
    )
    if not choice or choice == "false":
        return  # cancelled — leave the captures flagged as Away
    if choice == AWAY:
        _post_resolve(base, prompt_id, {"keep_as_away": True})
        return
    parent = choice

    # Step 2 — pick a subcategory
    subs = tree.get(parent, [])
    sub = parent
    if subs:
        sub_list = ", ".join('"%s"' % esc(s) for s in subs)
        picked = osa(
            f'choose from list {{{sub_list}}} '
            f'with title "{esc(parent)}" with prompt "Pick a subcategory" '
            f'default items {{"{esc(subs[0])}"}}'
        )
        if picked and picked != "false":
            sub = picked

    # Step 3 — description
    out = subprocess.run(
        ["osascript", "-e",
         f'display dialog "What were you doing?" default answer "" '
         f'with title "{esc(parent)} > {esc(sub)}" '
         f'buttons {{"Skip", "Save"}} default button "Save"'],
        capture_output=True, text=True,
    ).stdout.strip()
    desc = ""
    if "text returned:" in out:
        desc = out.split("text returned:", 1)[1].strip()

    _post_resolve(base, prompt_id, {"category": parent, "subcategory": sub, "description": desc})


def run_idle_popup(prompt_id: int):
    import urllib.request

    port = int(CONFIG.get("dashboard_port", 5555))
    base = f"http://127.0.0.1:{port}"

    # Fetch prompt details and tree from the running tracker
    try:
        with urllib.request.urlopen(f"{base}/api/prompts/{prompt_id}", timeout=5) as r:
            prompt = json.loads(r.read())
        with urllib.request.urlopen(f"{base}/api/tree", timeout=5) as r:
            tree = json.loads(r.read())
    except Exception as e:
        # Fall back to direct DB read if Flask isn't up
        prompt = DB_INSTANCE.get_prompt(prompt_id)
        tree = DB_INSTANCE.get_tree()
        if not prompt:
            print(f"[popup] prompt {prompt_id} not found: {e}")
            return

    # macOS: use a native osascript dialog (no Tkinter needed)
    if sys.platform == "darwin":
        try:
            _run_idle_popup_macos(prompt_id, prompt, tree, base)
        except Exception as e:
            print(f"[popup] macOS dialog failed: {e}")
        return

    # Windows / Linux: Tkinter modal
    import tkinter as tk
    from tkinter import ttk, messagebox

    start = datetime.fromisoformat(prompt["start_ts"]).strftime("%I:%M %p")
    end = datetime.fromisoformat(prompt["end_ts"]).strftime("%I:%M %p")
    span_min = len(prompt["entry_ids"]) * int(CONFIG.get("screenshot_interval_minutes", 15))

    root = tk.Tk()
    root.title("Welcome back — what were you working on?")
    root.geometry("460x300")
    root.attributes("-topmost", True)
    root.lift()

    tk.Label(root, text=f"You were away {start} → {end}  (~{span_min} min)",
             font=("Segoe UI", 11, "bold")).pack(pady=(14, 4), padx=14, anchor="w")
    tk.Label(root, text="Backfill those captures with the right category, or keep as Away.",
             fg="#666", font=("Segoe UI", 9)).pack(padx=14, anchor="w")

    frm = tk.Frame(root); frm.pack(padx=14, pady=10, fill="x")

    tk.Label(frm, text="Category").grid(row=0, column=0, sticky="w")
    parent_var = tk.StringVar(value=sorted(tree.keys())[0] if tree else "Admin/Other")
    parent_cb = ttk.Combobox(frm, textvariable=parent_var, values=sorted(tree.keys()), width=30, state="readonly")
    parent_cb.grid(row=0, column=1, sticky="ew", pady=2)

    tk.Label(frm, text="Subcategory").grid(row=1, column=0, sticky="w")
    sub_var = tk.StringVar()
    sub_cb = ttk.Combobox(frm, textvariable=sub_var, width=30)
    sub_cb.grid(row=1, column=1, sticky="ew", pady=2)

    def refresh_subs(*_):
        subs = tree.get(parent_var.get(), [])
        sub_cb["values"] = subs
        if subs and not sub_var.get():
            sub_var.set(subs[0])
    parent_cb.bind("<<ComboboxSelected>>", refresh_subs)
    refresh_subs()

    tk.Label(frm, text="What were you doing?").grid(row=2, column=0, sticky="nw", pady=(8, 0))
    desc_text = tk.Text(frm, width=32, height=4, font=("Segoe UI", 10))
    desc_text.grid(row=2, column=1, sticky="ew", pady=(8, 0))

    frm.columnconfigure(1, weight=1)

    btns = tk.Frame(root); btns.pack(padx=14, pady=12, fill="x")

    def submit(keep_as_away=False):
        payload = {
            "category": parent_var.get(),
            "subcategory": sub_var.get() or parent_var.get(),
            "description": desc_text.get("1.0", "end").strip(),
            "keep_as_away": keep_as_away,
        }
        try:
            req = urllib.request.Request(
                f"{base}/api/prompts/{prompt_id}/resolve",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5).read()
            root.destroy()
        except Exception as e:
            messagebox.showerror("Backfill failed", str(e))

    tk.Button(btns, text="I was actually away", command=lambda: submit(keep_as_away=True)).pack(side="left")
    tk.Button(btns, text="Save backfill", command=lambda: submit(False),
              bg="#4f7cff", fg="white", font=("Segoe UI", 10, "bold")).pack(side="right")

    root.mainloop()


# ---------------------------------------------------------------------------
# Self-update (pull latest code from the team repo on launch)
# ---------------------------------------------------------------------------

def self_update():
    """On launch, pull the latest code from the private team repo and, if anything
    changed, restart into the new version. Best-effort: offline, no git, or not a
    clone -> silently skipped. Disable with auto_update:false in config or env
    TT_NO_UPDATE=1. Local config.json / DB / screenshots are gitignored, so a pull
    never touches anyone's data or key."""
    import shutil
    if os.environ.get("TT_NO_UPDATE") or os.environ.get("TT_UPDATED"):
        return
    if not CONFIG.get("auto_update", True):
        return
    if not (ROOT / ".git").exists():
        return
    git = shutil.which("git")
    if not git:
        return
    try:
        out = subprocess.run(
            [git, "-C", str(ROOT), "pull", "--rebase", "--autostash"],
            capture_output=True, text=True, timeout=30,
        )
        tail = (out.stdout + out.stderr).strip().splitlines()
        print("[update]", tail[-1] if tail else "(no output)")
        if out.returncode == 0 and "Already up to date" not in out.stdout:
            print("[update] new version pulled — restarting into it")
            os.environ["TT_UPDATED"] = "1"  # guard against a restart loop
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"[update] skipped: {e}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Otishi Work Time Tracker")
    parser.add_argument("--dashboard", action="store_true", help="dashboard only, no capture loop")
    parser.add_argument("--once", action="store_true", help="capture one screenshot and exit")
    parser.add_argument("--idle-prompt", type=int, default=None, metavar="ID",
                        help="(internal) open Tk modal to backfill an idle range")
    parser.add_argument("--no-update", action="store_true", help="skip the launch-time git pull")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    if args.idle_prompt is not None:
        run_idle_popup(args.idle_prompt)
        return

    if args.once:
        print(json.dumps(capture_once(), indent=2, default=str))
        return

    # Auto-update before we start the long-running services (not for --once/--idle-prompt).
    if not args.no_update:
        self_update()

    if not args.dashboard:
        threading.Thread(target=capture_loop, daemon=True).start()
        threading.Thread(target=scheduler_loop, daemon=True).start()

    app = create_app()
    port = args.port or int(CONFIG.get("dashboard_port", 5555))
    print(f"[dashboard] http://localhost:{port}  (also reachable on your LAN / Tailscale)")
    # Bind to all interfaces so Tailscale / LAN can reach the dashboard.
    # /api/log is gated to 127.0.0.1 inside the route.
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
