# Instalador para Windows: Node.js, FFmpeg y Git (con winget) + skills de video para Claude Code.
# Uso: abre PowerShell en esta carpeta y corre  .\instalar.ps1
# Si PowerShell bloquea el script: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Tiene($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Instalar($id, $nombre) {
  Write-Host "Instalando $nombre..."
  winget install --id $id -e --accept-package-agreements --accept-source-agreements --silent
}

if (-not (Tiene winget)) { throw "No encontre winget. Actualiza 'Instalador de aplicacion' desde Microsoft Store." }
if (-not (Tiene node))   { Instalar "OpenJS.NodeJS.LTS" "Node.js" }
if (-not (Tiene ffmpeg)) { Instalar "Gyan.FFmpeg" "FFmpeg" }
if (-not (Tiene git))    { Instalar "Git.Git" "Git" }
# refrescar el PATH de esta ventana para ver lo recien instalado
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")

$nodeMajor = [int]((node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 22) { Write-Host "OJO: HyperFrames necesita Node 22 o mas nuevo (tienes $(node --version)). Corre: winget upgrade OpenJS.NodeJS.LTS" }

# skills de video desde los repositorios oficiales
$dest = ".claude\skills"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$tmp = Join-Path $env:TEMP ("skills-" + [guid]::NewGuid())
foreach ($repo in @("heygen-com/hyperframes", "remotion-dev/skills")) {
  $dir = Join-Path $tmp ($repo -replace "/", "-")
  git clone -q --depth 1 --filter=blob:none --sparse "https://github.com/$repo.git" $dir
  git -C $dir sparse-checkout set skills | Out-Null
  Get-ChildItem (Join-Path $dir "skills") -Directory | ForEach-Object {
    $target = Join-Path $dest $_.Name
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    Copy-Item $_.FullName $target -Recurse
  }
}
Remove-Item $tmp -Recurse -Force
"instalado desde Windows $(Get-Date -Format s)" | Out-File (Join-Path $dest ".instaladas") -Encoding utf8

# sin estadisticas anonimas de HyperFrames
[Environment]::SetEnvironmentVariable("DO_NOT_TRACK", "1", "User")

Write-Host ""
Write-Host "Listo. Skills instaladas: $((Get-ChildItem $dest -Directory).Count)"
Write-Host "Prueba el ejemplo:  cd ejemplos\marin-promo ;  npx hyperframes render"
Write-Host "O abre Claude Code en esta carpeta y pidele: /hyperframes hazme un video de ..."
