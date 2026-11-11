#!/bin/bash
# Double-click to start the screener on a Mac.
# Keeps the Mac awake while it runs and opens the dashboard when it is ready.

SELF="$0"
[ -L "$SELF" ] && SELF="$(readlink "$SELF")"
cd "$(dirname "$SELF")" || exit 1

URL="http://127.0.0.1:8000"

pause_and_exit() {
  echo
  read -n 1 -s -r -p "Press any key to close this window."
  echo
  exit "$1"
}

if [ ! -x .venv/bin/python ]; then
  echo "The screener isn't set up in this folder yet (no .venv folder found)."
  echo "Run the Quick start steps in README.md once, then try again."
  pause_and_exit 1
fi

if curl -s -o /dev/null --max-time 2 "$URL/api/status"; then
  echo "The screener is already running. Opening the dashboard."
  open "$URL"
  pause_and_exit 0
fi

# Start moomoo OpenD if it is installed. Harmless if it isn't.
if open -g -a "moomoo_OpenD" 2>/dev/null; then
  echo "Opened moomoo OpenD. Log in there if it asks (don't click Unlock Trade)."
fi

# Open the dashboard once the screener answers (up to about 10 minutes,
# since the first start of the day may refresh price history first).
(
  for _ in $(seq 1 300); do
    if curl -s -o /dev/null --max-time 2 "$URL/api/status"; then
      open "$URL"
      break
    fi
    sleep 2
  done
) &
WAITER=$!

echo "Starting the screener. Leave this window open."
echo "To stop it: click this window and press Control+C."
echo
caffeinate -i .venv/bin/python main.py
STATUS=$?

kill "$WAITER" 2>/dev/null
echo
echo "The screener has stopped."
pause_and_exit "$STATUS"
