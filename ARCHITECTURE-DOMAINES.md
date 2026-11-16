# Jarvis AIOS — Contrats des domaines (suite de ARCHITECTURE.md, §11)

## 11.1 Second cerveau (`backend/brain/`, `backend/api/cerveau.py`, `backend/api/memoires.py`)
- Note = fichier `.md` sous `BRAIN_DIR` (sous-dossiers = branches : `00-Profil`, `01-Business-Projets`, `02-Connaissances-Methodes`, `03-Outils-Workflows`, `04-Analytics-Resultats`, `05-Erreurs-Lecons`). Frontmatter YAML : `titre` (sinon nom de fichier), `tags: []`, `branche` (sinon dossier de 1er niveau), `statut: actif|archive|brouillon`, `date: AAAA-MM-JJ`. Id d'une note = chemin relatif POSIX sans `.md` (ex. `02-Connaissances-Methodes/methode-prospection`). `SOUVENIRS.md` et les fichiers hors branches sont indexés avec la branche `autres`.
- Liens `[[Cible]]`, `[[Cible|alias]]`, `[[Cible#titre]]` résolus par nom de fichier (insensible casse/accents) puis par titre ; non résolus → `liens_casses`.
- Index : table FTS5 `notes_fts(id UNINDEXED, titre, contenu, tags, tokenize="unicode61 remove_diacritics 2")` + table `notes(id PK, titre, branche, tags JSON, statut, date, mtime, taille, liens JSON)`. Réindexation complète au démarrage, puis incrémentale via watchdog (debounce 500 ms) → broadcast `cerveau_maj`.
- `GET /api/cerveau/graphe` → `{"branches":[{"id","libelle","couleur","nb"}], "noeuds":[{"id","titre","branche","tags","poids","actif","date"}], "liens":[{"source","cible"}], "liens_casses":[…]}`. Couleurs : 00 `#f59e0b`, 01 `#22d3ee`, 02 `#a78bfa`, 03 `#34d399`, 04 `#f472b6`, 05 `#f87171`, autres `#94a3b8`. Libellés : Profil, Business & Projets, Connaissances & Méthodes, Outils & Workflows, Analytics & Résultats, Erreurs & Leçons, Autres. `poids` = 1 + liens entrants + liens sortants. `actif` = statut `actif` ET (modifiée ou datée il y a moins de 30 jours).
- `GET /api/cerveau/recherche?q=&limite=20` → `[{"id","titre","branche","extrait"}]` (FTS5 `snippet` avec `<mark>`…`</mark>` ; requête nettoyée, préfixe `mot*` pour la frappe instantanée). `GET /api/cerveau/note?id=` (jeton requis) → `{"id","titre","branche","tags","statut","date","contenu_md","liens_sortants","liens_entrants","chemin_absolu"}`. `PUT /api/cerveau/note {id, contenu_md}` (crée ou remplace ; dossiers créés). `POST /api/cerveau/ouvrir {id}` → `os.startfile("obsidian://open?vault=second-brain&file=<id encodé URL>")`, repli `os.startfile(fichier)`.
- Sécurité : `chemin_sur(id) -> Path` résout sous `BRAIN_DIR` et refuse ce qui sort (`..`, absolu, lecteur) → `ValueError` → 400.
- Souvenirs : `second-brain/memories.json` = `[{"id","type": "preference"|"correction"|"regle"|"fait","texte","source","agent","cree_le"}]` ; `second-brain/SOUVENIRS.md` régénéré à chaque modification (groupé par type, frontmatter `tags:[souvenirs]`). API `GET /api/memoires`, `POST /api/memoires {type,texte,source?,agent?}`, `DELETE /api/memoires/{id}`. `ajouter_souvenir(...)` dédupliqué (texte normalisé identique → ignoré).
- Extraction automatique : `backend/brain/memories.py::extraire_souvenirs(conversation_id)` appelée tous les 6 messages utilisateur d'une conversation si `memoire_auto` : `router.generer` (modele `hydrogen`, effort `low`, sans session) sur les 12 derniers messages ; réponse JSON `{"souvenirs":[{"type","texte"}]}` ; ajout dédupliqué ; ne bloque jamais la réponse (tâche de fond, erreurs journalisées).

