# Jarvis AIOS — Architecture et contrats (référence pour tous les lots)

Tout le code DOIT respecter ce document. Textes visibles en français. Python 3.12 (venv `.venv`), lancé depuis la racine du projet : `.venv\Scripts\python.exe -m backend.main`.

## 1. Résultats des bancs d'essai (lot 1, 2026-11-16)
- `agy` 1.5.9 : `agy --project default-cli-project --add-dir <sandbox> --sandbox --disable-slash-commands --model <id> --output-format stream-json --print-timeout <n>s --print "<prompt>"` fonctionne.
  - stream-json : une ligne JSON par événement. `{"event":"init","conversation_id":…}`, `{"event":"step_update","step_update":{"step_type":"agent_response","state":"ACTIVE"|"DONE","text_delta":"…"}}`, `{"event":"result","result":{"conversation_id","status":"SUCCESS"|"ERROR","response","error","usage":{input_tokens,output_tokens,…}}}`.
  - `--conversation <id>` reprend une conversation (mémoire OK). Pas d'option « prompt système » : le système est placé en tête du 1er message.
  - `--json-schema <fichier>` marche (`result.structured_output`) mais coûte un 2e tour → NON utilisé ; on demande du JSON dans le prompt et on parse de façon tolérante.
  - Images : copier l'image dans le dossier sandbox et demander « ouvre le fichier X.png » → agy la lit (coordonnées approximatives seulement).
  - Coût fixe ≈ 20 000 tokens d'entrée par appel (outils/plugins d'agy, non réductible sans toucher la config globale de l'utilisateur — interdit).
  - Erreur passagère possible : `Eligibility check failed: unexpected EOF` → relancer (2 fois, attente 3 s puis 8 s).
  - Variables à retirer de l'environnement enfant : regex `^(GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_GENAI_API_KEY|GOOGLE_GENERATIVE_AI_API_KEY|ANTIGRAVITY_API_KEY|ANTIGRAVITY_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|GOOGLE_CLOUD_PROJECT|GOOGLE_GENAI_USE_VERTEXAI|VERTEX_.*|.*_GEMINI_KEY)$` (insensible à la casse). Jamais `--new-project`, jamais `--dangerously-skip-permissions`.
- Quota : `agy -p /usage --output-format json` (lu par `backend/llm/agy_usage.py`, cache 600 s) → `{"updatedAt", "buckets": {"gemini-5h"|"gemini-weekly"|"3p-5h"|"3p-weekly"|"gemini-argon-wtu-weekly": {"group","window","remaining": 0..1,"resetsAt"}}}`. Groupes : Hydrogen+Flash = `gemini-*` ; Claude+GPT-OSS via agy = `3p-*` ; Argon = `gemini-argon-wtu-weekly`.
- Claude Code CLI (`claude` (découvert par `backend/llm/binaries.py`)) : `claude -p --model sonnet|opus|haiku --effort low|medium|high --setting-sources project --strict-mcp-config --tools "" --output-format stream-json --verbose --include-partial-messages --system-prompt "<s>" [--resume <session_id>] "<prompt>"`, lancé depuis `runtime/claude-sandbox`, en retirant de l'environnement `CLAUDE_CODE_*`, `ANTHROPIC_*`, `CLAUDECODE`. **État actuel : connexion OAuth expirée** (« Failed to authenticate: OAuth session expired ») → le moteur doit détecter ce message, se marquer `indisponible` avec la raison « Lance `claude` puis /login une fois », et passer au suivant.
- `codex` : absent. Slot « ChatGPT » = `gpt-oss-120b-medium` via agy.

