<#
.SYNOPSIS
Installe la voix locale naturelle (Kyutai TTS) et le modèle de réponses rapides (LM Studio).

.DESCRIPTION
Crée un venv dédié (torch CUDA 12.8 + moshi) dans -Dossier (défaut G:\ai-cli\kyutai-tts, sinon
%USERPROFILE%\JarvisAIOS\kyutai-tts si G: est absent), puis télécharge Qwen3 4B Instruct (Q4, 2,5 Go)
via la CLI LM Studio si elle est présente. Nécessite : uv, une carte NVIDIA (>= 6 Go libres).
Poids du modèle Kyutai : CC-BY 4.0, téléchargés au premier lancement du serveur.
#>
[CmdletBinding()]
param(
    [string]$Dossier = $(if (Test-Path 'G:\') { 'G:\ai-cli\kyutai-tts' } else { Join-Path $env:USERPROFILE 'JarvisAIOS\kyutai-tts' }),
    [switch]$SansLLM
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "L'outil 'uv' est introuvable (https://docs.astral.sh/uv/)." }

Write-Host "[1/3] Venv Python 3.12 dans $Dossier" -ForegroundColor Cyan
uv venv $Dossier --python 3.12
$py = Join-Path $Dossier 'Scripts\python.exe'

Write-Host '[2/3] torch CUDA 12.8 puis moshi (le ré-épinglage de torch évite la version CPU)' -ForegroundColor Cyan
uv pip install --python $py 'torch==2.7.1' --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $py 'moshi==0.2.11' sphn fastapi uvicorn numpy
uv pip install --python $py 'torch==2.7.1+cu128' --index-url https://download.pytorch.org/whl/cu128 --reinstall-package torch
& $py -c "import torch; assert torch.cuda.is_available(), 'CUDA indisponible'; print('GPU :', torch.cuda.get_device_name(0))"

if (-not $SansLLM) {
    $lms = Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe'
    if (Test-Path $lms) {
        Write-Host '[3/3] Modèle de réponses rapides (Qwen3 4B Instruct 2507, Q4)' -ForegroundColor Cyan
        & $lms get qwen/qwen3-4b-2507 --gguf --yes
    } else {
        Write-Warning 'LM Studio introuvable : installe-le (lmstudio.ai) puis télécharge un modèle 4-8B pour la voie rapide locale.'
    }
}
Write-Host "Terminé. Dans Jarvis > Paramètres > Voix : choisis « Kyutai » et « Modèle local ». Dossier : $Dossier" -ForegroundColor Green
if ($Dossier -ne 'G:\ai-cli\kyutai-tts') { Write-Host "Définis JARVIS_KYUTAI_DIR=$Dossier pour que Jarvis le trouve." -ForegroundColor Yellow }
