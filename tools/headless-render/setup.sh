#!/usr/bin/env bash
# Instala el entorno del render headless: Node.js 24 LTS, FFmpeg 9 (estático)
# y Playwright con su Chromium. Idempotente: si algo ya está, lo salta.
set -euo pipefail

NODE_VERSION="${NODE_VERSION:-v24.22.0}"
FFMPEG_SERIES="${FFMPEG_SERIES:-9.0}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$BIN_DIR"
export PATH="$BIN_DIR:$PATH"

# --- Node.js ---------------------------------------------------------------
if [[ "$(node -v 2>/dev/null)" != "$NODE_VERSION" ]]; then
  echo ">> Node.js $NODE_VERSION"
  tarball="node-$NODE_VERSION-linux-x64.tar.xz"
  curl -fsSL -o "$TMP/$tarball" "https://nodejs.org/dist/$NODE_VERSION/$tarball"
  (cd "$TMP" && curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" \
     | grep " $tarball\$" | sha256sum -c -)
  mkdir -p /opt/node24
  tar -xJf "$TMP/$tarball" -C /opt/node24 --strip-components=1
  for b in node npm npx corepack; do ln -sf "/opt/node24/bin/$b" "$BIN_DIR/$b"; done
fi

# --- FFmpeg ----------------------------------------------------------------
if ! ffmpeg -version 2>/dev/null | head -1 | grep -q "version n$FFMPEG_SERIES"; then
  echo ">> FFmpeg $FFMPEG_SERIES"
  curl -fsSL -o "$TMP/ffmpeg.tar.xz" \
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n$FFMPEG_SERIES-latest-linux64-gpl-$FFMPEG_SERIES.tar.xz"
  mkdir -p /opt/ffmpeg
  tar -xJf "$TMP/ffmpeg.tar.xz" -C /opt/ffmpeg --strip-components=1
  ln -sf /opt/ffmpeg/bin/ffmpeg "$BIN_DIR/ffmpeg"
  ln -sf /opt/ffmpeg/bin/ffprobe "$BIN_DIR/ffprobe"
fi

# --- Playwright + Chromium -------------------------------------------------
cd "$HERE"
npm install --no-audit --no-fund
if ! npx playwright install chromium; then
  # Si cdn.playwright.dev está bloqueado, baja el mismo Chrome for Testing
  # desde el bucket público de Google y colócalo donde Playwright lo busca.
  echo ">> Fallback: Chrome for Testing desde storage.googleapis.com"
  info="$(npx playwright install --dry-run chromium)"
  cft="$(grep -oE 'Chrome for Testing [0-9.]+' <<<"$info" | head -1 | awk '{print $4}')"
  rev="$(grep -oE 'chromium-[0-9]+' <<<"$info" | head -1 | cut -d- -f2)"
  base="https://storage.googleapis.com/chrome-for-testing-public/$cft/linux64"
  for pair in "chromium-$rev:chrome-linux64" "chromium_headless_shell-$rev:chrome-headless-shell-linux64"; do
    dir="$PLAYWRIGHT_BROWSERS_PATH/${pair%%:*}"; zip="${pair##*:}.zip"
    curl -fsSL -o "$TMP/$zip" "$base/$zip"
    mkdir -p "$dir" && unzip -q -o "$TMP/$zip" -d "$dir" && touch "$dir/INSTALLATION_COMPLETE"
  done
fi

echo
node -v; ffmpeg -version | head -1; npx playwright --version
echo "Listo. Asegúrate de tener $BIN_DIR al principio del PATH."
