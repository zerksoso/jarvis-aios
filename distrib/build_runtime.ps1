<#
.SYNOPSIS
Construit le runtime Python 3.12 autonome et élagué pour Jarvis AIOS.

.DESCRIPTION
Installe Python 3.12 via uv, copie le runtime autonome vers -Sortie (par défaut
build\python), installe uniquement requirements.txt (sans outils dev), élague
les fichiers superflus (__pycache__, tests/, *.pyi, pip/setuptools), vérifie la
taille et les imports critiques, puis écrit RUNTIME_VERSION (SHA-256 court de
la version Python + requirements.txt).

.PARAMETER Sortie
Dossier cible du runtime embarqué (relatif à la racine du dépôt ou absolu).
Par défaut : build\python.
#>
[CmdletBinding()]
param(
    [string]$Sortie = 'build\python'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ([System.IO.Path]::IsPathRooted($Sortie)) {
    $SortieAbs = [System.IO.Path]::GetFullPath($Sortie)
} else {
    $SortieAbs = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Sortie))
}

$RequirementsPath = Join-Path $RepoRoot 'requirements.txt'
if (-not (Test-Path $RequirementsPath)) {
    throw "Fichier requirements.txt introuvable : $RequirementsPath"
}

$UvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $UvCmd) {
    throw "L'outil 'uv' est introuvable dans le PATH."
}

function Remove-CheminSiPresent {
    param([string]$Chemin)
    if (Test-Path $Chemin) {
        Remove-Item -Path $Chemin -Recurse -Force
    }
}

function Get-PythonStandaloneDir {
    # Localise le Python autonome géré par uv (dossier réel, pas la jonction ni un venv du projet).
    $base = (& uv python dir | Select-Object -First 1).ToString().Trim()
    $candidats = Get-ChildItem -Path $base -Directory -Filter 'cpython-3.12.*-windows-x86_64-none' -ErrorAction SilentlyContinue |
        Where-Object { -not $_.LinkType -and (Test-Path (Join-Path $_.FullName 'python.exe')) } |
        Sort-Object Name -Descending
    if (-not $candidats) {
        throw "Python 3.12 autonome introuvable dans $base (uv python install 3.12)."
    }
    return @($candidats)[0].FullName
}

function Get-RuntimeVersionHash {
    # Empreinte courte (12 hex) de la version de Python + requirements.txt : change quand le runtime doit être réinstallé.
    param(
        [string]$PythonExe,
        [string]$ReqFile
    )
    $pyVer = (& $PythonExe -c "import sys; print('{0}.{1}.{2}'.format(*sys.version_info[:3]))").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $pyVer) {
        throw "Impossible de lire la version du runtime Python."
    }
    $reqBrut = [System.IO.File]::ReadAllText($ReqFile, [System.Text.Encoding]::UTF8)
    $contenu = "$pyVer`n" + $reqBrut.Replace("`r`n", "`n")
    $octets = [System.Text.Encoding]::UTF8.GetBytes($contenu)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try { $hashOctets = $sha256.ComputeHash($octets) } finally { $sha256.Dispose() }
    return ([System.BitConverter]::ToString($hashOctets).Replace('-', '').ToLowerInvariant()).Substring(0, 12)
}

Write-Host '[1/6] Installation / vérification de Python 3.12 via uv...' -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'
& uv python install 3.12 2>&1 | ForEach-Object { Write-Host "$_" }
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) {
    throw "Échec de 'uv python install 3.12' (code $LASTEXITCODE)."
}

$PySrcDir = Get-PythonStandaloneDir
if (-not (Test-Path (Join-Path $PySrcDir 'python.exe'))) {
    throw "Interpréteur python.exe introuvable dans $PySrcDir"
}

Write-Host "[2/6] Copie du runtime autonome vers $SortieAbs..." -ForegroundColor Cyan
Remove-CheminSiPresent -Chemin $SortieAbs
New-Item -ItemType Directory -Force -Path $SortieAbs | Out-Null
Copy-Item -Path (Join-Path $PySrcDir '*') -Destination $SortieAbs -Recurse -Force

$TargetPy = Join-Path $SortieAbs 'python.exe'
$TargetPyw = Join-Path $SortieAbs 'pythonw.exe'
if (-not (Test-Path $TargetPy)) {
    throw "python.exe absent après copie dans $SortieAbs"
}
if (-not (Test-Path $TargetPyw)) {
    throw "pythonw.exe absent après copie dans $SortieAbs"
}

$ExtManaged = Join-Path $SortieAbs 'Lib\EXTERNALLY-MANAGED'
if (Test-Path $ExtManaged) {
    Remove-Item -Path $ExtManaged -Force
}

Write-Host '[3/6] Installation des dépendances runtime (requirements.txt)...' -ForegroundColor Cyan
& uv pip install --python $TargetPy -r $RequirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "Échec de l'installation des dépendances via uv pip (code $LASTEXITCODE)."
}

Write-Host '[4/6] Élagage du runtime (__pycache__, tests, *.pyi, outils de packaging)...' -ForegroundColor Cyan
Remove-CheminSiPresent -Chemin (Join-Path $SortieAbs 'Lib\test')
Remove-CheminSiPresent -Chemin (Join-Path $SortieAbs 'Lib\ensurepip')

