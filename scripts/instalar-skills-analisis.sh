#!/usr/bin/env bash
# Descarga las skills de analisis de tradermonty/claude-trading-skills (licencia MIT) que sirven
# para revisar los resultados de Marin: backtesting, post-mortem de operaciones, coach de
# desempeno, freno por perdidas y regimen cripto. Las usa Claude Code al analizar tus reportes;
# el bot no las necesita para operar. Se ejecuta sola al abrir una sesion de Claude Code.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=".claude/skills"
MARK="$DEST/.instaladas-analisis"
SKILLS="backtest-expert signal-postmortem trade-performance-coach drawdown-circuit-breaker crypto-regime-analyzer weekly-performance-digest"
if [ -f "$MARK" ] && [ "${1:-}" != "--actualizar" ]; then exit 0; fi
mkdir -p "$DEST"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
git clone -q --depth 1 --filter=blob:none --sparse https://github.com/tradermonty/claude-trading-skills.git "$TMP/r"
git -C "$TMP/r" sparse-checkout set $(for s in $SKILLS; do echo "skills/$s"; done) >/dev/null  # los archivos de la raiz (LICENSE) vienen siempre
for s in $SKILLS; do rm -rf "${DEST:?}/$s"; cp -R "$TMP/r/skills/$s" "$DEST/$s"; done
cp "$TMP/r/LICENSE" "$DEST/LICENSE-claude-trading-skills"
echo "tradermonty/claude-trading-skills @ $(git -C "$TMP/r" rev-parse --short HEAD)" > "$MARK"
echo "Skills de analisis listas: $SKILLS"