## 2. Arborescence
```
backend/  __init__.py main.py config.py db.py security.py ws_hub.py settings_store.py logging_setup.py
backend/llm/  __init__.py base.py agy_engine.py claude_engine.py codex_engine.py quota.py router.py catalog.py jsonparse.py
backend/api/  __init__.py (un module par domaine : sante.py moteurs.py chat.py cerveau.py memoires.py skills.py routines.py taches.py agents.py canaux.py approbations.py jarvis.py voix.py cartes.py parametres.py)
backend/brain/ backend/aios/ backend/agents/ backend/jarvis_engine/ backend/voice/
backend/tests/  (pytest ; conftest.py crée un ROOT temporaire via la variable JARVIS_ROOT)
overlay/  frontend/  second-brain/  aios/{skills,routines,scripts}  agents/  canaux/  runtime/
```

## 3. Configuration (`backend/config.py`)
- `ROOT = Path(os.environ.get("JARVIS_ROOT") or Path(__file__).resolve().parents[1])`
- `HOST="127.0.0.1"`, `PORT=5230`, `RUNTIME=ROOT/"runtime"`, `DB_PATH=RUNTIME/"jarvis.db"`, `BRAIN_DIR=ROOT/"second-brain"`, `SKILLS_DIR=ROOT/"aios/skills"`, `ROUTINES_DIR=ROOT/"aios/routines"`, `SCRIPTS_DIR=ROOT/"aios/scripts"`, `AGENTS_DIR=ROOT/"agents"`, `CANAUX_DIR=ROOT/"canaux"`, `FRONT_DIST=ROOT/"frontend/dist"`, `AGY_SANDBOX=RUNTIME/"agy-sandbox"`, `CLAUDE_SANDBOX=RUNTIME/"claude-sandbox"`, `LOG_DIR=RUNTIME/"logs"`, `AGY_QUOTA_SCRIPT` (chemin ci-dessus).
- `ensure_dirs()` crée tous les dossiers.

