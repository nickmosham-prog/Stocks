#!/bin/bash
# Double-click to download the latest version of the screener.
# Stop the screener first; start it again afterwards with "Start Screener".

SELF="$0"
[ -L "$SELF" ] && SELF="$(readlink "$SELF")"
cd "$(dirname "$SELF")" || exit 1

pause_and_exit() {
  echo
  read -n 1 -s -r -p "Press any key to close this window."
  echo
  exit "$1"
}

if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:8000/api/status"; then
  echo "The screener is still running."
  echo "Stop it first (click its window and press Control+C), then run this again."
  pause_and_exit 1
fi

echo "Downloading the latest version..."
if ! git pull --ff-only; then
  echo
  echo "The update did not go through. Nothing was changed."
  echo "Send a screenshot of this window to get help."
  pause_and_exit 1
fi

echo
echo "Installing anything new it needs..."
if ! .venv/bin/python -m pip install -q -r requirements.txt; then
  echo
  echo "Installing requirements failed. Send a screenshot of this window to get help."
  pause_and_exit 1
fi

echo
echo "Up to date. Double-click \"Start Screener\" to run it."
pause_and_exit 0