## 11.2 Skills (`backend/aios/skills_engine.py`, `backend/api/skills.py`)
- `aios/skills/<nom>/SKILL.md` : frontmatter `nom`, `description`, `declencheurs: [mots-clés]`, `outils: [noms d'outils]`, `agent_par_defaut`, puis corps Markdown avec `## Étapes` (liste numérotée).
- `lister_skills()` (nom, description, declencheurs, outils, nb_etapes), `charger_skill(nom)` (+ `contenu_md`, `etapes: [str]`), `trouver_skills(texte) -> list[str]` (déclencheurs présents, insensible casse/accents), `creer_skill(nom, description, declencheurs, outils, etapes, corps_md=None, ecraser=False) -> str` (slug sûr).
- API : `GET /api/skills`, `GET /api/skills/{nom}`, `POST /api/skills`, `PUT /api/skills/{nom}`.

## 11.3 Outils (`backend/aios/tools_registry.py`)
```python
@dataclass
class Outil:
    nom: str; description: str; parametres: dict[str, str]   # {nom_param: "type — description"}
    risque: Literal["lecture","ecriture","action_pc","irreversible"]
    fonction: Callable[..., Awaitable[Any]]                  # async def f(ctx: ContexteOutil, **args)
registre: dict[str, Outil]
def outil(nom, description, parametres, risque): ...          # décorateur d'enregistrement
@dataclass
class ContexteOutil:
    agent_id: str; conversation_id: str | None; acces_cerveau: bool
    demander_accord: Callable[[str, dict, str], Awaitable[bool]]   # (resume, action, risque)
async def executer(nom: str, args: dict, ctx: ContexteOutil) -> dict   # {"ok": bool, "resultat"|"erreur": …, "duree": s}
def description_pour_prompt(noms: list[str]) -> str
```
Tout outil `irreversible` appelle `ctx.demander_accord` avant d'agir ; refus → `{"ok": False, "erreur": "Refusé par l'utilisateur"}`. Résultats tronqués à 6 000 caractères.
Outils du socle (module `backend/aios/tools/` un fichier par famille, importés par `tools_registry.charger_outils()`) : `cerveau.chercher(q)`, `cerveau.lire(id)`, `cerveau.ecrire(id, contenu_md)` (refus si pas `acces_cerveau`), `memoriser(type, texte)`, `web.chercher(q, n=5)` (ddgs), `web.lire(url)` (httpx 15 s, HTML → texte brut, 8 000 caractères), `taches.lister()`, `taches.ajouter(titre)`, `taches.cocher(id)`, `carte.afficher(titre, contenu_md)` (crée une carte + broadcast `carte`), `script.executer(nom, args=[])` (fichiers de `SCRIPTS_DIR` uniquement ; `.py` via le python du venv, `.ps1` via `powershell -NoProfile -ExecutionPolicy Bypass -File` ; 60 s ; sortie 4 000 car. ; risque `irreversible`), `skill.creer(nom, description, declencheurs, outils, etapes)`, `agent.creer(description)` (lot agents). Les outils écran/PC sont ajoutés par le lot Jarvis engine.

