"""Routes API de l'assistant de premier lancement (``/api/setup/*``)."""

import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from backend import bootstrap, config
from backend.brain import indexer, memories
from backend.llm import agy_usage, binaries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup", tags=["setup"])

MoteurSetup = Literal["agy", "claude", "codex"]
MOTEURS_SUPPORTES: tuple[str, ...] = ("agy", "claude", "codex")

URL_INSTALL_CLAUDE = "https://claude.ai/install.ps1"
URL_TELECHARGEMENT_AGY = "https://antigravity.google/download"

MAX_LOG_LIGNES = 200
DUREE_RETENTION_JOB_S = 3600.0
MAX_CHAMP_PROFIL_CARS = 500
_TIMEOUT_SONDE_S = 10.0
_TIMEOUT_VERSION_S = 5.0
_TIMEOUT_INSTALL_S = 600.0

_ENV_CLAUDE_EXCLU_RE = re.compile(r"^(CLAUDE_CODE_.*|ANTHROPIC_.*|CLAUDECODE)$", re.IGNORECASE)
_ENV_AGY_EXCLU_RE = re.compile(
    r"^(GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_GENAI_API_KEY|GOOGLE_GENERATIVE_AI_API_KEY|"
    r"ANTIGRAVITY_API_KEY|ANTIGRAVITY_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|"
    r"GOOGLE_CLOUD_PROJECT|GOOGLE_GENAI_USE_VERTEXAI|VERTEX_.*|.*_GEMINI_KEY)$",
    re.IGNORECASE,
)

_CHAMPS_PROFIL_CONFIG: tuple[tuple[str, str, str, str], ...] = (
    ("prenom", "Qui je suis", "Prénom", "fait"),
    ("activite", "Qui je suis", "Activité principale", "fait"),
    ("secteur", "Qui je suis", "Secteur & clients cibles", "fait"),
    ("objectifs", "Objectifs", "Objectif du trimestre", "fait"),
    ("outils", "Outils utilisés", "Autres outils", "fait"),
    ("preferences", "Préférences de travail", "Préférences", "preference"),
)

_LABELS_PROFIL_RE = re.compile(
    r"^\s*-\s*\*\*(Prénom|Activité principale|Secteur & clients cibles|"
    r"Objectif du trimestre|Plage de concentration|Autres outils|Préférences)\*\*\s*:\s*(.+?)\s*$"
)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_taches_install: set[asyncio.Task[Any]] = set()


class ProfilSetupRequest(BaseModel):
    """Corps de requête optionnel de l'étape 3 du setup (profil utilisateur)."""

    prenom: str = ""
    activite: str = ""
    secteur: str = ""
    objectifs: str = ""
    outils: str = ""
    preferences: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _coercition_chaine(cls, valeur: Any) -> str:
        """Convertit ``None`` ou valeur scalaire en chaîne nettoyée."""
        if valeur is None:
            return ""
        return str(valeur)


def reset_etat_pour_tests() -> None:
    """Réinitialise le registre en mémoire des jobs d'installation (tests unitaires)."""
    with _jobs_lock:
        _jobs.clear()
    for tache in list(_taches_install):
        if not tache.done():
            tache.cancel()
    _taches_install.clear()


def _purger_jobs_expires_sans_verrou(maintenant: float | None = None) -> None:
    """Supprime les jobs terminés depuis plus d'une heure (appelé sous ``_jobs_lock``)."""
    ref = time.monotonic() if maintenant is None else maintenant
    expires = [
        job_id
        for job_id, job in _jobs.items()
        if job.get("statut") != "en_cours"
        and job.get("termine_le") is not None
        and (ref - float(job["termine_le"])) > DUREE_RETENTION_JOB_S
    ]
    for job_id in expires:
        _jobs.pop(job_id, None)


def _ajouter_log_job(job_id: str, ligne: str) -> None:
    """Ajoute une ligne au journal d'un job en conservant au plus 200 lignes."""
    propre = ligne.rstrip("\r\n")
    if not propre:
        return
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        log: list[str] = job["log"]
        log.append(propre)
        if len(log) > MAX_LOG_LIGNES:
            del log[: len(log) - MAX_LOG_LIGNES]


