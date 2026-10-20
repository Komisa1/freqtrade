# Marín (memebot)

Bot de meme coins en Solana. El usuario no es programador: explica en español sencillo y dale
comandos completos para copiar y pegar en PowerShell (`.\.venv\Scripts\python.exe -m memebot ...`).

## Al revisar resultados
- Pide el texto de `python -m memebot report` (por etapa, por forma de entrada, ganadoras vs
  perdedoras, últimas 15) antes de cambiar la estrategia. No cambies parámetros a ciegas.
- Skills de análisis disponibles en `.claude/skills/` (se descargan solas con
  `scripts/instalar-skills-analisis.sh`): `backtest-expert` (robustez, sobreajuste, slippage),
  `signal-postmortem` (falsos positivos, oportunidades perdidas), `trade-performance-coach`,
  `drawdown-circuit-breaker`, `crypto-regime-analyzer`, `weekly-performance-digest`.
  Están pensadas para acciones/swing: toma la metodología, no los umbrales.
- Con pocas operaciones (<30 por grupo) los resultados son ruido: dilo.
- Nunca pidas ni leas `.env` (clave privada).

## Desarrollo
- Tests: `python -m pytest` (desde esta carpeta). Lint: `ruff check memebot tests`.
- Las estrategias se versionan como presets en `memebot/config.py` (`STRATEGY_PRESETS`) y se
  aplican con `python -m memebot estrategia <preset>`.
