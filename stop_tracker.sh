#!/usr/bin/env bash
# Stop the background Otishi Time Tracker process.

cd "$(dirname "$0")" || exit 1

echo "Stopping Otishi Time Tracker..."

killed=0

# Preferred: kill the PID we recorded at startup
if [ -f .tracker.pid ]; then
    pid=$(cat .tracker.pid)
    if kill "$pid" >/dev/null 2>&1; then
        echo "  killed PID $pid"
        killed=1
    fi
    rm -f .tracker.pid
fi

# Fallback: find any python running main.py from this folder
for pid in $(pgrep -f "[p]ython3 main.py"); do
    if kill "$pid" >/dev/null 2>&1; then
        echo "  killed PID $pid"
        killed=1
    fi
done

if [ "$killed" -eq 0 ]; then
    echo "  no running tracker found."
else
    echo "Done."
fi