def _env_sans_cles(pattern: re.Pattern[str]) -> dict[str, str]:
    """Construit une copie de ``os.environ`` sans les variables correspondant au motif."""
    return {k: v for k, v in os.environ.items() if not pattern.match(k)}


def _executer_sonde_sync(
    cmd: list[str],
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> tuple[int | None, str, str]:
    """Exécute un sous-processus court sans fenêtre console et capture stdout/stderr.

    Args:
        cmd: Commande et arguments (sans shell).
        timeout_s: Délai maximal en secondes avant terminaison forcée.
        env: Environnement enfant optionnel.

    Returns:
        Tuple ``(returncode_ou_None, stdout, stderr)``.
    """
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=creationflags,
        )
    except OSError as exc:
        return None, "", str(exc)

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None, "", "timeout"

    return proc.returncode, stdout or "", stderr or ""


def _lire_version_binaire(chemin: str) -> str | None:
    """Interroge ``<binaire> --version`` sans fenêtre et retourne la première ligne utile."""
    code, stdout, stderr = _executer_sonde_sync([chemin, "--version"], _TIMEOUT_VERSION_S)
    if code != 0:
        return None
    texte = (stdout or stderr).strip()
    if not texte:
        return None
    premiere = texte.splitlines()[0].strip()
    return premiere[:64] if premiere else None


def _claude_est_connecte_sync(chemin: str) -> bool:
    """Vérifie l'authentification Claude Code via ``claude auth status`` (JSON si disponible)."""
    code, stdout, stderr = _executer_sonde_sync(
        [chemin, "auth", "status"],
        _TIMEOUT_SONDE_S,
        env=_env_sans_cles(_ENV_CLAUDE_EXCLU_RE),
    )
    sortie = stdout.strip()
    if sortie:
        try:
            donnees = json.loads(sortie)
        except json.JSONDecodeError:
            donnees = None
        if isinstance(donnees, dict):
            for cle in ("loggedIn", "authenticated", "isAuthenticated", "connected", "connecte"):
                if isinstance(donnees.get(cle), bool):
                    return bool(donnees[cle])
            statut = str(donnees.get("status") or "").strip().lower()
            if statut in ("unauthenticated", "logged_out", "disconnected"):
                return False
            if code == 0:
                return True

    if code != 0:
        return False
    combine = f"{stdout}\n{stderr}".lower()
    if any(m in combine for m in ("not logged in", "unauthenticated", "logged out", "non connecté")):
        return False
    return True


def _codex_est_connecte_sync(chemin: str) -> bool:
    """Vérifie la connexion Codex via ``codex login status`` (code retour 0 attendu)."""
    code, _stdout, _stderr = _executer_sonde_sync([chemin, "login", "status"], _TIMEOUT_SONDE_S)
    return code == 0


async def _etat_agy() -> dict[str, Any]:
    """Inspecte l'état d'installation, de version et de connexion d'Antigravity (``agy``)."""
    chemin = binaries.trouver("agy")
    if not chemin:
        return {
            "installe": False,
            "connecte": False,
            "chemin": None,
            "version": None,
            "installation_auto": False,
            "page_telechargement": URL_TELECHARGEMENT_AGY,
        }

    version_task = asyncio.to_thread(_lire_version_binaire, chemin)
    usage_task = agy_usage.lire_usage()
    version, usage = await asyncio.gather(version_task, usage_task)
    connecte = bool(isinstance(usage, dict) and isinstance(usage.get("buckets"), dict) and usage["buckets"])
    return {
        "installe": True,
        "connecte": connecte,
        "chemin": chemin,
        "version": version,
        "installation_auto": False,
        "page_telechargement": URL_TELECHARGEMENT_AGY,
    }


async def _etat_claude() -> dict[str, Any]:
    """Inspecte l'état d'installation, de version et de connexion de Claude Code (``claude``)."""
    chemin = binaries.trouver("claude")
    if not chemin:
        return {
            "installe": False,
            "connecte": False,
            "chemin": None,
            "version": None,
            "installation_auto": True,
            "page_telechargement": None,
        }

    version, connecte = await asyncio.gather(
        asyncio.to_thread(_lire_version_binaire, chemin),
        asyncio.to_thread(_claude_est_connecte_sync, chemin),
    )
    return {
        "installe": True,
        "connecte": connecte,
        "chemin": chemin,
        "version": version,
        "installation_auto": True,
        "page_telechargement": None,
    }


