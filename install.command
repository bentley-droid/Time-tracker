#!/bin/bash
# Otishi Time Tracker — one-click installer for Mac.
# Double-click this file. It downloads the app, installs it, starts it, and
# opens the setup screen in your browser. Safe to run again to update/repair.

REPO="https://github.com/bentley-droid/Time-tracker.git"
DEST="$HOME/OtishiTimeTracker"

echo ""
echo "=============================================="
echo "   Otishi Time Tracker — Setup"
echo "=============================================="
echo ""

# 1. Python 3 -------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed yet."
  echo "Opening the download page — install it, then double-click this installer again."
  open "https://www.python.org/downloads/"
  echo ""
  read -p "Press Return to close this window."
  exit 1
fi
echo "[1/5] Python 3 found: $(python3 --version)"

# 2. git (Xcode Command Line Tools) --------------------------------------
if ! command -v git >/dev/null 2>&1; then
  echo "[2/5] Installing developer tools (one-time)..."
  echo "      Approve the popup that appears, let it finish, then run this installer again."
  xcode-select --install >/dev/null 2>&1 || true
  echo ""
  read -p "Press Return to close this window."
  exit 1
fi
echo "[2/5] git found"

# 3. Download (or update) the app ----------------------------------------
if [ -d "$DEST/.git" ]; then
  echo "[3/5] Updating existing install at $DEST ..."
  git -C "$DEST" pull --rebase --autostash || true
else
  echo "[3/5] Downloading the app to $DEST ..."
  git clone "$REPO" "$DEST" || { echo "Download failed (check your internet)."; read -p "Press Return."; exit 1; }
fi

cd "$DEST" || { echo "Could not open $DEST"; read -p "Press Return."; exit 1; }

# 4. Dependencies --------------------------------------------------------
echo "[4/5] Installing dependencies (this can take a minute the first time)..."
python3 -m pip install --user --quiet --upgrade pip >/dev/null 2>&1 || true
python3 -m pip install --user --quiet -r requirements.txt || {
  echo "Dependency install hit a problem. Trying without --user..."
  python3 -m pip install --quiet -r requirements.txt || { echo "Install failed."; read -p "Press Return."; exit 1; }
}

# 5. Start now + enable start-at-login, then open the dashboard -----------
echo "[5/5] Starting the tracker..."
chmod +x *.sh 2>/dev/null || true
./install_autostart.sh >/dev/null 2>&1 || true   # launches now + every login
./start_tracker.sh >/dev/null 2>&1 || true        # idempotent: opens the browser too

echo ""
echo "=============================================="
echo "  All set!"
echo "  Your browser should open to a setup screen."
echo "  1) Pick your name."
echo "  2) Paste the API key Bentley sent you."
echo ""
echo "  Dashboard anytime:  http://localhost:5555"
echo "  It runs in the background and updates itself."
echo "=============================================="
echo ""
read -p "Press Return to close this window."
