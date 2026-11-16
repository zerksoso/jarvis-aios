<#
.SYNOPSIS
Construit une release complète de Jarvis AIOS (zip applicatif, installeur Inno Setup et manifest.json).

.DESCRIPTION
Exécute les 9 étapes de packaging avec arrêt immédiat en cas d'erreur :
1. Vérification des prérequis et recherche d'ISCC.exe
2. Compilation du frontend (npm ci && npm run build)
3. Compilation du lanceur Windows (launcher\build.cmd -> Jarvis.exe)
4. Préparation du runtime Python embarqué (build\python)
5. Assemblage de build\stage\ (VERSION, RUNTIME_VERSION, installed.flag, code, templates)
6. Scan de secrets bloquant sur build\stage\ (distrib\scan_secrets.py)
7. Création de dist-release\JarvisAIOS-app-X.Y.Z.zip
8. Compilation de l'installeur dist-release\JarvisAIOS-Setup-X.Y.Z.exe via ISCC.exe
9. Calcul des empreintes SHA-256 et écriture de dist-release\manifest.json

.PARAMETER Version
Version semver de la release au format X.Y.Z (ex. 1.0.0).

.PARAMETER Notes
Notes de version optionnelles inscrites dans manifest.json.

.PARAMETER ReconstruireRuntime
Force la reconstruction complète de build\python même s'il existe déjà.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$Notes = '',

    [switch]$ReconstruireRuntime
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BuildDir = Join-Path $RepoRoot 'build'
$StageDir = Join-Path $BuildDir 'stage'
$PythonRuntimeDir = Join-Path $BuildDir 'python'
$DistDir = Join-Path $RepoRoot 'dist-release'
$Utf8SansBom = New-Object System.Text.UTF8Encoding($false)

function Find-IsccExe {
    $cmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }

    $candidats = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'G:\ai-cli\InnoSetup\ISCC.exe',
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($chemin in $candidats) {
        if ($chemin -and (Test-Path $chemin)) {
            return $chemin
        }
    }
    return $null
}

function Copy-ArbreFiltre {
    param(
        [string]$Source,
        [string]$Destination
    )
    $dossiersExclus = @(
        '__pycache__', 'tests', '.pytest_cache', '.ruff_cache',
        '.mypy_cache', '.agent-runs', '.git', '.venv', 'node_modules',
        'runtime', 'secrets'
    )
    $extensionsExclues = @('.pyc', '.pyo', '.db', '.db-journal')

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($item in (Get-ChildItem -LiteralPath $Source -Force)) {
        if ($item.PSIsContainer) {
            if ($dossiersExclus -contains $item.Name) {
                continue
            }
            Copy-ArbreFiltre -Source $item.FullName -Destination (Join-Path $Destination $item.Name)
        } else {
            if ($extensionsExclues -contains $item.Extension.ToLowerInvariant()) {
                continue
            }
            if ($item.Name -eq '.env' -or $item.Name -like '.env.*') {
                continue
            }
            Copy-Item -LiteralPath $item.FullName -Destination (Join-Path $Destination $item.Name) -Force
        }
    }
}