async def _etat_codex(npm_installe: bool) -> dict[str, Any]:
    """Inspecte l'état d'installation, de version et de connexion de Codex (``codex``)."""
    chemin = binaries.trouver("codex")
    if not chemin:
        return {
            "installe": False,
            "connecte": False,
            "chemin": None,
            "version": None,
            "installation_auto": npm_installe,
            "page_telechargement": None,
        }

    version, connecte = await asyncio.gather(
        asyncio.to_thread(_lire_version_binaire, chemin),
        asyncio.to_thread(_codex_est_connecte_sync, chemin),
    )
    return {
        "installe": True,
        "connecte": connecte,
        "chemin": chemin,
        "version": version,
        "installation_auto": npm_installe,
        "page_telechargement": None,
    }


def _chemin_profil_utilisateur() -> Path:
    """Retourne le chemin vers ``BRAIN_DIR/00-Profil/profil-utilisateur.md``."""
    return config.BRAIN_DIR / "00-Profil" / "profil-utilisateur.md"


def _profil_est_rempli() -> bool:
    """Indique si au moins un champ du profil utilisateur a été renseigné hors ``(à compléter)``."""
    chemin = _chemin_profil_utilisateur()
    if not chemin.is_file():
        return False
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except OSError:
        return False

    for ligne in contenu.splitlines():
        correspondance = _LABELS_PROFIL_RE.match(ligne)
        if not correspondance:
            continue
        valeur = correspondance.group(2).strip()
        if valeur and valeur != "(à compléter)":
            return True
    return False


def _nettoyer_champ_profil(valeur: str) -> str:
    """Nettoie un champ texte pour éviter toute injection frontmatter/Markdown et limite à 500 car."""
    sans_nul = valeur.replace("\x00", "")
    une_ligne = " ".join(sans_nul.replace("\r", " ").replace("\n", " ").split())
    # Empêche l'apparition d'un délimiteur YAML frontmatter dans le contenu saisi.
    sans_fm = une_ligne.replace("---", "-")
    return sans_fm[:MAX_CHAMP_PROFIL_CARS].strip()


def _lire_contenu_profil_initial(chemin: Path) -> str:
    """Lit la note de profil existante ou part du modèle vierge si absente."""
    if chemin.is_file():
        return chemin.read_text(encoding="utf-8")

    modele = config.TEMPLATES_DIR / "second-brain" / "00-Profil" / "profil-utilisateur.md"
    if modele.is_file():
        return modele.read_text(encoding="utf-8")

    return (
        "---\n"
        "titre: Profil utilisateur\n"
        "tags: [profil, preferences, jarvis]\n"
        "branche: 00-Profil\n"
        "statut: actif\n"
        "---\n\n"
        "# Profil utilisateur\n"
    )


def _appliquer_champ_dans_markdown(contenu: str, section: str, label: str, valeur: str) -> str:
    """Remplace ``(à compléter)`` pour ``label`` ou ajoute la ligne/section si absente.

    Ne modifie aucune autre section ni aucun champ déjà rempli par l'utilisateur.
    """
    lignes = contenu.splitlines()
    motif_placeholder = re.compile(
        rf"^(\s*-\s*\*\*{re.escape(label)}\*\*\s*:\s*)\(à compléter\)\s*$"
    )
    for idx, ligne in enumerate(lignes):
        m = motif_placeholder.match(ligne)
        if m:
            lignes[idx] = f"{m.group(1)}{valeur}"
            return "\n".join(lignes) + ("\n" if contenu.endswith("\n") else "")

    motif_existant = re.compile(rf"^\s*-\s*\*\*{re.escape(label)}\*\*\s*:")
    if any(motif_existant.match(ligne) for ligne in lignes):
        # Champ déjà renseigné manuellement : on le préserve sans l'écraser.
        return contenu

    entete_section = f"## {section}"
    nouvelle_ligne = f"- **{label}** : {valeur}"
    idx_section: int | None = None
    for idx, ligne in enumerate(lignes):
        if ligne.strip() == entete_section:
            idx_section = idx
            break

    if idx_section is not None:
        # Insère juste avant la section suivante (ou en fin de fichier), sans toucher le reste.
        fin_section = len(lignes)
        for j in range(idx_section + 1, len(lignes)):
            if lignes[j].startswith("## "):
                fin_section = j
                break
        while fin_section > idx_section + 1 and not lignes[fin_section - 1].strip():
            fin_section -= 1
        lignes.insert(fin_section, nouvelle_ligne)
        return "\n".join(lignes) + "\n"

    if lignes and lignes[-1].strip():
        lignes.append("")
    lignes.extend([entete_section, "", nouvelle_ligne])
    return "\n".join(lignes) + "\n"


