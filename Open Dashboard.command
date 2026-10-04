#!/bin/bash
# Double-click this file in Finder to open your stock dashboard with
# click-to-add (search for a stock, add it to your watchlist, record a
# purchase...). A Terminal window opens — keep it open while you use the
# dashboard; close it to stop.
cd "$(dirname "$0")" || exit 1

if [ ! -x "./venv/bin/python3" ]; then
    echo ""
    echo "The dashboard isn't set up on this computer yet."
    echo "Open Terminal in this folder and run these three lines once:"
    echo ""
    echo "    python3 -m venv venv"
    echo "    source venv/bin/activate"
    echo "    pip install -r requirements.txt"
    echo ""
    echo "Then double-click 'Open Dashboard.command' again."
    echo ""
    read -r -p "Press Enter to close this window… " _
    exit 1
fi

./venv/bin/python3 app.py "$@"
status=$?
if [ $status -ne 0 ]; then
    echo ""
    read -r -p "Something went wrong (see above). Press Enter to close this window… " _
fi
exit $status