## 11.4 Routines (`backend/aios/scheduler.py`, `backend/api/routines.py`)
- `aios/routines/<id>.md` : frontmatter `id`, `nom`, `cron` (5 champs, ex. `0 6 * * *`), `agent`, `skill` (optionnel), `sortie` (chemin relatif au cerveau, `{date}` remplacé par AAAA-MM-JJ), `actif`, `description` ; corps = consigne envoyée à l'agent.
- APScheduler 3 `AsyncIOScheduler(timezone="Europe/Paris")`, `CronTrigger.from_crontab(cron, timezone=...)`, `coalesce=True`, `misfire_grace_time=6*3600`, `max_instances=1`. Au démarrage : si une routine quotidienne n'a pas de run réussi aujourd'hui et que son heure est passée → rattrapage (une fois). Rechargement à chaque création/modification.
- Exécution `async lancer_routine(id) -> run_id` : run en base (en_cours → ok|erreur) → `backend.agents.agent_runtime.executer_tache(agent_id, consigne, skill)` (import tardif ; si le module n'existe pas encore, appel direct `router.generer`) → écriture de la sortie (frontmatter `titre`, `tags:[routine, <id>]`, `branche` = dossier, `statut: actif`, `date`) → résumé 200 car. → broadcast `routine_etat`. Pour `brief-du-matin` : les lignes `- [ ] …` de la section `## Tâches du jour` deviennent des tâches (source `brief`, date du jour, sans doublon) → broadcast `taches_maj`.
- API : `GET /api/routines` (+ `prochaine_execution`, `dernier_run`), `GET /api/routines/{id}`, `POST /api/routines`, `PUT /api/routines/{id}`, `DELETE /api/routines/{id}` (send2trash), `POST /api/routines/{id}/lancer` → `{run_id}`, `GET /api/routines/{id}/historique?limite=20` (avec contenu de la sortie).

## 11.5 Tâches et cartes (`backend/api/taches.py`, `backend/api/cartes.py`)
- `GET /api/taches?date=AAAA-MM-JJ` (défaut aujourd'hui), `POST /api/taches {titre, date?}`, `PATCH /api/taches/{id} {fait?, titre?, ordre?}`, `DELETE /api/taches/{id}` ; broadcast `taches_maj`.
- `GET /api/cartes?epinglees=1`, `POST /api/cartes {titre, contenu_md, type}`, `POST /api/cartes/{id}/epingler {epinglee}`, `DELETE /api/cartes/{id}`.

## 11.6 Approbations (`backend/jarvis_engine/approvals.py`, `backend/api/approbations.py`)
`async def demander_accord(source, resume, action: dict, risque, timeout_s=300) -> bool` : ligne `approbations` (en_attente), broadcast `approbation_demande`, agent source → `attend_accord`, attente (asyncio.Event) de la décision via WS `approbation_reponse` ou `POST /api/approbations/{id} {decision:"oui"|"non"}` ; délai dépassé → `expiree`, False. `GET /api/approbations?statut=en_attente`.

## 11.7 Agents (`agents/<id>.json`, `backend/agents/`, `backend/api/agents.py`)
```json
{"id":"jarvis","nom":"Jarvis","titre":"Coordinateur","avatar":{"forme":"sphere","couleur":"#22d3ee","yeux":"ronds","etat":"libre"},
 "instructions":"…","modele_principal":"hydrogen","effort_raisonnement":"medium","modele_secours":"claude-sonnet",
 "souvenirs":[],"skills_assignes":["brief-du-matin"],"acces_second_cerveau":true,"outils":["cerveau.chercher","web.chercher"],
 "fichiers_generes":[],"routines":["brief-du-matin"],"cree_le":"2026-11-16T09:00:00"}
```
`avatar.forme` ∈ sphere|cube|diamant|anneau|goutte ; `avatar.yeux` ∈ ronds|fentes|points|visiere ; `avatar.etat` ∈ libre|travaille|attend_accord|fini|erreur (état temps réel, tenu en mémoire et diffusé par `agent_etat`).
- `agent_runtime.py` : `charger_agent(id)`, `lister_agents()`, `enregistrer_agent(dict)` (pydantic), `etat_agent(id)`, `async repondre(agent_id, conversation_id, message, moteur=None, modele=None, effort=None, on_delta=None, historique_canal=None) -> str`, `async executer_tache(agent_id, consigne, skill=None) -> str`.
- Boucle : prompt système = persona + règles absolues §11.8 + verbosité (paramètres) + souvenirs (ceux de l'agent + 20 souvenirs globaux récents) + skills assignés (nom + description ; étapes complètes du skill déclenché ou demandé) + outils autorisés (`description_pour_prompt`) + extraits du cerveau pertinents si `acces_second_cerveau` (3 meilleurs résultats FTS du message) + format de réponse. **Format imposé** : JSON `{"pensee": "…", "actions": [{"outil": "…", "args": {…}}], "reponse": "…", "termine": true|false}`. Actions non vides → exécution par le backend (garde-fous, approbations), puis message « Résultats des outils : <json> » dans la même session, jusqu'à `termine=true` ou 8 tours. Ce qui est streamé à l'UI = le champ `reponse` (le flux brut est bufferisé ; on extrait progressivement la valeur de `"reponse"` si possible, sinon on diffuse la réponse finale en morceaux). Narration `narration` à chaque action. États : travaille → (attend_accord) → fini → libre (après 3 s) ; erreur si échec.
- JSON invalide → texte brut = réponse, termine.
- Modèle utilisé : celui demandé dans la requête, sinon `modele_principal` ; `effort_raisonnement` par défaut ; `modele_secours` inséré en 2e position de la chaîne de repli.
- `factory.py` : `async creer_agent_depuis_texte(description) -> dict` (hydrogen high → JSON conforme, id slug unique, avatar cohérent, skills/outils existants uniquement), outil `agent.creer`.
- API : `GET /api/agents` (avec état temps réel), `GET /api/agents/{id}`, `POST /api/agents`, `PUT /api/agents/{id}`, `DELETE /api/agents/{id}` (send2trash ; `jarvis` protégé), `POST /api/agents/creer-depuis-texte {description}`. `POST /api/chat {cible:"agent"}` passe par `repondre` (via `set_traiteur`).

## 11.8 Règles absolues (injectées dans TOUS les prompts système ; appliquées en dur par `guardrails.py`)
1. Ne jamais publier quoi que ce soit sur un réseau social ou une plateforme externe.
2. Ne jamais valider un paiement, un achat, une commande ou un abonnement.
3. Ne jamais supprimer de fichier système ou critique ; toute suppression passe par la corbeille et demande l'accord.
4. Avant toute action irréversible (envoi, validation, suppression, exécution de script), s'arrêter et demander l'accord de l'utilisateur.
5. Ne jamais saisir de mot de passe, de numéro de carte ou d'identifiant officiel.

## 11.9 Canaux (`canaux/<id>/canal.json`, `canaux/<id>/memoire.md`, `backend/agents/channel_orchestrator.py`, `backend/api/canaux.py`)
```json
{"id":"general","nom":"general","description":"…","membres":["jarvis","atlas","nova","maya","remi"],"coordinateur":"jarvis","max_tours_par_agent":2,"routines":[],"cree_le":"…"}
```
- Messages en base (`conversations` id `canal:<id>`), auteur = id d'agent ou `utilisateur`.
- `async traiter_message_canal(canal_id, message)` : 1) coordinateur (appel LLM court, JSON `{"ordre":[ids], "consignes":{id: "…"}}`) choisit 1..n membres pertinents dans un ordre logique ; 2) chacun répond à tour de rôle avec le fil récent du canal (20 derniers messages) + sa consigne, session `canal:<id>:<agent>`, en streamant ; 3) un agent qui mentionne `@autre` déclenche un tour de cet autre (limite `max_tours_par_agent`, total ≤ 2 × nb membres) ; 4) synthèse finale du coordinateur (meta `synthese: true`). Mémoire de canal : ≤ 5 puces ajoutées à `memoire.md` par échange (extraites par le coordinateur dans sa synthèse JSON `memoire:[…]`), injectées dans les prompts du canal.
- API : `GET /api/canaux`, `GET /api/canaux/{id}`, `POST /api/canaux`, `PUT /api/canaux/{id}`, `DELETE /api/canaux/{id}`, `GET /api/canaux/{id}/messages`. `POST /api/chat {cible:"canal"}` → orchestrateur.

