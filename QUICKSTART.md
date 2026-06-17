# Quick Start

1. **Open PowerShell in this folder:** Shift+right-click `C:\Users\bentl\Projects\time-tracker` → "Open PowerShell window here".

2. **Install dependencies:**
   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

3. **Paste your Anthropic API key into `config.json`** (replace `PASTE_YOUR_ANTHROPIC_API_KEY_HERE`).

4. **Test it once** (takes a screenshot, classifies, prints result):
   ```powershell
   py -3 main.py --once
   ```

5. **Start it for real** (background, no window):
   ```powershell
   .\start_tracker.bat
   ```
   The dashboard opens automatically.

6. **Open the dashboard:** http://localhost:5555

7. **Phone access (optional):**
   - Install Tailscale on laptop + phone, sign in with the same Google account on both
   - On laptop: `tailscale ip -4` → note the `100.x.y.z` address
   - On phone: open `http://100.x.y.z:5555/log` (bookmark to home screen)

**Stop:** `.\stop_tracker.bat`  
**Auto-start at login:** `.\install_autostart.bat` (one-time setup)