## 4. Sécurité (`backend/security.py`)
- Jeton : `RUNTIME/token.txt`, `secrets.token_urlsafe(32)` créé au 1er lancement.
- Middleware : refuse (403) toute requête dont l'en-tête `Host` n'est pas `127.0.0.1:5230` ou `localhost:5230` (anti DNS-rebinding ; en test, `testserver` est accepté si `JARVIS_TEST=1`).
- Toute méthode ≠ GET/HEAD/OPTIONS sur `/api/*` exige l'en-tête `X-Jarvis-Token` = jeton, et si l'en-tête `Origin` est présent il doit être `http://127.0.0.1:5230` ou `http://localhost:5230` (ou `http://127.0.0.1:5231` / `http://localhost:5231` = serveur Vite de dev).
- GET sensibles (captures d'écran, contenu de notes) exigent aussi le jeton (`/api/ecran/*`, `/api/cerveau/note`).
- WebSocket `/ws?token=…&client=ui|overlay` : jeton obligatoire, Origin vérifié s'il est présent.
- Le frontend reçoit le jeton : `index.html` est servi par le backend avec le texte `__JARVIS_TOKEN__` remplacé par le jeton (balise `<meta name="jarvis-token" content="__JARVIS_TOKEN__">`). Les autres fichiers de `frontend/dist` sont servis tels quels ; toute route non-API inconnue renvoie `index.html` (SPA, routes en hash).
- Comparaison du jeton avec `secrets.compare_digest`.

## 5. Base SQLite (`backend/db.py`)
Connexion par appel (`sqlite3.connect(DB_PATH, check_same_thread=False)`, `row_factory=sqlite3.Row`, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`), verrou `threading.Lock` pour les écritures, fonctions utilitaires `execute(sql, params)`, `query(sql, params) -> list[dict]`, `query_one`. Appelé depuis l'async via `asyncio.to_thread` quand c'est long. `init_db()` crée :
```
conversations(id TEXT PK, type TEXT CHECK(type IN ('agent','canal')), cible_id TEXT, titre TEXT, cree_le TEXT)
messages(id INTEGER PK AUTOINCREMENT, conversation_id TEXT, role TEXT, auteur TEXT, contenu TEXT, meta TEXT DEFAULT '{}', cree_le TEXT)  + index(conversation_id,id)
sessions_moteur(cle TEXT, modele TEXT, session_id TEXT, maj_le TEXT, PRIMARY KEY(cle, modele))
usage_llm(id INTEGER PK, ts TEXT, moteur TEXT, modele TEXT, input_tokens INT, output_tokens INT, duree REAL, ok INT, erreur TEXT)
runs_routines(id INTEGER PK, routine_id TEXT, debut TEXT, fin TEXT, statut TEXT, sortie_path TEXT, resume TEXT, erreur TEXT)
approbations(id TEXT PK, source TEXT, action TEXT, resume TEXT, risque TEXT, statut TEXT, cree_le TEXT, decide_le TEXT)
taches(id INTEGER PK, titre TEXT, fait INT DEFAULT 0, date TEXT, source TEXT, ordre INT DEFAULT 0)
cartes(id TEXT PK, titre TEXT, contenu_md TEXT, type TEXT, epinglee INT DEFAULT 0, cree_le TEXT)
```
Dates : ISO 8601 locales `datetime.now().isoformat(timespec="seconds")`.

## 6. WebSocket (`backend/ws_hub.py`)
`hub = Hub()` singleton : `connect(ws, client)`, `disconnect`, `async broadcast(type, data, cible="tous"|"ui"|"overlay")`, `broadcast_threadsafe(type, data, cible)` (depuis un thread, via la boucle mémorisée au démarrage). Enveloppe : `{"type": str, "data": {...}, "ts": iso}`.
Types serveur → clients :
`etat_jarvis{etat: repos|ecoute|reflexion|parole|action}` · `agent_etat{id, etat: libre|travaille|attend_accord|fini|erreur}` · `chat_debut{conversation_id, auteur}` · `chat_delta{conversation_id, auteur, delta}` · `chat_fin{conversation_id, auteur, message_id, contenu, moteur, modele}` · `chat_erreur{conversation_id, auteur, erreur}` · `narration{texte, niveau}` · `guide{id, boites:[{x,y,w,h,label,couleur}], attendre_clic: bool}` · `guide_effacer{id?}` · `carte{id,titre,contenu_md,type}` · `approbation_demande{id, resume, risque, action}` · `approbation_resultat{id, statut}` · `routine_etat{id, statut, run_id}` · `quotas{…}` · `cerveau_maj{}` · `taches_maj{}` · `toast{texte, niveau}` · `overlay_statut{connecte: bool}` · `voix_parler{url, texte}` (l'overlay joue l'audio).
Types clients → serveur : `ping` (réponse `pong`), `overlay_hello`, `ecoute{action: debut|fin}`, `guide_clic{id, index}`, `approbation_reponse{id, decision: oui|non}`, `carte_epingler{id}`, `chat{…mêmes champs que POST /api/chat}`.
Le hub publie `overlay_statut` à chaque connexion/déconnexion d'un client overlay et l'expose `hub.overlay_connecte`.

## 7. Moteurs LLM (`backend/llm/`)
```python
# base.py
Effort = Literal["low","medium","high","xhigh"]
class LLMRequest(BaseModel):
    prompt: str                      # message utilisateur (ou consigne)
    systeme: str = ""                # persona + règles + outils ; envoyé au 1er tour de session (agy) ou via --system-prompt (claude)
    moteur: Literal["auto","gemini","claude","chatgpt"] = "auto"
    modele: str | None = None        # id du catalogue (voir catalog.py) ; None = défaut du moteur
    effort: Effort = "medium"
    session_cle: str | None = None   # clé de continuité (ex. "agent:jarvis", "canal:general:atlas") ; None = sans mémoire
    images: list[str] = []           # chemins absolus d'images à joindre
    timeout_s: int = 180
class LLMResult(BaseModel):
    texte: str; moteur: str; modele: str; session_id: str | None
    input_tokens: int = 0; output_tokens: int = 0; duree_s: float = 0
    bascules: list[str] = []         # ex. ["hydrogen: quota < 10 %", "flash: Eligibility check failed"]
class EngineError(Exception): transitoire: bool; message: str
DeltaCallback = Callable[[str], Awaitable[None]] | None
class Engine(Protocol):
    nom: str
    async def disponible(self) -> tuple[bool, str]
    async def generer(self, req: LLMRequest, modele_id: str, session_id: str | None, on_delta: DeltaCallback) -> LLMResult
```
- `catalog.py` : catalogue des modèles exposés à l'UI. Chaque entrée : `id`, `libelle`, `moteur_ui` (gemini|claude|chatgpt), `backend` (agy|claude_cli), `groupe_quota` (gemini|3p|argon|claude_cli), `agy_ids` par effort.
  - `hydrogen` « Gemini 4 Hydrogen » : low→`gemini-4-hydrogen-low`, medium→`-medium`, high→`-high`, xhigh→`-xhigh` (groupe gemini) — **défaut global**.
  - `flash` « Gemini 3.8 Flash » : `gemini-3.8-flash-{low,medium,high}` (xhigh→high).
  - `argon` « Gemini 4 Argon (réserve) » : `gemini-4-argon-{low,medium,high}` (groupe argon).
  - `claude-sonnet` « Claude Sonnet 5.5 (Antigravity) » : `claude-sonnet-5-5-{low,medium,high}` (groupe 3p) ; `claude-opus` idem `claude-opus-5-5-*`.
  - `gpt-oss` « GPT-OSS 120B (Antigravity) » : `gpt-oss-120b-medium` pour tous les efforts (groupe 3p).
  - `cli-sonnet`, `cli-opus`, `cli-haiku` « Claude Code CLI » : backend claude_cli, `--model sonnet|opus|haiku`, effort xhigh→high.
- `router.py` : `async def generer(req, on_delta=None) -> LLMResult`. Chaînes de repli :
  - auto / gemini : [modèle choisi ou hydrogen] → flash → claude-sonnet → argon → cli-sonnet
  - claude : [choisi ou claude-sonnet] → claude-opus → cli-sonnet → hydrogen
  - chatgpt : gpt-oss → hydrogen → claude-sonnet → cli-sonnet
  (dédupliquer ; le modèle choisi est toujours tenté en premier).
  - Un maillon est sauté si : son groupe de quota < 10 % (`quota.py`, cache 10 min ; Argon sauté sous 10 % même s'il est choisi), ou le moteur est indisponible (`disponible()`), ou il a échoué. Raison ajoutée à `bascules`.
  - `EngineError(transitoire=True)` → relance du même maillon jusqu'à 2 fois (attente 3 s puis 8 s), ensuite maillon suivant.
  - Sémaphores : 2 appels agy simultanés max, 1 appel claude CLI.
  - Continuité : si `session_cle`, on lit `sessions_moteur(cle, modele_catalogue)` pour l'id de session ; après succès on l'enregistre. Si la session reprise échoue (id inconnu), on relance une fois sans session.
  - Chaque appel (réussi ou non) est enregistré dans `usage_llm`.
- `agy_engine.py` : processus lancé avec `subprocess.Popen` dans un thread (`asyncio.to_thread`), lecture ligne à ligne de stdout (stream-json), deltas renvoyés à la boucle par `loop.call_soon_threadsafe` + `asyncio.Queue`. Cwd = `AGY_SANDBOX`. Arguments : voir §1 ; `--conversation <id>` si session. 1er tour : prompt = `"<<SYSTEME>>\n{systeme}\n<</SYSTEME>>\n\n{prompt}"` ; tours suivants : prompt seul. Images : copiées dans `AGY_SANDBOX/img/<uuid>.png`, ajout au prompt « Fichiers image joints (ouvre-les et analyse-les) : img/… ». Timeout → kill + `EngineError(transitoire=True)`. `status:"ERROR"` → `EngineError(transitoire = "Eligibility" in erreur or "EOF" in erreur or "UNAVAILABLE" in erreur or "deadline" in erreur.lower())`. Journal stderr dans `LOG_DIR/agy-last.log` ; si le texte contient `generativelanguage.googleapis.com`, `aiplatform.googleapis.com`, `x-goog-api-key` ou `AIza…` → `EngineError` non transitoire « facturation API détectée » + broadcast `toast` niveau erreur, et moteur agy désactivé jusqu'au redémarrage. `disponible()` = `shutil.which("agy")`. `CREATE_NO_WINDOW` sur Windows.
- `claude_engine.py` : même mécanique, `claude.cmd` (via `shutil.which("claude")`), cwd `CLAUDE_SANDBOX`, env nettoyé (§1). stream-json : lignes `{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":…}}}` → deltas ; ligne finale `{"type":"result","result":…,"session_id":…,"is_error":…,"usage":{…}}`. Message « OAuth »/« authenticate » → moteur marqué indisponible 30 min avec raison « Connexion Claude Code expirée : lance `claude` puis /login ». Images : passer `--add-dir` du dossier image + mention du chemin dans le prompt et `--tools Read` dans ce cas seulement.
- `codex_engine.py` : `disponible()` = `shutil.which("codex")` ; si absent → (False, "Codex CLI non installé"). `generer` = `codex exec --json "<prompt>"` best effort (non utilisé par défaut).
- `quota.py` : `async def lire_quotas() -> dict` (lance le script node, cache 10 min, en cas d'échec renvoie le dernier cache ou `{}`), `restant(groupe) -> float | None`, plus estimation Claude CLI = somme des tokens `usage_llm` du moteur claude_cli sur 5 h glissantes. Diffusion `quotas` au client toutes les 10 min.
- `jsonparse.py` : `extraire_json(texte) -> dict | None` tolérant (bloc ```json, premier objet `{…}` équilibré, virgules finales).

## 8. API REST (préfixe `/api`, JSON, erreurs `{"detail": "..."}`)
- `GET /sante` → `{ok, version, overlay_connecte, moteurs:{agy:bool, claude_cli:{ok, raison}, codex:bool}}`
- `GET /moteurs` → catalogue + disponibilité ; `GET /quotas` → quotas agy + estimation CLI
- `POST /chat` `{cible:"agent"|"canal", id, message, moteur?, modele?, effort?}` → `{conversation_id}` immédiatement ; le travail se fait en tâche de fond et streame en WS (`chat_debut/delta/fin`).
- `GET /conversations/{type}/{cible_id}/messages?limite=100`
- Les autres domaines (cerveau, mémoires, skills, routines, tâches, agents, canaux, approbations, jarvis, voix, cartes, paramètres) sont définis dans leurs lots respectifs mais montés par `main.py` via `include_router` d'un module `backend/api/<domaine>.py` exposant `router = APIRouter(prefix="/<domaine>")`.

## 9. Paramètres (`backend/settings_store.py`)
`RUNTIME/parametres.json` (créé avec les défauts, fusion profonde) : `{"moteur":"auto","modele":"hydrogen","effort":"medium","verbosite":"normal"|"concis"|"detaille","accent":"#22d3ee","voix":{"active":true,"nom":"fr-FR-DeniseNeural"},"raccourci":"ctrl+space","memoire_auto":true}`. `lire()`, `ecrire(partiel)`.

## 10. Conventions
- Chaque fonction a une docstring en français (1–3 lignes). Typage partout. `ruff check` propre (config dans `pyproject.toml`).
- Aucun secret en dur. Aucune écriture hors de `ROOT` sauf ce qui est explicitement prévu.
- Logs : `logging` vers `LOG_DIR/jarvis.log` (rotation 5 × 2 Mo) + console.
- Tests : `pytest` depuis la racine, `backend/tests/`, sans réseau ni vrai moteur (moteurs simulés) ; les tests marqués `@pytest.mark.reel` appellent les vrais moteurs et sont exclus par défaut (`-m "not reel"`).
