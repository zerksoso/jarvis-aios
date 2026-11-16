"""Moteur LLM Antigravity CLI (``agy``) avec streaming JSON et garde-fous Windows."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backend import config
from backend.llm import binaries
from backend.llm.base import DeltaCallback, EngineError, LLMRequest, LLMResult
from backend.llm.catalog import resoudre_modele
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

MAX_PROMPT_CLI_CHARS = 24_000
PROJET_DEFAUT = "default-cli-project"

_ENV_EXCLU_RE = re.compile(
    r"^(GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_GENAI_API_KEY|GOOGLE_GENERATIVE_AI_API_KEY|"
    r"ANTIGRAVITY_API_KEY|ANTIGRAVITY_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|"
    r"GOOGLE_CLOUD_PROJECT|GOOGLE_GENAI_USE_VERTEXAI|VERTEX_.*|.*_GEMINI_KEY)$",
    re.IGNORECASE,
)

_FACTURATION_RE = re.compile(
    r"(generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|x-goog-api-key|AIza[0-9A-Za-z_-]{10,})"
)

_desactive_facturation = False


def reset_etat_pour_tests() -> None:
    """Réinitialise l'état interne de désactivation pour les tests unitaires."""
    global _desactive_facturation
    _desactive_facturation = False


def _environnement_propre() -> dict[str, str]:
    """Construit un environnement enfant expurgé des clés API Google/Gemini."""
    return {k: v for k, v in os.environ.items() if not _ENV_EXCLU_RE.match(k)}


def _chemin_config_projet() -> Path:
    """Retourne le chemin vers ``ROOT/runtime/agy-projet.json``."""
    return config.RUNTIME / "agy-projet.json"


def _lire_utiliser_projet() -> bool:
    """Indique s'il faut passer ``--project default-cli-project`` à ``agy`` (défaut ``True``)."""
    chemin = _chemin_config_projet()
    if not chemin.is_file():
        return True
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if isinstance(donnees, dict) and "utiliser_project" in donnees:
        return bool(donnees["utiliser_project"])
    return True