function New-AppZip {
    param(
        [string]$SourceDir,
        [string]$ZipPath
    )
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    if (Test-Path $ZipPath) {
        Remove-Item -Path $ZipPath -Force
    }

    $baseLen = $SourceDir.TrimEnd('\', '/').Length + 1
    $archive = [System.IO.Compression.ZipFile]::Open($ZipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        $fichiers = Get-ChildItem -Path $SourceDir -File -Recurse
        foreach ($f in $fichiers) {
            $rel = $f.FullName.Substring($baseLen).Replace('\', '/')
            if ($rel -eq 'installed.flag') {
                continue
            }
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive,
                $f.FullName,
                $rel,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    } finally {
        $archive.Dispose()
    }
}

Write-Host "[1/9] Vérification des prérequis et recherche d'ISCC.exe..." -ForegroundColor Cyan
$IsccExe = Find-IsccExe
if (-not $IsccExe) {
    throw "ISCC.exe (Inno Setup 6) introuvable dans le PATH, ProgramFiles(x86), LOCALAPPDATA ou G:\ai-cli\InnoSetup."
}
Write-Host "ISCC.exe trouvé : $IsccExe" -ForegroundColor Gray

$IssPath = Join-Path $PSScriptRoot 'JarvisAIOS.iss'
if (-not (Test-Path $IssPath)) {
    throw "Script Inno Setup introuvable : $IssPath"
}
$ScanScript = Join-Path $PSScriptRoot 'scan_secrets.py'
if (-not (Test-Path $ScanScript)) {
    throw "Script de scan des secrets introuvable : $ScanScript"
}
$TemplatesSrc = Join-Path $PSScriptRoot 'templates'
if (-not (Test-Path $TemplatesSrc)) {
    throw "Dossier des modèles vierges introuvable : $TemplatesSrc"
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

Write-Host '[2/9] Compilation du frontend (npm ci && npm run build)...' -ForegroundColor Cyan
$FrontendDir = Join-Path $RepoRoot 'frontend'
Push-Location $FrontendDir
try {
    & npm ci
    if ($LASTEXITCODE -ne 0) {
        throw "Échec de 'npm ci' dans frontend/ (code $LASTEXITCODE)."
    }
    & npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Échec de 'npm run build' dans frontend/ (code $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}
$FrontDistSrc = Join-Path $FrontendDir 'dist'
if (-not (Test-Path (Join-Path $FrontDistSrc 'index.html'))) {
    throw "Build frontend incomplet : index.html manquant dans $FrontDistSrc"
}

Write-Host '[3/9] Compilation du lanceur Jarvis.exe (launcher\build.cmd)...' -ForegroundColor Cyan
$BuildCmd = Join-Path $RepoRoot 'launcher\build.cmd'
Push-Location $RepoRoot
try {
    & cmd.exe /c "`"$BuildCmd`""
    if ($LASTEXITCODE -ne 0) {
        throw "Échec de la compilation du lanceur via launcher\build.cmd (code $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}
$LauncherExe = Join-Path $RepoRoot 'Jarvis.exe'
if (-not (Test-Path $LauncherExe)) {
    throw "Jarvis.exe introuvable après compilation : $LauncherExe"
}

Write-Host '[4/9] Préparation du runtime Python embarqué (build\python)...' -ForegroundColor Cyan
$EmbeddedPy = Join-Path $PythonRuntimeDir 'python.exe'
if ($ReconstruireRuntime -or -not (Test-Path $EmbeddedPy)) {
    & (Join-Path $PSScriptRoot 'build_runtime.ps1') -Sortie $PythonRuntimeDir
    if ($LASTEXITCODE -ne 0) {
        throw "Échec de build_runtime.ps1 (code $LASTEXITCODE)."
    }
} else {
    Write-Host "Runtime existant réutilisé : $PythonRuntimeDir" -ForegroundColor Gray
}

$RuntimeVersionFile = Join-Path $PythonRuntimeDir 'RUNTIME_VERSION'
if (-not (Test-Path $RuntimeVersionFile)) {
    throw "Fichier RUNTIME_VERSION absent du runtime : $RuntimeVersionFile"
}
$RuntimeVersion = ([System.IO.File]::ReadAllText($RuntimeVersionFile, [System.Text.Encoding]::UTF8)).Trim()
if (-not $RuntimeVersion) {
    throw "RUNTIME_VERSION est vide dans $RuntimeVersionFile"
}

Write-Host '[5/9] Assemblage de build\stage\...' -ForegroundColor Cyan
if (Test-Path $StageDir) {
    Remove-Item -Path $StageDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

[System.IO.File]::WriteAllText((Join-Path $StageDir 'VERSION'), $Version, $Utf8SansBom)
[System.IO.File]::WriteAllText((Join-Path $StageDir 'RUNTIME_VERSION'), $RuntimeVersion, $Utf8SansBom)
[System.IO.File]::WriteAllText((Join-Path $StageDir 'installed.flag'), "installed=$Version`n", $Utf8SansBom)

Copy-ArbreFiltre -Source (Join-Path $RepoRoot 'backend') -Destination (Join-Path $StageDir 'backend')
Copy-ArbreFiltre -Source (Join-Path $RepoRoot 'overlay') -Destination (Join-Path $StageDir 'overlay')
Copy-ArbreFiltre -Source (Join-Path $RepoRoot 'scripts') -Destination (Join-Path $StageDir 'scripts')
Copy-ArbreFiltre -Source $FrontDistSrc -Destination (Join-Path $StageDir 'frontend\dist')
Copy-ArbreFiltre -Source $TemplatesSrc -Destination (Join-Path $StageDir 'templates')

Get-ChildItem -Path $RepoRoot -Filter 'ARCHITECTURE*.md' -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $StageDir $_.Name) -Force
}
foreach ($docFichier in @('README.md', 'LICENSE', 'setup_voice.py')) {
    $srcDoc = Join-Path $RepoRoot $docFichier
    if (-not (Test-Path $srcDoc)) {
        throw "Fichier requis manquant à la racine : $srcDoc"
    }
    Copy-Item -LiteralPath $srcDoc -Destination (Join-Path $StageDir $docFichier) -Force
}

Write-Host '[6/9] Exécution du scan de secrets sur build\stage\...' -ForegroundColor Cyan
& $EmbeddedPy $ScanScript $StageDir
if ($LASTEXITCODE -ne 0) {
    throw "Le scan de secrets a échoué sur build\stage\ (code $LASTEXITCODE). Release annulée."
}

Write-Host "[7/9] Création de l'archive applicative JarvisAIOS-app-$Version.zip..." -ForegroundColor Cyan
$AppZipName = "JarvisAIOS-app-$Version.zip"
$AppZipPath = Join-Path $DistDir $AppZipName
New-AppZip -SourceDir $StageDir -ZipPath $AppZipPath
if (-not (Test-Path $AppZipPath)) {
    throw "Échec de création de l'archive : $AppZipPath"
}

Write-Host "[8/9] Compilation de l'installeur Inno Setup (JarvisAIOS-Setup-$Version.exe)..." -ForegroundColor Cyan
$SetupName = "JarvisAIOS-Setup-$Version.exe"
$SetupPath = Join-Path $DistDir $SetupName
if (Test-Path $SetupPath) {
    Remove-Item -Path $SetupPath -Force
}
& $IsccExe "/DAppVersion=$Version" "/O$DistDir" $IssPath
if ($LASTEXITCODE -ne 0) {
    throw "Échec de la compilation Inno Setup (code $LASTEXITCODE)."
}
if (-not (Test-Path $SetupPath)) {
    throw "Installeur attendu introuvable après compilation : $SetupPath"
}

Write-Host '[9/9] Calcul des empreintes SHA-256 et écriture de manifest.json...' -ForegroundColor Cyan
$AppHash = (Get-FileHash -Path $AppZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$AppSize = (Get-Item -LiteralPath $AppZipPath).Length
$SetupHash = (Get-FileHash -Path $SetupPath -Algorithm SHA256).Hash.ToLowerInvariant()
$SetupSize = (Get-Item -LiteralPath $SetupPath).Length

$NotesFinales = if ($Notes) { $Notes } else { "Version $Version" }
$Manifest = [ordered]@{
    version         = $Version
    runtime_version = $RuntimeVersion
    notes           = $NotesFinales
    app_zip         = [ordered]@{
        name   = $AppZipName
        sha256 = $AppHash
        size   = [int64]$AppSize
    }
    setup           = [ordered]@{
        name   = $SetupName
        sha256 = $SetupHash
        size   = [int64]$SetupSize
    }
}

$ManifestJson = $Manifest | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText((Join-Path $DistDir 'manifest.json'), $ManifestJson, $Utf8SansBom)

Write-Host "Release $Version prête dans $DistDir :" -ForegroundColor Green
Write-Host "  - $AppZipName ($AppSize octets, sha256=$AppHash)" -ForegroundColor Gray
Write-Host "  - $SetupName ($SetupSize octets, sha256=$SetupHash)" -ForegroundColor Gray
Write-Host "  - manifest.json" -ForegroundColor Gray
