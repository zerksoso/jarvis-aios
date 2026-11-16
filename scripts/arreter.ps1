# Arrête proprement l'overlay et le backend de Jarvis AIOS (PID enregistrés dans runtime\).
$AppDir = Split-Path -Parent $PSScriptRoot
$EstInstalle = Test-Path (Join-Path $AppDir 'installed.flag')
if ($EstInstalle) {
  $DataDir = Join-Path $env:USERPROFILE 'JarvisAIOS'
} else {
  $DataDir = $AppDir
  $env:JARVIS_ROOT = $AppDir
}
$Runtime = Join-Path $DataDir 'runtime'

foreach ($nom in 'overlay', 'backend') {
  $f = Join-Path $Runtime "$nom.pid"
  if (Test-Path $f) {
    $id = (Get-Content $f -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($id) {
      $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
      if ($proc) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Write-Host "$nom arrêté (PID $id)."
      }
    }
    Remove-Item $f -Force -ErrorAction SilentlyContinue
  }
}

# Filet de sécurité : processus Python embarqué ou du venv lancés avec backend.main / overlay.main.
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object {
  $_.CommandLine -match 'backend\.main|overlay\.main' -and $_.ExecutablePath -like "$AppDir*"
} | ForEach-Object {
  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  Write-Host "Processus $($_.ProcessId) arrêté."
}
