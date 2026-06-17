#!/usr/bin/env bash
# Start the Otishi Time Tracker in the background and open the dashboard.
# Safe to run repeatedly — if it's already running, it just opens the browser.

cd "$(dirname "$0")" || exit 1

PORT=$(python3 -c "import json;print(json.load(open('config.json')).get('dashboard_port',5555))" 2>/dev/null || echo 5555)

# Already running? (something listening on the port)
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Tracker already running on port $PORT."
else
    echo "Starting Otishi Time Tracker..."
    # nohup so it survives terminal close; output to tracker.log
    nohup python3 main.py >> tracker.log 2>&1 &
    echo $! > .tracker.pid
    # Wait up to ~8s for the dashboard to bind
    for _ in $(seq 1 16); do
        if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then break; fi
        sleep 0.5
    done
    echo "Tracker running (PID $(cat .tracker.pid)). Logs: tracker.log"
fi

echo "Dashboard: http://localhost:$PORT"
open "http://localhost:$PORT/today" 2>/dev/null || true
