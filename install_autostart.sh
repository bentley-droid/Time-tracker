#!/usr/bin/env bash
# One-time setup: install a macOS LaunchAgent so the tracker starts at login.
# Creates ~/Library/LaunchAgents/com.otishi.timetracker.plist

set -e
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
PYTHON="$(command -v python3)"
LABEL="com.otishi.timetracker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -z "$PYTHON" ]; then
    echo "ERROR: python3 not found on PATH. Install it first (brew install python3)."
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$PROJECT_DIR/main.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/tracker.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/tracker.log</string>
</dict>
</plist>
PLISTEOF

# Reload the agent (unload first in case it already exists)
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Auto-start installed: $PLIST"
echo "The tracker will now launch automatically when you log in."
echo
echo "To start it right now:   launchctl start $LABEL"
echo "To remove auto-start:    launchctl unload \"$PLIST\" && rm \"$PLIST\""