$SitePackages = Join-Path $SortieAbs 'Lib\site-packages'
if (Test-Path $SitePackages) {
    $dossiersTests = Get-ChildItem -Path $SitePackages -Directory -Recurse -Filter 'tests' -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending
    foreach ($d in $dossiersTests) {
        if (Test-Path $d.FullName) {
            Remove-Item -Path $d.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    foreach ($motif in @('pip', 'pip-*.dist-info', 'setuptools', 'setuptools-*.dist-info', 'pkg_resources', 'wheel', 'wheel-*.dist-info')) {
        Get-ChildItem -Path $SitePackages -Filter $motif -ErrorAction SilentlyContinue | ForEach-Object {
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# PySide6 : l'overlay n'utilise que Core, Gui, Widgets et Multimedia. On retire WebEngine, Qt3D, Quick/QML, Designer, etc.
$PySideDir = Join-Path $SitePackages 'PySide6'
if (Test-Path $PySideDir) {
    $motifsQt = @('Qt6WebEngine*', 'QtWebEngine*', 'Qt63D*', 'Qt3D*', 'Qt6Quick*', 'QtQuick*', 'Qt6Qml*', 'QtQml*', 'Qt6Designer*', 'QtDesigner*',
        'Qt6Charts*', 'QtCharts*', 'Qt6DataVisualization*', 'QtDataVisualization*', 'Qt6Pdf*', 'QtPdf*', 'Qt6Location*', 'QtLocation*',
        'Qt6Bluetooth*', 'QtBluetooth*', 'Qt6Nfc*', 'QtNfc*', 'Qt6Sensors*', 'QtSensors*', 'Qt6SerialPort*', 'QtSerialPort*', 'Qt6SerialBus*',
        'Qt6Scxml*', 'QtScxml*', 'Qt6Test*', 'QtTest*', 'Qt6Help*', 'QtHelp*', 'Qt6Graphs*', 'QtGraphs*', 'Qt6Spatial*', 'Qt6RemoteObjects*',
        'QtRemoteObjects*', 'Qt6Positioning*', 'QtPositioning*', 'Qt6WebChannel*', 'QtWebChannel*', 'Qt6WebSockets*', 'QtWebSockets*',
        'Qt6Sql*', 'QtSql*', 'Qt6Concurrent*', 'Qt6Xml*', 'QtXml*', 'Qt6HttpServer*', 'Qt6StateMachine*', 'Qt6TextToSpeech*', 'QtTextToSpeech*',
        'lupdate*', 'lrelease*', 'designer*', 'assistant*', 'linguist*', 'qmlls*', 'qml*', 'translations', 'resources', 'include', 'typesystems',
        'glue', 'scripts', 'examples', 'metatypes')
    foreach ($m in $motifsQt) {
        Get-ChildItem -Path $PySideDir -Filter $m -ErrorAction SilentlyContinue | ForEach-Object {
            Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    $QtDir = Join-Path $PySideDir 'Qt'
    foreach ($m in @('qml', 'translations', 'resources', 'metatypes', 'libexec')) {
        Remove-CheminSiPresent -Chemin (Join-Path $QtDir $m)
    }
}

$pycaches = Get-ChildItem -Path $SortieAbs -Directory -Recurse -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Sort-Object { $_.FullName.Length } -Descending
foreach ($pc in $pycaches) {
    if (Test-Path $pc.FullName) {
        Remove-Item -Path $pc.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Get-ChildItem -Path $SortieAbs -File -Recurse -Filter '*.pyi' -ErrorAction SilentlyContinue | ForEach-Object {
    Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
}

Write-Host '[5/6] Vérification de la taille et des imports critiques...' -ForegroundColor Cyan
$tailleOctets = (Get-ChildItem -Path $SortieAbs -File -Recurse | Measure-Object -Property Length -Sum).Sum
$tailleMo = [math]::Round($tailleOctets / 1MB, 1)
Write-Host ("Taille finale du runtime : {0} Mo" -f $tailleMo) -ForegroundColor Gray
if ($tailleMo -lt 50) {
    throw ("Taille du runtime anormalement faible ({0} Mo < 50 Mo)." -f $tailleMo)
}
if ($tailleMo -gt 2000) {
    Write-Warning ("Taille du runtime élevée ({0} Mo > 2000 Mo)." -f $tailleMo)
}

& $TargetPy -c "import fastapi, uvicorn, PySide6, faster_whisper"
if ($LASTEXITCODE -ne 0) {
    throw "Échec de validation des imports critiques dans le runtime embarqué."
}

Write-Host '[6/6] Calcul et écriture de RUNTIME_VERSION...' -ForegroundColor Cyan
$RuntimeVersion = Get-RuntimeVersionHash -PythonExe $TargetPy -ReqFile $RequirementsPath
$Utf8SansBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText((Join-Path $SortieAbs 'RUNTIME_VERSION'), $RuntimeVersion, $Utf8SansBom)
Write-Host "Runtime prêt dans $SortieAbs (RUNTIME_VERSION=$RuntimeVersion)." -ForegroundColor Green
