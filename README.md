# Otishi Work Time Tracker

A local-only desktop time tracker that screenshots your screen every 15 minutes, asks Claude to categorize what you're working on, and gives you a Flask dashboard with daily/weekly reports.

Built for a single-user workflow (a co-founder demonstrating workload distribution). Everything — screenshots, database, API calls — stays on your machine except for the vision classification request to Anthropic.

## What it does

- Every N minutes (default 15): grabs a screenshot of all monitors → downsizes it → sends to Claude → logs the category + a one-sentence description to SQLite.
- Categories you seed in `config.json` are pinned. If Claude sees work that doesn't fit any of them, it proposes a new category and the tracker auto-adds it.
- A Flask dashboard at `http://localhost:5555` shows daily and weekly views, with each category expanding to the actual screenshots + descriptions that fed into it.
- Manual entries for meetings, calls, and off-screen work.
- A printable `/weekly-report` page you can send to your co-founder.

## Setup

1. **Install Python 3.10+** if you don't have it.

2. **Install dependencies** (a venv is recommended):
   ```powershell
   cd C:\Users\bentl\Projects\time-tracker
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Add your Anthropic API key** to `config.json`:
   ```json
   "anthropic_api_key": "sk-ant-..."
   ```
   Or leave the placeholder and set `ANTHROPIC_API_KEY` as a Windows environment variable.

4. **Test it once** before running for real:
   ```powershell
   python main.py --once
   ```
   This takes one screenshot, classifies it, prints the JSON result, and exits. Confirms your key works and the API call returns a sensible category.

## Running

**Foreground (you see logs in a terminal):**
```powershell
python main.py
```
Opens `http://localhost:5555` and starts the 15-min capture loop.

**Background (no window):**
```powershell
.\start_tracker.bat
```
Launches via `pythonw.exe` so there's no console. Opens the dashboard automatically.

**Stop it:**
```powershell
.\stop_tracker.bat
```

**Auto-start at Windows login:**
```powershell
.\install_autostart.bat
```
Puts a shortcut in your Startup folder. To remove, delete the shortcut from
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`.

## Configuration (`config.json`)

| Field | Purpose |
|---|---|
| `anthropic_api_key` | Your Claude API key. |
| `model` | Vision model. Default `claude-sonnet-4-6` (fast + cheap + plenty accurate). Use `claude-opus-4-7` if you want better accuracy. |
| `screenshot_interval_minutes` | How often to capture. 15 is the default; 10 and 30 also reasonable. |
| `screenshot_dir` | Where JPEGs are saved (relative to repo root). |
| `database_path` | SQLite file path. |
| `dashboard_port` | Flask port. |
| `retention_days` | Auto-deletes screenshot JPEGs older than this. DB entries stay, but their `screenshot_path` is nulled. Runs at startup and at midnight. |
| `work_hours_start` / `work_hours_end` | `"HH:MM"` 24-hour. Outside this window, the auto-capture loop skips screenshots. The timer endpoints still work 24/7. Omit both keys to capture all day. |
| `categories` | Seed list. Claude will propose new ones as needed. |
| `user_context` | Sent to Claude with each classification so it knows what your work looks like. **Edit this** — accurate context dramatically improves categorization. |

## Idle detection

Every capture is hashed (8x8 average hash). If the screen looks the same as the last one (Hamming distance ≤ `similarity_threshold`, default 6), the tracker increments an idle streak. After `captures_to_flag_idle` similar captures in a row (default 2 → 30 min of same screen), the next captures are logged as `Away / Idle` instead of being sent to Claude.

When the screen finally changes, a backfill prompt is created. Two ways to fill it in:

1. **Tkinter popup** — a small window opens (spawned as a separate process so the tracker keeps running) asking what you were actually doing. Pick category/sub + add a description → it backfills every idle entry in the range. Or click "I was actually away" to leave them as Away.
2. **Dashboard banner** — pending prompts also show up at the top of `/today` and `/week`. Same form, same effect. Use this if you dismissed the popup.

Tune in `config.json`:
```json
"idle_detection": {
  "enabled": true,
  "similarity_threshold": 6,        // higher = more tolerant of small changes
  "captures_to_flag_idle": 2,       // 2 captures × 15 min = flag after 30 min of same screen
  "show_popup": true                // set false to use dashboard banner only
}
```

## Dashboard URLs

- `/today` — today's breakdown (with idle backfill banner if needed)
- `/today?date=2026-05-25` — any specific day
- `/week` — current week
- `/weekly-report` — printable summary for sharing
- POST `/capture-now` — trigger an off-schedule capture (button in the UI)
- POST `/manual` — add a manual entry (form in the UI)

## JSON API

Flask binds to `0.0.0.0` so the dashboard and `/log` page are reachable over your Tailnet / LAN. Read-only API endpoints are also reachable over the network. The **`/api/log` write endpoint is gated to `127.0.0.1`** — Cowork skills running on the same machine can write to it, but nothing remote can (even over Tailscale).

**`POST /api/log`** — log an entry directly (localhost only).
```bash
curl -X POST http://localhost:5555/api/log \
  -H "Content-Type: application/json" \
  -d '{"category":"Paid Media","subcategory":"Meta Ads","description":"Reviewed Sneaker A campaign","duration_minutes":25}'
