# Contribuer à Jarvis AIOS

Merci de votre intérêt pour Jarvis AIOS.

## Prérequis locaux (Windows)
- Python 3.12 et `uv`
- Node.js 22+
- Windows PowerShell 5.1+ (et .NET Framework 4.x pour recompiler `Jarvis.exe` via `launcher\build.cmd`)

## Installation de l'environnement de développement
```powershell
uv venv .venv --python 3.12
uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt
cd frontend && npm ci
```

## Vérifications avant toute Pull Request
```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m pytest
cd frontend && npm run build
.venv\Scripts\python.exe distrib\scan_secrets.py .
```

## Règles de contribution
- Rédiger les docstrings et messages visibles en français.
- Respecter les contrats d'architecture (`ARCHITECTURE.md`, `ARCHITECTURE-DISTRIB.md`).
- Ne jamais inclure de clés API, jetons, adresses e-mail personnelles ou chemins locaux (`C:\Users\<nom>`).
- Les tests par défaut doivent rester hors ligne et sans appels aux moteurs IA réels (`-m "not reel"`).
