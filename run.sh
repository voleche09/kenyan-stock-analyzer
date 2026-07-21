#!/bin/bash
# Kenyan Stock Analyzer — Single Entry Point
# Usage: ./run.sh

set -e
cd "$(dirname "$0")"

# Activate venv
source venv/bin/activate

# Fix WeasyPrint on macOS
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_LIBRARY_PATH"

# Fix SSL for TradingView
export SSL_CERT_FILE="$VIRTUAL_ENV/lib/python3.14/site-packages/certifi/cacert.pem"

# Run the pipeline
python main.py \
  --report-type both \
  --export-excel \
  --detailed \
  "$@"

# Open the dashboard
echo ""
echo "Opening dashboard..."
open reports/index.html