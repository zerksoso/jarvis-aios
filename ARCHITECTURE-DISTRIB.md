# ARCHITECTURE-DISTRIB — installeur Windows, données vierges, mises à jour GitHub

Contrat partagé par tous les lots de distribution. Complète `ARCHITECTURE.md` (§3 config) et `ARCHITECTURE-DOMAINES.md`.
Langue du code et des messages : français (identifiants Python existants conservés). Python 3.12, FastAPI, PySide6.

## 1. Deux dossiers : programme (APP_DIR) et données (ROOT)
- `config.APP_DIR` = dossier du code (`Path(__file__).resolve().parents[1]`), lecture seule en production. Contient `backend/`, `overlay/`, `frontend/dist/`, `scripts/`, `templates/`, `python/` (runtime embarqué, installé seulement), `VERSION`, `RUNTIME_VERSION`, `Jarvis.exe`, `installed.flag` (présent seulement si installé par l'installeur).
- `config.ROOT` = dossier des **données utilisateur** (inchangé pour tout le code existant : `runtime/`, `second-brain/`, `agents/`, `canaux/`, `aios/skills`, `aios/routines`, `aios/scripts`).
  Résolution : (1) variable `JARVIS_ROOT` si définie (tests, mode développeur) ; sinon (2) si `APP_DIR/installed.flag` existe → `%USERPROFILE%\JarvisAIOS` ; sinon (3) `APP_DIR` (mode développement actuel).
- `config.FRONT_DIST = APP_DIR / "frontend/dist"`, `config.TEMPLATES_DIR = APP_DIR / "templates"`, `config.VERSION` lu depuis `APP_DIR/VERSION` (défaut `0.0.0-dev`).
- `refresh_paths()` continue de fonctionner pour les tests (met à jour aussi `APP_DIR`-dérivés si besoin).
- Plus **aucun** chemin absolu propre à une machine dans le code (tout chemin de dossier utilisateur Windows en dur est interdit).

## 2. Modèles de données vierges (`templates/`)
Source versionnée : `distrib/templates/` (copiée telle quelle vers `APP_DIR/templates/` par le build) :
`second-brain/` (notes de méthode génériques + `00-Profil/profil-utilisateur.md` à compléter), `memories.json` (3 règles génériques de prudence, aucune donnée personnelle), `agents/*.json` (5 agents de base, `souvenirs: []`), `canaux/`, `aios/skills/`, `aios/routines/`.
`backend/bootstrap.py::ensure_data_dir()` : copie récursivement `TEMPLATES_DIR` → `ROOT` **sans jamais écraser un fichier existant** ; crée `runtime/`; appelée au démarrage (lifespan) avant l'init DB. Idempotente.
État du premier lancement : `ROOT/runtime/premier_lancement.json` = `{"termine": bool, "etape": str, "termine_le": iso|null}`.

## 3. Découverte des CLI et moteurs (autres utilisateurs)
- `backend/llm/binaries.py::trouver(nom: str) -> str | None` pour `agy`, `claude`, `codex`, `node`, `npm`, `git` : `shutil.which` puis dossiers connus Windows : `%LOCALAPPDATA%\agy\bin`, `%LOCALAPPDATA%\Programs\Antigravity\bin`, `%USERPROFILE%\.local\bin`, `%APPDATA%\npm`, `%LOCALAPPDATA%\Programs\nodejs`, `%ProgramFiles%\nodejs` (`.exe`, `.cmd`). Les moteurs utilisent ce module (plus de `shutil.which` direct).
- `backend/llm/agy_usage.py` remplace le script Node `agy-quota.mjs` (supprimer `config.AGY_QUOTA_SCRIPT`, et la dépendance à `node`) :
  exécute `agy -p /usage --output-format json` (env nettoyé des variables de clé API : `GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_GENAI_API_KEY|GOOGLE_GENERATIVE_AI_API_KEY|ANTIGRAVITY_API_KEY|ANTIGRAVITY_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_CLOUD_PROJECT|GOOGLE_GENAI_USE_VERTEXAI|VERTEX_*|*_GEMINI_KEY`, timeout 30 s, sans fenêtre console), lit `data.command.data.groups[].buckets[]` (`id`, `remaining_fraction`, `window`, `reset_time`) et renvoie le même instantané qu'avant : `{"updatedAt": iso, "buckets": {id: {"group","window","remaining","resetsAt"}}}`. Cache JSON `ROOT/runtime/agy-quota.json` (écriture atomique, TTL 600 s). `quota.lire_quotas()` utilise ce module ; sortie inchangée pour les consommateurs.
- Projet agy : le moteur utilise `--project default-cli-project`. Si agy répond que le projet n'existe pas, réessayer **sans** `--project` (jamais `--new-project`) et mémoriser le choix dans `runtime/agy-projet.json`. (Non testable ici : comportement à documenter dans le README comme « à valider sur un compte neuf ».)
- `codex_engine.py` : moteur réel `codex exec --json` (sortie JSONL, dernier message agent = réponse), `codex login status` pour la connexion, modèles mappés dans `catalog.py` sous le moteur `chatgpt` : si Codex est installé **et** connecté, `chatgpt` utilise Codex ; sinon repli sur GPT-OSS via agy (comportement actuel). Mentionné « non testé avec un vrai compte » tant qu'aucun test réel n'a eu lieu.
- Le routeur ne propose que les moteurs installés **et** connectés ; si aucun n'est utilisable, l'erreur renvoyée contient `code: "aucun_moteur"` et l'interface redirige vers l'assistant `/#/bienvenue`.

## 4. API d'assistant de premier lancement (`backend/api/setup.py`, préfixe `/api/setup`)
Tout est protégé par le jeton existant. Réponses JSON.
- `GET /api/setup/etat` → `{"termine": bool, "version": str, "moteurs": {"agy": M, "claude": M, "codex": M}, "node": {"installe": bool}, "profil_rempli": bool}` avec `M = {"installe": bool, "connecte": bool, "chemin": str|null, "version": str|null, "installation_auto": bool, "page_telechargement": str|null}`.
  Connexion : agy → `agy -p /usage --output-format json` renvoie des quotas ; claude → `claude auth status` (JSON si disponible) ; codex → `codex login status` (code retour 0).
- `POST /api/setup/installer/{moteur}` → démarre un job `{"job_id"}` : `claude` via le script officiel PowerShell `https://claude.ai/install.ps1` (téléchargé puis exécuté, sortie capturée) ; `codex` via `npm install -g @openai/codex` si `npm` existe, sinon `installation_auto=false` ; `agy` : `installation_auto=false` et `page_telechargement` (URL officielle Antigravity) — l'utilisateur l'installe lui-même. Aucune installation sans clic explicite.
- `GET /api/setup/jobs/{job_id}` → `{"statut": "en_cours|ok|erreur", "log": [str]}` (journal tronqué à 200 lignes).
- `POST /api/setup/connecter/{moteur}` → ouvre une **fenêtre de console visible** qui lance la commande de connexion officielle (`claude auth login`, `codex login`, `agy` pour sa première connexion interactive) ; renvoie `{"ok": true}` ; l'interface sonde ensuite `GET /api/setup/etat` toutes les 3 s.
- `POST /api/setup/profil` corps `{"prenom","activite","secteur","objectifs","outils","preferences"}` (tous optionnels, textes courts) → remplit `second-brain/00-Profil/profil-utilisateur.md` (sections existantes, jamais d'écrasement des autres sections) et ajoute des souvenirs globaux typés via `brain.memories.ajouter_souvenir`.
- `POST /api/setup/terminer` → `premier_lancement.json.termine = true`.

## 5. Mises à jour (`backend/update/`, API `backend/api/maj.py`, préfixe `/api/maj`)
- `backend/version.py` : `VERSION`, `DEPOT_GITHUB = "zerksoso/jarvis-aios"` (dépôt public de référence, modifiable par la variable d'environnement `JARVIS_DEPOT_GITHUB` et par `parametres.json: maj.depot`).
- Assets d'une release GitHub `vX.Y.Z` : `manifest.json`, `JarvisAIOS-app-X.Y.Z.zip` (code + frontend + templates, **sans** `python/`), `JarvisAIOS-Setup-X.Y.Z.exe` (installeur complet).
  `manifest.json` = `{"version","runtime_version","notes","app_zip":{"name","sha256","size"},"setup":{"name","sha256","size"}}`.
- Vérification : `GET https://api.github.com/repos/{depot}/releases/latest` (sans authentification, `User-Agent: JarvisAIOS-Updater`), au démarrage puis toutes les 6 h, selon `parametres.json: maj.mode` = `demander` (défaut) | `auto` | `jamais`. Comparaison semver.
- Hôtes autorisés pour tout téléchargement : `api.github.com`, `github.com`, `objects.githubusercontent.com`, `release-assets.githubusercontent.com` ; HTTPS uniquement ; taille ≤ 600 Mo ; SHA-256 du fichier **obligatoirement** égal à celui du manifest ; si le Setup porte une signature Authenticode, `Get-AuthenticodeSignature` doit être `Valid`.
- Installation (jamais sans accord utilisateur sauf `maj.mode == "auto"`) :
  - `runtime_version` identique à `APP_DIR/RUNTIME_VERSION` → mise à jour **légère** : télécharger le zip app, vérifier, l'extraire dans `APP_DIR/_maj_staging`, puis lancer `Jarvis.exe --appliquer-maj` (détaché) et arrêter le serveur. Le lanceur attend l'arrêt, renomme `backend overlay frontend scripts templates VERSION RUNTIME_VERSION` en `_ancien/`, déplace le staging en place, relance Jarvis ; si `/api/sante` ne répond pas sous 90 s, il restaure `_ancien/` et signale l'échec dans `runtime/logs/maj.log`.
  - sinon → télécharger le Setup, vérifier, lancer `Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` détaché.
- Endpoints : `GET /api/maj/etat` → `{"version","derniere":{"version","notes","legere":bool}|null,"disponible":bool,"verifie_le":iso|null,"mode":str}` ; `POST /api/maj/verifier` ; `POST /api/maj/installer` (démarre un job, progression `GET /api/maj/progression` → `{"etape","pourcent","erreur"}`) ; `POST /api/maj/mode` corps `{"mode"}`.
- Événement WebSocket `maj_disponible` `{"version","notes"}` à la détection.

## 6. Packaging (`distrib/`)
- `distrib/build_runtime.ps1` : `uv python install 3.12`, copie du Python autonome vers `build/python/`, `uv pip install --python build/python/python.exe -r requirements.txt` (runtime seulement ; `pytest`/`ruff` déplacés dans `requirements-dev.txt`), élagage (`__pycache__`, `tests/`, `*.pyi`, `pip`/`setuptools` optionnels), calcule `RUNTIME_VERSION` = SHA-256 court de (`requirements.txt` + version de Python).
- `distrib/build_release.ps1 -Version X.Y.Z` : `npm ci && npm run build` (frontend), `launcher\build.cmd` (Jarvis.exe), assemble `build/stage/` (APP_DIR installé), exécute `distrib/scan_secrets.py` sur `build/stage/` (échec bloquant), crée le zip app, compile l'installeur avec `ISCC`, calcule les SHA-256, écrit `manifest.json`, dépose tout dans `dist-release/`.
- `distrib/JarvisAIOS.iss` (Inno Setup 6) : installation **par utilisateur** (`PrivilegesRequired=lowest`, `{localappdata}\Programs\JarvisAIOS`), interface en français, `AppId` fixe, raccourcis menu Démarrer + Bureau (tâche optionnelle), démarrage avec Windows (tâche optionnelle, clé `HKCU\...\Run` vers `Jarvis.exe --silencieux`), écrit `installed.flag`, case « Lancer Jarvis » en fin d'installation, `CloseApplications=yes`, désinstallation : arrête Jarvis, supprime le programme, **conserve les données** (`%USERPROFILE%\JarvisAIOS`) sauf case cochée.
- `Jarvis.exe` (source `launcher/JarvisLauncher.cs`, C# 5) : en plus de l'existant, gère `--silencieux` (démarre sans ouvrir le navigateur), `--appliquer-maj` (§5) ; trouve `python\pythonw.exe` si installé, sinon `.venv\Scripts\`.
- `scripts/lancer.ps1` / `arreter.ps1` : utilisent `APP_DIR\python\python.exe` si présent sinon `.venv\Scripts\python.exe` ; définissent `JARVIS_ROOT` seulement en mode développement.
- `.github/workflows/ci.yml` (push/PR : ruff, pytest, `npm run build`, `scan_secrets`) et `release.yml` (tag `v*` : build complet sur `windows-latest`, Inno Setup via `choco install innosetup`, publication de la release avec les 3 assets).
- `distrib/scan_secrets.py` : refuse `AIza[0-9A-Za-z_-]{35}`, `sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{30,}`, `xox[bap]-`, les en-têtes de clés privées PEM, adresses e-mail réelles, chemins de dossiers utilisateur en dur, mots-clés d'identité personnelle (liste interne) ; liste d'exclusions explicite et courte ; code retour 1 si trouvaille.

## 7. Interface
- Route `/#/bienvenue` : assistant en 4 étapes (Moteurs IA → Connexion → Profil → Terminé). Redirection automatique depuis l'accueil tant que `termine == false` ou si le chat renvoie `code: "aucun_moteur"`.
- Pastille « Mise à jour vX.Y.Z » dans la barre du haut quand `disponible`, fenêtre avec notes et bouton « Installer » ; section « Mises à jour » dans Paramètres (mode, « Vérifier maintenant », version).

## 8. Hors périmètre / limites déclarées
- La connexion aux comptes (Google/Antigravity, Claude, ChatGPT) est toujours faite par l'utilisateur.
- Pas de signature de code incluse (avertissement SmartScreen possible) ; documentée dans le README.
- Voix : `edge-tts` utilise un service Microsoft non officiel ; le paramètre `voix.moteur = "windows"` (pyttsx3, local) doit rester disponible comme alternative.
- Pas de licence au nom de l'auteur dans le dépôt tant que l'auteur n'a pas validé (fichier `LICENSE` « contributeurs de Jarvis AIOS »).