```
- Required: `category`
- Optional: `subcategory` (defaults to category), `description`, `duration_minutes` (default 15), `timestamp` (ISO format, default now), `source` (default "api")
- Returns 201 with the created entry. New subcategories under existing parents are auto-added.

**`GET /api/tree`** — current category tree as `{parent: [subs]}`.

**`GET /api/prompts/pending`** — list unresolved idle backfill prompts.

**`POST /api/prompts/<id>/resolve`** — resolve a prompt. Body: `{"category": "...", "subcategory": "...", "description": "...", "keep_as_away": false}`. If `keep_as_away` is true, the idle entries stay as Away and just the prompt is closed.

## How the data is stored

```
timetracker.db          SQLite, two tables: entries + categories
screenshots/            JPEGs, named YYYYMMDD_HHMMSS.jpg
```

Each `entries` row has: timestamp, category, description, confidence, duration_minutes, source (`auto`/`manual`), screenshot_path. You can edit/delete entries inline in the dashboard — useful for fixing the occasional misclassification.

## Mobile / phone access via Tailscale

The Flask dashboard binds to `0.0.0.0` so it's reachable from any device on the same network. The recommended setup is [Tailscale](https://tailscale.com/), which gives you a private encrypted mesh between your laptop and phone — no port forwarding, nothing exposed to the public internet.

**Setup:**

1. **Install Tailscale** on both devices:
   - Laptop: download from [tailscale.com/download/windows](https://tailscale.com/download/windows)
   - Phone: install the Tailscale app from the App Store or Play Store
2. **Sign in with Google on both** using the same Google account. Both devices will appear in your Tailscale admin console.
3. **Find your laptop's Tailscale IP** (run on the laptop):
   ```powershell
   tailscale ip -4
   ```
   You'll get something like `100.x.y.z`.
4. **On your phone**, open `http://100.x.y.z:5555/log` for the quick-log page (or `/today` for the full dashboard).

**Tip:** save the URL as a home-screen bookmark so quick-log is one tap away.

**Security:** Tailscale traffic is encrypted device-to-device (WireGuard). Nothing is exposed to the public internet — only devices on your Tailnet can reach the dashboard. The `/api/log` JSON endpoint is additionally restricted to `127.0.0.1` (localhost only) so Cowork skills can write to it, but nothing remote can — even over Tailscale.

**Alternative — same WiFi without Tailscale:** find your laptop's LAN IP (`ipconfig` → look for IPv4) and use `http://<that-ip>:5555/log` from your phone. Works only when both devices are on the same WiFi.

## Quick-log page (`/log`)

Built mobile-first for thumb tapping:

1. **Tap a parent category** (big card buttons, 2-column grid)
2. **Tap a subcategory** (or "+ new subcategory" to type a custom name)
3. **Type a description**, pick a duration chip (15m/30m/45m/1h/1.5h/2h) or enter a custom number of minutes, tap "Log it"

Logs submit via AJAX so the form resets without a page reload — useful for batching several quick entries after a stretch of meetings.

## Cost

Each classification is ~one image (≤1600px) + small prompt → roughly $0.005–$0.01 with Sonnet. At 15-minute intervals during a 10-hour workday that's ~40 calls/day ≈ $0.20–$0.40/day. Switch to Haiku in `config.json` (`"model": "claude-haiku-4-5-20251001"`) to cut that ~3-5x.

## Privacy

Screenshots never leave your machine except as image payloads to Anthropic during classification. Anthropic does not train on API traffic by default. If a screenshot contains anything you'd rather not send (a password manager, private message, etc.) you can either:
- Pause the tracker briefly (`stop_tracker.bat`), or
- Delete the entry + its screenshot afterward via the dashboard.

## Troubleshooting

- **"No API key" error** — set it in `config.json` or as `ANTHROPIC_API_KEY`.
- **All entries are "Admin/Other"** — your screenshots may be getting downscaled too aggressively, or your `user_context` is too vague. Edit the context, run `python main.py --once`, and inspect the result.
- **Dashboard shows nothing** — confirm `python main.py` is running (check Task Manager for `python.exe` / `pythonw.exe`). Run `--once` to seed an entry.
- **Multi-monitor capture is huge** — that's fine, the image is downscaled to 1600px before sending.
