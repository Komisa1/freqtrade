#!/usr/bin/env bash
# Genera el video completo: música -> volumen normalizado -> render 1080p con audio.
# Uso: ./build.sh [salida.mp4] [fps]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$(realpath -m "${1:-$HERE/out/viaje-japon.mp4}")"
FPS="${2:-30}"
WORK="$HERE/out"; mkdir -p "$WORK" "$(dirname "$OUT")"

node "$HERE/music.mjs" "$WORK/musica-raw.wav" 62
# -14 LUFS: volumen estándar de redes sociales
ffmpeg -y -loglevel error -i "$WORK/musica-raw.wav" -af loudnorm=I=-14:TP=-1.5:LRA=11 -ar 44100 "$WORK/musica.wav"
node "$HERE/../render.mjs" --scene "$HERE/japon.html" --width 1920 --height 1080 --fps "$FPS" \
  --audio "$WORK/musica.wav" --out "$OUT"