def _ecrire_utiliser_projet(utiliser_project: bool) -> None:
    """Mémorise dans ``ROOT/runtime/agy-projet.json`` si ``--project`` doit être utilisé."""
    chemin = _chemin_config_projet()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_name(f"{chemin.stem}.{uuid.uuid4().hex[:8]}.tmp")
    contenu = {
        "utiliser_project": bool(utiliser_project),
        "projet": PROJET_DEFAUT if utiliser_project else None,
    }
    try:
        tmp.write_text(json.dumps(contenu, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(chemin)
    except OSError as exc:
        logger.warning("Impossible d'écrire %s : %s", chemin, exc)
    finally:
        tmp.unlink(missing_ok=True)


def _est_erreur_projet_absent(message: str) -> bool:
    """Détecte une erreur indiquant que le projet passé via ``--project`` n'existe pas."""
    bas = message.lower()
    if PROJET_DEFAUT in bas and any(
        cle in bas for cle in ("not found", "exist", "introuvable", "unknown", "invalid", "failed")
    ):
        return True
    if "project" not in bas and "projet" not in bas:
        return False
    return any(
        cle in bas
        for cle in (
            "not found",
            "does not exist",
            "doesn't exist",
            "no such",
            "unknown",
            "invalid",
            "introuvable",
            "n'existe pas",
            "not exist",
        )
    )


def parse_ligne(ligne: str) -> dict[str, Any] | None:
    """Parse une ligne ``stream-json`` émise par ``agy`` en événement normalisé.

    Args:
        ligne: Ligne brute lue sur stdout.

    Returns:
        Dictionnaire d'événement (``init``, ``delta`` ou ``result``) ou ``None``.
    """
    texte = ligne.strip()
    if not texte:
        return None
    try:
        donnees = json.loads(texte)
    except json.JSONDecodeError:
        return None
    if not isinstance(donnees, dict):
        return None

    event = donnees.get("event")
    if event == "init":
        return {
            "type": "init",
            "conversation_id": donnees.get("conversation_id"),
        }

    if event == "step_update":
        step = donnees.get("step_update")
        if not isinstance(step, dict):
            return None
        if step.get("step_type") == "agent_response":
            delta = step.get("text_delta")
            if isinstance(delta, str) and delta:
                return {
                    "type": "delta",
                    "delta": delta,
                    "state": step.get("state"),
                }
        return None

    if event == "result":
        res = donnees.get("result")
        if not isinstance(res, dict):
            return None
        usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
        return {
            "type": "result",
            "status": str(res.get("status") or ""),
            "conversation_id": res.get("conversation_id"),
            "response": str(res.get("response") or ""),
            "error": str(res.get("error") or ""),
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }

    return None


def _verifier_facturation(texte: str) -> None:
    """Détecte toute fuite de facturation API directe, désactive ``agy`` et alerte l'UI."""
    global _desactive_facturation
    if _FACTURATION_RE.search(texte):
        _desactive_facturation = True
        hub.broadcast_threadsafe(
            "toast",
            {
                "texte": "Facturation API détectée dans agy : moteur désactivé jusqu'au redémarrage.",
                "niveau": "erreur",
            },
        )
        raise EngineError("facturation API détectée", transitoire=False)


class AgyEngine:
    """Moteur d'exécution basé sur le CLI ``agy`` en mode sandbox ``stream-json``."""

    nom = "agy"

    async def disponible(self) -> tuple[bool, str]:
        """Indique si le binaire ``agy`` est disponible et non désactivé.

        Returns:
            Tuple ``(disponible, raison)``.
        """
        if _desactive_facturation:
            return False, "Moteur agy désactivé (facturation API détectée)"
        if not binaries.trouver("agy"):
            return False, "CLI agy introuvable"
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Exécute ``agy`` dans un thread dédié avec streaming des deltas.

        Args:
            req: Requête LLM complète.
            modele_id: Identifiant catalogue (ex. ``"hydrogen"``).
            session_id: Identifiant de conversation ``agy`` à reprendre, ou ``None``.
            on_delta: Coroutine appelée à chaque fragment de texte reçu.

        Returns:
            ``LLMResult`` contenant le texte final, les tokens et le ``session_id``.

        Raises:
            EngineError: En cas d'échec, timeout ou détection de facturation API.
        """
        ok, raison = await self.disponible()
        if not ok:
            raise EngineError(raison, transitoire=False)

        agy_bin = binaries.trouver("agy")
        if not agy_bin:
            raise EngineError("CLI agy introuvable", transitoire=False)

        _, agy_model_id, _ = resoudre_modele(modele_id, req.effort)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def _consommer_deltas() -> None:
            while True:
                delta = await queue.get()
                if delta is None:
                    break
                if on_delta is not None:
                    await on_delta(delta)

        consumer_task = asyncio.create_task(_consommer_deltas())
        debut = time.monotonic()

        def _executer_sync() -> LLMResult:
            config.AGY_SANDBOX.mkdir(parents=True, exist_ok=True)
            (config.AGY_SANDBOX / "img").mkdir(parents=True, exist_ok=True)
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)

            fichiers_temp: list[Path] = []
            try:
                prompt_effectif = req.prompt
                if session_id is None and req.systeme:
                    prompt_effectif = f"<<SYSTEME>>\n{req.systeme}\n<</SYSTEME>>\n\n{req.prompt}"

                images_relatives: list[str] = []
                for img_str in req.images:
                    src = Path(img_str)
                    if src.is_file():
                        ext = src.suffix or ".png"
                        nom_img = f"{uuid.uuid4().hex}{ext}"
                        dest = config.AGY_SANDBOX / "img" / nom_img
                        shutil.copy2(src, dest)
                        fichiers_temp.append(dest)
                        images_relatives.append(f"img/{nom_img}")

                if images_relatives:
                    prompt_effectif += (
                        "\n\nFichiers image joints (ouvre-les et analyse-les) : "
                        + ", ".join(images_relatives)
                    )

                arg_prompt = prompt_effectif
                if len(prompt_effectif) > MAX_PROMPT_CLI_CHARS:
                    nom_prompt = f"prompt-{uuid.uuid4().hex}.md"
                    chemin_prompt = config.AGY_SANDBOX / nom_prompt
                    chemin_prompt.write_text(prompt_effectif, encoding="utf-8")
                    fichiers_temp.append(chemin_prompt)
                    arg_prompt = (
                        f"Lis le fichier {nom_prompt} du dossier courant "
                        "et exécute les instructions qu'il contient."
                    )

                def _lancer_une_fois(avec_projet: bool) -> LLMResult:
                    cmd = [agy_bin]
                    if avec_projet:
                        cmd.extend(["--project", PROJET_DEFAUT])
                    cmd.extend(
                        [
                            "--add-dir",
                            str(config.AGY_SANDBOX),
                            "--sandbox",
                            "--disable-slash-commands",
                            "--model",
                            agy_model_id,
                            "--output-format",
                            "stream-json",
                            "--print-timeout",
                            f"{req.timeout_s}s",
                        ]
                    )
                    if session_id:
                        cmd.extend(["--conversation", session_id])
                    cmd.extend(["--print", arg_prompt])

                    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(config.AGY_SANDBOX),
                        env=_environnement_propre(),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        creationflags=creationflags,
                    )

                    stderr_lignes: list[str] = []

                    def _lire_stderr() -> None:
                        if proc.stderr is None:
                            return
                        for ligne_err in proc.stderr:
                            stderr_lignes.append(ligne_err)

                    t_err = threading.Thread(target=_lire_stderr, daemon=True)
                    t_err.start()

                    timed_out = False

                    def _on_timeout() -> None:
                        nonlocal timed_out
                        timed_out = True
                        try:
                            proc.kill()
                        except OSError:
                            pass

                    timer = threading.Timer(float(req.timeout_s) + 10.0, _on_timeout)
                    timer.start()

                    conv_id: str | None = session_id
                    deltas: list[str] = []
                    result_event: dict[str, Any] | None = None

                    try:
                        if proc.stdout is not None:
                            for ligne in proc.stdout:
                                evt = parse_ligne(ligne)
                                if evt is None:
                                    continue
                                etype = evt["type"]
                                if etype == "init" and evt.get("conversation_id"):
                                    conv_id = str(evt["conversation_id"])
                                elif etype == "delta":
                                    delta = str(evt["delta"])
                                    deltas.append(delta)
                                    loop.call_soon_threadsafe(queue.put_nowait, delta)
                                elif etype == "result":
                                    result_event = evt
                                    if evt.get("conversation_id"):
                                        conv_id = str(evt["conversation_id"])
                        proc.wait()
                    finally:
                        timer.cancel()
                        t_err.join(timeout=2.0)

                    stderr_text = "".join(stderr_lignes)
                    try:
                        (config.LOG_DIR / "agy-last.log").write_text(stderr_text, encoding="utf-8")
                    except OSError as exc:
                        logger.warning("Impossible d'écrire agy-last.log : %s", exc)

                    _verifier_facturation(stderr_text)

                    if timed_out:
                        raise EngineError(f"Expiration du délai agy ({req.timeout_s}s)", transitoire=True)

                    status = str((result_event or {}).get("status") or "")
                    err_result = str((result_event or {}).get("error") or "")
                    if err_result:
                        _verifier_facturation(err_result)

                    if status == "ERROR" or (proc.returncode != 0 and not result_event and not deltas):
                        msg = err_result or stderr_text.strip() or f"Échec agy (code {proc.returncode})"
                        transitoire = (
                            "Eligibility" in msg
                            or "EOF" in msg
                            or "UNAVAILABLE" in msg
                            or "deadline" in msg.lower()
                        )
                        raise EngineError(msg, transitoire=transitoire)

                    texte_final = str((result_event or {}).get("response") or "") or "".join(deltas)
                    return LLMResult(
                        texte=texte_final,
                        moteur=self.nom,
                        modele=modele_id,
                        session_id=conv_id,
                        input_tokens=int((result_event or {}).get("input_tokens") or 0),
                        output_tokens=int((result_event or {}).get("output_tokens") or 0),
                        duree_s=round(time.monotonic() - debut, 3),
                    )

                utiliser_projet = _lire_utiliser_projet()
                try:
                    return _lancer_une_fois(utiliser_projet)
                except EngineError as exc:
                    if utiliser_projet and _est_erreur_projet_absent(exc.message):
                        logger.info(
                            "Projet agy '%s' refusé (%s) ; repli sans --project mémorisé.",
                            PROJET_DEFAUT,
                            exc.message,
                        )
                        _ecrire_utiliser_projet(False)
                        return _lancer_une_fois(False)
                    raise
            finally:
                for fichier in fichiers_temp:
                    try:
                        fichier.unlink(missing_ok=True)
                    except OSError:
                        pass
                loop.call_soon_threadsafe(queue.put_nowait, None)

        try:
            return await asyncio.to_thread(_executer_sync)
        except BaseException:
            if not consumer_task.done():
                await queue.put(None)
            raise
        finally:
            await consumer_task
