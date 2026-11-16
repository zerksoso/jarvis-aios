# Lance Jarvis AIOS : backend (127.0.0.1:5230), overlay HUD, puis ouvre le tableau de bord.
# Usage : powershell -NoProfile -ExecutionPolicy Bypass -File scripts\lancer.ps1 [-SansNavigateur] [-SansOverlay]
param([switch]$SansNavigateur, [switch]$SansOverlay)
$ErrorActionPreference = 'Stop'

$AppDir = Split-Path -Parent $PSScriptRoot
$EstInstalle = Test-Path (Join-Path $AppDir 'installed.flag')
if ($EstInstalle) {
  $DataDir = Join-Path $env:USERPROFILE 'JarvisAIOS'
} else {
  $DataDir = $AppDir
  $env:JARVIS_ROOT = $AppDir
}

$Runtime = Join-Path $DataDir 'runtime'
$Logs = Join-Path $Runtime 'logs'
New-Item -ItemType Directory -Force -Path $Logs | Out-Null

$PyEmbarque = Join-Path $AppDir 'python\python.exe'
$PywEmbarque = Join-Path $AppDir 'python\pythonw.exe'
if (Test-Path $PyEmbarque) {
  $Py = $PyEmbarque
  if (Test-Path $PywEmbarque) { $Pyw = $PywEmbarque } else { $Pyw = $PyEmbarque }
} else {
  $Py = Join-Path $AppDir '.venv\Scripts\python.exe'
  $Pyw = Join-Path $AppDir '.venv\Scripts\pythonw.exe'
}
$Url = 'http://127.0.0.1:5230'

# Vérifie si le backend répond déjà.
function Test-Backend {
  try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "$Url/api/sante").StatusCode -eq 200 } catch { $false }
}

# Vérifie si un processus enregistré dans un fichier PID est vivant.
function Test-Pid($file) {
  if (-not (Test-Path $file)) { return $false }
  $id = (Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $id) { return $false }
  return [bool](Get-Process -Id $id -ErrorAction SilentlyContinue)
}

if (-not (Test-Path $Py)) { Write-Host "Environnement Python introuvable ($Py)." -ForegroundColor Red; exit 1 }

if (Test-Backend) {
  Write-Host "Backend déjà lancé." -ForegroundColor Cyan
} else {
  Write-Host "Démarrage du backend…" -ForegroundColor Cyan
  $p = Start-Process -FilePath $Py -ArgumentList '-m','backend.main' -WorkingDirectory $AppDir -WindowStyle Hidden `
       -RedirectStandardOutput (Join-Path $Logs 'backend.out.log') -RedirectStandardError (Join-Path $Logs 'backend.err.log') -PassThru
  Set-Content -Path (Join-Path $Runtime 'backend.pid') -Value $p.Id
  $ok = $false
  for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Milliseconds 500; if (Test-Backend) { $ok = $true; break } }
  if (-not $ok) { Write-Host "Le backend ne répond pas. Voir $Logs\backend.err.log" -ForegroundColor Red; exit 2 }
  Write-Host "Backend prêt sur $Url" -ForegroundColor Green
}

if (-not $SansOverlay) {
  if (Test-Pid (Join-Path $Runtime 'overlay.pid')) {
    Write-Host "Overlay déjà lancé." -ForegroundColor Cyan
  } elseif (Test-Path $Pyw) {
    $o = Start-Process -FilePath $Pyw -ArgumentList '-m','overlay.main' -WorkingDirectory $AppDir -PassThru
    Set-Content -Path (Join-Path $Runtime 'overlay.pid') -Value $o.Id
    Write-Host "Overlay HUD lancé (Ctrl+Espace pour parler)." -ForegroundColor Green
  } else {
    Write-Host "Exécutable pythonw introuvable ($Pyw) ; overlay ignoré." -ForegroundColor Yellow
  }
}

if (-not $SansNavigateur) { Start-Process "$Url/#/accueil" }
