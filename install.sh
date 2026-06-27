#!/usr/bin/env bash
# humaneval-eval one-click installer
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

echo "=== humaneval-eval setup ==="

# Detect Python
PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" &>/dev/null; then
  echo "ERROR: $PYTHON not found. Install Python 3.10+ first." >&2
  exit 1
fi

# Create venv
if [ ! -d ".venv" ]; then
  echo "[1/3] Creating virtual environment..."
  "$PYTHON" -m venv .venv
else
  echo "[1/3] Virtual environment already exists."
fi

# Activate and install deps
echo "[2/3] Installing dependencies..."
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Verify
echo "[3/3] Verifying installation..."
.venv/bin/python3 -c "import yaml; import requests; import pytest; print('deps OK')"
.venv/bin/pytest tests/ -q --no-header 2>&1 | tail -1

echo ""
echo "=== Setup complete ==="
echo ""
echo "Quick start:"
echo "  source .venv/bin/activate"
echo "  python3 humaneval_eval.py --list"
echo "  python3 humaneval_eval.py --config config.yaml --models your-model --num 3 --db"
echo ""
echo "Set your API key:"
echo "  export YOUR_API_KEY=<your_key_here>"
echo "  # match the api_key_env in your config.yaml"