## 11.10 Jarvis engine (`backend/jarvis_engine/`, `backend/api/jarvis.py`)
- `dpi.activer()` : `ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))` (repli `shcore.SetProcessDpiAwareness(2)`), appelé au démarrage du backend ET de l'overlay. Coordonnées = pixels physiques de l'écran virtuel.
- `vision.capturer(ecran=1, largeur_max=1280) -> Capture{chemin, largeur, hauteur, echelle, origine_x, origine_y}` (mss → `RUNTIME/captures/`, 20 dernières gardées ; `echelle` = taille réelle / taille image).
- `uia.fenetre_active() -> {titre, processus, pid, rect}` ; `uia.elements(max=300) -> [{index, nom, role, rect:{x,y,w,h}, actif}]` (contrôles visibles et nommés de la fenêtre active) ; `uia.element_au_point(x,y) -> {nom, role, rect} | None`.
- `ocr.lire(capture) -> [{texte, rect}]` (Windows.Media.Ocr, repli rapidocr) en coordonnées écran.
- `teacher.trouver(objectif) -> {x,y,w,h,label,source}` : candidats UIA + OCR numérotés → `router.generer` (hydrogen low, JSON `{"index": n, "raison": "…"}`) → repli vision (capture annotée de numéros → hydrogen + image). `teacher.guider(objectif, etapes=None)` : pour chaque étape, trouve la cible, broadcast `guide{id, boites:[{x,y,w,h,label:"1/2 Clique ici",couleur}], attendre_clic:true}`, attend `guide_clic` ou un clic réel dans le rectangle (pynput, 120 s), étape suivante, `guide_effacer` à la fin. Ne clique JAMAIS lui-même.
- `hands.py` : `cliquer(x,y,bouton="left")`, `double_cliquer`, `taper(texte)` (pyperclip + ctrl+v), `touche(combinaison)`, `defiler(dy)`, `ouvrir_application(nom)` (liste blanche : notepad, calc, explorer, mspaint, chrome, msedge, obsidian). `async executer_action(action: dict, attendu: dict | None) -> dict` = boucle obligatoire : capture → fenêtre active vérifiée (`attendu.titre_contient`/`attendu.processus`, sinon refus) → narration → `guardrails.verifier(action, contexte)` (contexte inclut `uia.element_au_point` pour les clics, juste avant) → `interdit` : refus ; `accord_requis` : `demander_accord` → exécution → nouvelle capture → vérification (fenêtre attendue toujours au premier plan, différence d'image) → résultat `{ok, avant, apres, fenetre, narration:[…]}`. pyautogui `FAILSAFE=True`, `PAUSE=0.15`. États `etat_jarvis action` pendant l'exécution.
- `guardrails.py` : `verifier(action, contexte) -> Decision(statut: "autorise"|"accord_requis"|"interdit", raison)`, `verifier_chemin_suppression(path) -> Decision`, `verifier_script(nom) -> Decision`. Normalisation : minuscules, NFKD sans accents, suppression des caractères invisibles/Unicode de format, homoglyphes cyrilliques/grecs courants → latin, espaces multiples. Interdits : mots de paiement (payer, paiement, acheter, achat, commander, commande, valider la commande, checkout, panier, cart, buy, purchase, pay now, carte bancaire, paypal, stripe, abonnement, s'abonner, subscribe) dans le titre de fenêtre, l'URL ou l'élément visé ; publication (publier, poster, partager, tweeter, tweet, post, share, publish, story) quand titre/URL contient facebook, instagram, x.com, twitter, linkedin, tiktok, youtube, reddit, threads, bluesky, mastodon, pinterest, snapchat, discord, telegram, whatsapp ; frappe d'une suite de 13–19 chiffres valide Luhn ; frappe dans un champ dont le rôle/nom indique un mot de passe. Accord requis : touche Entrée/ctrl+entrée ; clic sur un élément nommé envoyer, send, valider, confirmer, supprimer, delete, effacer, installer, enregistrer sous, oui, ok ; toute action dans regedit, services, gestion de l'ordinateur, paramètres Windows, powershell, cmd, terminal ; `ouvrir_application` hors liste blanche = interdit. Chemins interdits en suppression : racines de lecteur, `C:\Windows`, `C:\Program Files*`, `C:\ProgramData`, le dossier utilisateur lui-même, `AppData`, `.ssh`, `.gnupg`, tout `.git`, `ROOT/backend`, `ROOT/.venv`, `ROOT/runtime`, `ROOT/overlay`, `ROOT/frontend` ; ailleurs : accord requis + corbeille.
- Outils : `ecran.capturer()`, `ecran.fenetre_active()`, `ecran.trouver(objectif)`, `pc.action(action, attendu)` (risque action_pc), `professeur.guider(objectif, etapes?)`, `fichier.supprimer(chemin)` (irreversible, corbeille).
- API : `POST /api/ecran/capture` → `{nom, url, largeur, hauteur}` ; `GET /api/ecran/image/{nom}?token=` ; `GET /api/ecran/fenetre` ; `POST /api/professeur/trouver {objectif}` ; `POST /api/professeur/guider {objectif, etapes?}` → `{guide_id}` ; `POST /api/pc/action {action, attendu?}` ; `POST /api/overlay/test` (boîte de test au centre + narration + carte).

## 11.11 Voix (`backend/voice/`, `backend/api/voix.py`)
- `stt.transcrire(chemin) -> str` : faster-whisper `small` (CUDA float16, repli CPU int8 en cas d'erreur), `language="fr"`, `vad_filter=True`, chargement paresseux, verrou. `stt.enregistrer_micro(duree_max=15, silence_s=1.2) -> chemin_wav` (sounddevice 16 kHz mono, arrêt au silence après parole).
- `tts.synthetiser(texte, voix="fr-FR-DeniseNeural") -> chemin` (edge-tts mp3, cache par hash `RUNTIME/tts/`), repli `pyttsx3` wav.
- API : `POST /api/voix/transcrire` (multipart `audio`) → `{texte}` ; `POST /api/voix/ecouter` (micro du PC, broadcast `etat_jarvis ecoute`) → `{texte}` ; `POST /api/voix/parler {texte}` → `{url}` + broadcast `voix_parler{url,texte}` ; `GET /api/voix/audio/{nom}`.

## 11.13 Statistiques et liste de notes
- `GET /api/stats` (`backend/api/stats.py`) → `{"notes": n, "liens": n, "branches": {id: n}, "routines_actives": n, "runs_7j": {"ok": n, "erreur": n}, "taches_jour": {"faites": n, "total": n}, "messages_24h": n, "usage_llm_24h": {"appels": n, "tokens": n, "par_modele": {modele: appels}}, "projets_actifs": n, "souvenirs": n}`.
- `GET /api/cerveau/notes?branche=&statut=` → `[{"id","titre","branche","tags","statut","date","extrait"}]` (extrait = 200 premiers caractères sans frontmatter), triées par date décroissante.

## 11.12 Overlay (`overlay/`)
- Lit `runtime/token.txt` ; WS `ws://127.0.0.1:5230/ws?token=…&client=overlay` (reconnexion 3 s) ; envoie `overlay_hello`.
- Ctrl+Espace (repli Ctrl+Alt+Espace si déjà pris) → `POST /api/voix/ecouter` puis `POST /api/chat {cible:"agent", id:"jarvis", message}` ; à `chat_fin` de jarvis, si voix active → `POST /api/voix/parler` (l'audio est joué à réception de `voix_parler`).
