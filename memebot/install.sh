#!/usr/bin/env bash
# Instalador para Linux / macOS
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
[ -f config.json ] || cp config.example.json config.json
[ -f .env ] || cp .env.example .env
echo
echo "Listo. Siguiente paso:"
echo "  source .venv/bin/activate"
echo "  python -m memebot run        # modo simulacion (sin dinero real)"
