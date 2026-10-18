#!/usr/bin/env bash
# Actualiza el bot a la ultima version (Linux/macOS). No toca .env, config.json ni data/.
set -e
cd "$(dirname "$0")"
tmp=$(mktemp -d)
curl -sSL "https://github.com/Komisa1/freqtrade/archive/refs/heads/memebot-solo.zip" -o "$tmp/u.zip"
unzip -q "$tmp/u.zip" -d "$tmp"
cp -R "$tmp/freqtrade-memebot-solo/." .
.venv/bin/pip install -q -r requirements.txt
rm -rf "$tmp"
echo "Listo, bot actualizado."
