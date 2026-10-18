# Actualiza el bot a la ultima version (Windows). Tus archivos .env, config.json y data/
# NO se tocan. Uso: .\update.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$zip = Join-Path $env:TEMP "memebot-update.zip"
$tmp = Join-Path $env:TEMP "memebot-update"
Write-Host "Descargando la ultima version..."
Invoke-WebRequest "https://github.com/Komisa1/freqtrade/archive/refs/heads/memebot-solo.zip" -OutFile $zip
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive $zip $tmp
Copy-Item (Join-Path $tmp "freqtrade-memebot-solo\*") . -Recurse -Force
.\.venv\Scripts\python.exe -m pip install -q -r requirements.txt
Remove-Item $zip, $tmp -Recurse -Force
Write-Host "Listo, bot actualizado. Arrancalo con doble clic en start.bat"