async def _telecharger_script_claude(
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """Télécharge le script officiel PowerShell d'installation Claude Code via HTTPS."""
    async with httpx.AsyncClient(
        timeout=30.0,
        follow_redirects=True,
        transport=transport,
    ) as client:
        reponse = await client.get(
            URL_INSTALL_CLAUDE,
            headers={"User-Agent": "JarvisAIOS-Setup"},
        )
        reponse.raise_for_status()
        texte = reponse.text
    if not texte.strip():
        raise ValueError("Le script d'installation téléchargé est vide.")
    return texte


def _executer_commande_avec_logs_sync(
    job_id: str,
    cmd: list[str],
    env: dict[str, str] | None = None,
) -> int:
    """Lance une commande sans fenêtre et pousse chaque ligne de sortie dans le job."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=creationflags,
    )

    timed_out = False

    def _on_timeout() -> None:
        nonlocal timed_out
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(_TIMEOUT_INSTALL_S, _on_timeout)
    timer.start()
    try:
        if proc.stdout is not None:
            for ligne in proc.stdout:
                _ajouter_log_job(job_id, ligne)
        proc.wait()
    finally:
        timer.cancel()

    if timed_out:
        _ajouter_log_job(job_id, f"Expiration du délai d'installation ({int(_TIMEOUT_INSTALL_S)}s).")
        return 1
    return int(proc.returncode or 0)


async def _executer_job_installation(job_id: str, moteur: str) -> None:
    """Orchestre le téléchargement/installation asynchrone d'un moteur et met à jour le job."""
    script_temp: Path | None = None
    try:
        if moteur == "claude":
            _ajouter_log_job(job_id, f"Téléchargement du script officiel {URL_INSTALL_CLAUDE}…")
            contenu_ps1 = await _telecharger_script_claude()
            dossier_tmp = config.RUNTIME / "tmp"
            dossier_tmp.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".ps1",
                prefix="claude-install-",
                dir=str(dossier_tmp),
                delete=False,
            ) as tmp:
                tmp.write(contenu_ps1)
                script_temp = Path(tmp.name)

            _ajouter_log_job(job_id, "Exécution de l'installeur Claude Code (PowerShell)…")
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_temp),
            ]
            code = await asyncio.to_thread(
                _executer_commande_avec_logs_sync,
                job_id,
                cmd,
                _env_sans_cles(_ENV_CLAUDE_EXCLU_RE),
            )
        elif moteur == "codex":
            npm_bin = binaries.trouver("npm")
            if not npm_bin:
                raise RuntimeError("npm est introuvable pour installer @openai/codex.")
            _ajouter_log_job(job_id, "Exécution de : npm install -g @openai/codex…")
            cmd = [npm_bin, "install", "-g", "@openai/codex"]
            code = await asyncio.to_thread(_executer_commande_avec_logs_sync, job_id, cmd)
        else:
            raise ValueError(f"Moteur non installable automatiquement : {moteur}")

        statut_final = "ok" if code == 0 else "erreur"
        if code != 0:
            _ajouter_log_job(job_id, f"Échec de l'installation (code de sortie {code}).")
        else:
            _ajouter_log_job(job_id, "Installation terminée avec succès.")
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["statut"] = statut_final
                _jobs[job_id]["termine_le"] = time.monotonic()
    except Exception as exc:
        logger.warning("Échec du job d'installation %s (%s) : %s", job_id, moteur, exc)
        _ajouter_log_job(job_id, f"Erreur : {exc}")
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id]["statut"] = "erreur"
                _jobs[job_id]["termine_le"] = time.monotonic()
    finally:
        if script_temp is not None:
            script_temp.unlink(missing_ok=True)


