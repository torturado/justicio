#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a
source "$HOME/.config/justicio.env"
set +a
source "$PROJECT_DIR/.venv/bin/activate"

cd "$PROJECT_DIR"
# Updates every bulletin and then the consolidated legislation. Exits with status 1 and emails the report
# when any source fails.
exec python -m src.etls.cli daily
