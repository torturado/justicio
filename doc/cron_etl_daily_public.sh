#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source "$HOME/.config/justicio.env"
set +a
source "$PROJECT_DIR/.venv/bin/activate"
export APP_PATH="$PROJECT_DIR"

cd "$PROJECT_DIR"
python -m src.etls.boe.load today
python -m src.etls.boe_consolidada.load today
