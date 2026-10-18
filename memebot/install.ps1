# Instalador para Windows (PowerShell). Requiere Python 3.10+ de https://www.python.org
# (marca "Add python.exe to PATH" al instalarlo).
# Si PowerShell bloquea el script: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host ""
Write-Host "Listo. Siguiente paso:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python -m memebot run        # modo simulacion (sin dinero real)"
