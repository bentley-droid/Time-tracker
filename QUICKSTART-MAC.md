# Quick Start — macOS

Step-by-step to get the Otishi Time Tracker running on a Mac.

## 1. Install Python 3 (if you don't have it)

Check first:
```bash
python3 --version
```
If that errors or shows something older than 3.10, install via [Homebrew](https://brew.sh):
```bash
brew install python3
```

## 2. Open Terminal in this folder

In Finder, right-click the `time-tracker` folder → **New Terminal at Folder**.
(Or `cd` into wherever you unzipped it.)

## 3. Install dependencies

```bash
pip3 install -r requirements.txt
```

## 4. Add your Anthropic API key

Open `config.json` in any text editor and replace
`PASTE_YOUR_ANTHROPIC_API_KEY_HERE` with your key:
```json
"anthropic_api_key": "sk-ant-...",
```
(Or set it as an environment variable instead: `export ANTHROPIC_API_KEY=sk-ant-...`)

## 5. Test it once

```bash
python3 main.py --once
```
This takes one screenshot, classifies it with Claude, prints the result, and exits.

**First run will ask for Screen Recording permission** — macOS shows a prompt the
first time the tracker grabs the screen. Go to **System Settings → Privacy &
Security → Screen Recording** and enable it for **Terminal** (or whatever app you
launched from). You may need to quit and reopen Terminal afterward.

## 6. Run it

```bash
./start_tracker.sh
```
This launches the tracker in the background and opens the dashboard in your
browser. It keeps running after you close Terminal.

- **Stop it:** `./stop_tracker.sh`
- **Start at login (optional):** `./install_autostart.sh`

If you get `permission denied`, make the scripts executable first:
```bash
chmod +x *.sh
```

## 7. Open the dashboard

[http://localhost:5555](http://localhost:5555)

- **Day / Week** — category breakdowns
- **Timeline** — visual day/week strip (drag blocks to adjust times)
- **Weekly Report** — shareable summary (with CSV export)
- **Quick log** — manual entries + start/stop timer

## Clocking in/out (important)

The tracker only takes screenshots **while you're clocked in.** Two ways:

- **Manual:** the **Clock in / Clock out** button in the top-right of the dashboard (also on the mobile Quick log page).
- **Automatic schedule:** click **⚙ Schedule** to set per-weekday work windows. It auto-clocks you in at each window's start and out at its end. Default is Mon–Fri 9:00–18:00, weekends off — edit to your hours.

If you ever notice nothing's being logged, check that you're clocked in (the dot turns green).

---

## Phone access via Tailscale (optional)

The dashboard binds to `0.0.0.0`, so it's reachable from your phone over a private
[Tailscale](https://tailscale.com) network — encrypted device-to-device, nothing
exposed to the public internet.

1. **Install Tailscale** on both devices:
   - Mac: `brew install --cask tailscale` (or download from tailscale.com/download)
   - Phone: the Tailscale app from the App Store / Play Store
2. **Sign in with the same Google account on both.**
3. **Find your Mac's Tailscale IP:**
   ```bash
   tailscale ip -4
   ```
   You'll get something like `100.x.y.z`.
4. **On your phone**, open `http://100.x.y.z:5555/log` for the quick-log page
   (or `/today` for the full dashboard). Save it to your home screen for one-tap access.

**Security:** Tailscale traffic is end-to-end encrypted (WireGuard). The `/api/log`
write endpoint is additionally locked to localhost, so even over Tailscale nothing
remote can write to it directly.

---

## Notes for Mac

- **Idle backfill popup:** when the tracker notices your screen sat unchanged for a
  while and then you came back, it pops a native macOS dialog asking what you were
  doing. If you dismiss it, the same prompt also shows as a banner on the dashboard.
- **Screenshots** are stored locally in `screenshots/` and auto-deleted after
  `retention_days` (default 30). The log entries stay.
- **Everything is local** except the screenshot sent to Claude for classification.