def _sauver_profil_sync(donnees: ProfilSetupRequest) -> bool:
    """Met à jour ``profil-utilisateur.md`` et crée les souvenirs globaux correspondants."""
    chemin = _chemin_profil_utilisateur()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    contenu = _lire_contenu_profil_initial(chemin)

    champs_nettoyes: dict[str, str] = {}
    for cle, section, label, _type_mem in _CHAMPS_PROFIL_CONFIG:
        valeur_brute = getattr(donnees, cle, "")
        propre = _nettoyer_champ_profil(valeur_brute)
        if not propre:
            continue
        champs_nettoyes[cle] = propre
        contenu = _appliquer_champ_dans_markdown(contenu, section, label, propre)

    if champs_nettoyes or not chemin.is_file():
        tmp = chemin.with_name(f"{chemin.stem}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(contenu, encoding="utf-8")
            tmp.replace(chemin)
        finally:
            tmp.unlink(missing_ok=True)

        try:
            indexer.indexer_fichier(chemin)
        except Exception as exc:
            logger.debug("Réindexation du profil différée ou ignorée (%s).", exc)

    gabarits_souvenirs: dict[str, tuple[str, str]] = {
        "prenom": ("fait", "Prénom de l'utilisateur : {val}."),
        "activite": ("fait", "Activité principale de l'utilisateur : {val}."),
        "secteur": ("fait", "Secteur et clients cibles : {val}."),
        "objectifs": ("fait", "Objectifs avec Jarvis : {val}."),
        "outils": ("fait", "Outils utilisés au quotidien : {val}."),
        "preferences": ("preference", "Préférences de travail et communication : {val}."),
    }
    for cle, propre in champs_nettoyes.items():
        type_mem, gabarit = gabarits_souvenirs[cle]
        memories.ajouter_souvenir(
            type_souvenir=type_mem,
            texte=gabarit.format(val=propre),
            source="setup",
            agent="jarvis",
        )

    return _profil_est_rempli()


@router.get("/etat")
async def lire_etat_setup() -> dict[str, Any]:
    """Retourne l'état du premier lancement, des moteurs détectés, de Node et du profil.

    Returns:
        Dictionnaire conforme au contrat ``GET /api/setup/etat`` (§4).

    Effets de bord:
        Exécute des sondes CLI courtes (``--version``, ``auth status``, ``login status``,
        ``agy /usage``) si les binaires sont présents sur la machine.
    """
    premier = bootstrap.lire_etat_premier_lancement()
    node_installe = binaries.trouver("node") is not None
    npm_installe = binaries.trouver("npm") is not None

    agy_etat, claude_etat, codex_etat = await asyncio.gather(
        _etat_agy(),
        _etat_claude(),
        _etat_codex(npm_installe),
    )

    return {
        "termine": bool(premier.get("termine", False)),
        "version": config.VERSION,
        "moteurs": {
            "agy": agy_etat,
            "claude": claude_etat,
            "codex": codex_etat,
        },
        "node": {"installe": node_installe},
        "profil_rempli": _profil_est_rempli(),
    }


@router.post("/installer/{moteur}")
async def installer_moteur(moteur: str) -> dict[str, str]:
    """Démarre en arrière-plan l'installation automatisée de ``claude`` ou ``codex``.

    Args:
        moteur: Identifiant du moteur demandé (``claude`` ou ``codex``).

    Returns:
        Dictionnaire ``{"job_id": "..."}``.

    Raises:
        HTTPException: 400 si moteur inconnu ou manuel (``agy``), 409 si ``npm`` manque pour Codex.

    Effets de bord:
        Crée un job suivi en mémoire et lance une tâche asynchrone d'installation.
    """
    moteur_propre = moteur.strip().lower()
    if moteur_propre not in MOTEURS_SUPPORTES:
        raise HTTPException(status_code=400, detail=f"Moteur inconnu : {moteur}")

    if moteur_propre == "agy":
        raise HTTPException(
            status_code=400,
            detail="Antigravity (agy) doit être installé manuellement depuis la page officielle.",
        )

    if moteur_propre == "codex" and binaries.trouver("npm") is None:
        raise HTTPException(
            status_code=409,
            detail="Node.js (npm) est requis pour installer Codex automatiquement.",
        )

    job_id = f"setup-{moteur_propre}-{uuid.uuid4().hex[:8]}"
    with _jobs_lock:
        _purger_jobs_expires_sans_verrou()
        _jobs[job_id] = {
            "id": job_id,
            "moteur": moteur_propre,
            "statut": "en_cours",
            "log": [],
            "termine_le": None,
        }

    tache = asyncio.create_task(_executer_job_installation(job_id, moteur_propre))
    _taches_install.add(tache)
    tache.add_done_callback(_taches_install.discard)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
async def lire_job_installation(job_id: str) -> dict[str, Any]:
    """Retourne le statut et le journal (max 200 lignes) d'un job d'installation.

    Args:
        job_id: Identifiant renvoyé par ``POST /api/setup/installer/{moteur}``.

    Returns:
        Dictionnaire ``{"statut": "en_cours|ok|erreur", "log": [str]}``.

    Raises:
        HTTPException: 404 si le job est inconnu ou expiré.
    """
    with _jobs_lock:
        _purger_jobs_expires_sans_verrou()
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job d'installation introuvable")
        return {
            "statut": job["statut"],
            "log": list(job["log"][-MAX_LOG_LIGNES:]),
        }


@router.post("/connecter/{moteur}")
async def connecter_moteur(moteur: str) -> dict[str, bool]:
    """Ouvre une console interactive dédiée pour authentifier l'utilisateur sur un moteur.

    Args:
        moteur: Identifiant du moteur (``agy``, ``claude`` ou ``codex``).

    Returns:
        ``{"ok": True}`` une fois la fenêtre console lancée.

    Raises:
        HTTPException: 400 si moteur inconnu, 409 si le binaire est absent, 500 si lancement impossible.

    Effets de bord:
        Démarre un processus détaché avec ``CREATE_NEW_CONSOLE``.
    """
    moteur_propre = moteur.strip().lower()
    if moteur_propre not in MOTEURS_SUPPORTES:
        raise HTTPException(status_code=400, detail=f"Moteur inconnu : {moteur}")

    chemin = binaries.trouver(moteur_propre)
    if not chemin:
        raise HTTPException(
            status_code=409,
            detail=f"Le moteur {moteur_propre} n'est pas installé sur cet ordinateur.",
        )

    if moteur_propre == "claude":
        cmd = [chemin, "auth", "login"]
        env = _env_sans_cles(_ENV_CLAUDE_EXCLU_RE)
    elif moteur_propre == "codex":
        cmd = [chemin, "login"]
        env = None
    else:
        cmd = [chemin]
        env = _env_sans_cles(_ENV_AGY_EXCLU_RE)

    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen(
            cmd,
            env=env,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Impossible d'ouvrir la fenêtre de connexion pour {moteur_propre} : {exc}",
        ) from exc

    return {"ok": True}


@router.post("/profil")
async def enregistrer_profil(body: ProfilSetupRequest) -> dict[str, bool]:
    """Complète ``profil-utilisateur.md`` sans écraser les autres sections et crée des souvenirs.

    Args:
        body: Champs optionnels saisis à l'étape 3 (tronqués à 500 caractères et nettoyés).

    Returns:
        ``{"ok": True, "profil_rempli": bool}``.

    Effets de bord:
        Met à jour ``second-brain/00-Profil/profil-utilisateur.md``, réindexe la note,
        ajoute des souvenirs globaux via ``brain.memories.ajouter_souvenir`` et met à jour
        l'étape dans ``premier_lancement.json``.
    """
    rempli = await asyncio.to_thread(_sauver_profil_sync, body)
    etat_actuel = bootstrap.lire_etat_premier_lancement()
    if not etat_actuel.get("termine"):
        bootstrap.ecrire_etat_premier_lancement(etape="termine")
    return {"ok": True, "profil_rempli": rempli}


@router.post("/terminer")
async def terminer_setup() -> dict[str, bool]:
    """Marque l'assistant de premier lancement comme terminé dans ``premier_lancement.json``.

    Returns:
        ``{"ok": True, "termine": True}``.

    Effets de bord:
        Met à jour ``ROOT/runtime/premier_lancement.json`` avec ``termine=True``.
    """
    etat = bootstrap.ecrire_etat_premier_lancement(termine=True, etape="termine")
    return {"ok": True, "termine": bool(etat.get("termine", True))}
