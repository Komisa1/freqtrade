#!/usr/bin/env bash
# Descarga las skills de video (HyperFrames y Remotion) desde sus repositorios oficiales
# a .claude/skills/. Se ejecuta solo al abrir una sesion de Claude Code (hook SessionStart).
# Si ya estan descargadas no hace nada; para actualizarlas: bash scripts/instalar-skills.sh --actualizar
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=".claude/skills"
MARK="$DEST/.instaladas"
if [ -f "$MARK" ] && [ "${1:-}" != "--actualizar" ]; then
  exit 0
fi
mkdir -p "$DEST"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
bajar() {  # $1 = repo de GitHub (dueno/nombre)
  git clone -q --depth 1 --filter=blob:none --sparse "https://github.com/$1.git" "$TMP/$2"
  git -C "$TMP/$2" sparse-checkout set skills >/dev/null
  for d in "$TMP/$2"/skills/*/; do
    name=$(basename "$d")
    rm -rf "${DEST:?}/$name"
    cp -R "$d" "$DEST/$name"
  done
  echo "$1 @ $(git -C "$TMP/$2" rev-parse --short HEAD)" >> "$MARK.tmp"
}
: > "$MARK.tmp"
bajar heygen-com/hyperframes hyperframes
bajar remotion-dev/skills remotion
mv "$MARK.tmp" "$MARK"
echo "Skills de video listas en $DEST: $(ls "$DEST" | tr '\n' ' ')"
