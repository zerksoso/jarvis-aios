"""Moteur LLM Codex CLI (``codex exec --json`` et ``codex login status``)."""

import asyncio
import json
import logging
import subprocess
import threading
import time
from typing import Any

from backend import config
from backend.llm import binaries
from backend.llm.base import DeltaCallback, EngineError, LLMRequest, LLMResult
from backend.llm.catalog import resoudre_modele

logger = logging.getLogger(__name__)

RAISON_NON_INSTALLE = "Codex CLI non installé"
RAISON_NON_CONNECTE = "Codex CLI non connecté : lance `codex login`"
_LOGIN_TIMEOUT_S = 10.0


def _extraire_texte_item(item: dict[str, Any]) -> str:
    """Extrait le texte d'un objet ``item`` issu d'un événement JSONL Codex."""
    texte_direct = item.get("text")
    if isinstance(texte_direct, str) and texte_direct:
        return texte_direct

    contenu = item.get("content")
    if isinstance(contenu, str) and contenu:
        return contenu
    if isinstance(contenu, list):
        morceaux: list[str] = []
        for bloc in contenu:
            if isinstance(bloc, dict):
                txt = bloc.get("text") or bloc.get("output_text")
                if isinstance(txt, str) and txt:
                    morceaux.append(txt)
        return "".join(morceaux)
    return ""


def parse_ligne(ligne: str) -> dict[str, Any] | None:
    """Parse une ligne JSONL émise par ``codex exec --json`` en événement normalisé.

    Supporte les événements structurés Codex CLI (``thread.started``, ``item.completed``,
    ``item.agent_message.delta``, ``turn.completed``, ``turn.failed``, ``error``) ainsi
    que les formats simplifiés (``msg.agent_message``, ``text``/``content``/``output``).

    Args:
        ligne: Ligne brute lue sur stdout.

    Returns:
        Dictionnaire d'événement (``init``, ``delta``, ``agent_message``, ``usage`` ou ``error``),
        ou ``None`` si la ligne est ignorée ou non JSON.
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

    evt_type = donnees.get("type")

    if evt_type == "thread.started" and donnees.get("thread_id"):
        return {"type": "init", "session_id": str(donnees["thread_id"])}

    if evt_type in ("item.agent_message.delta", "response.output_text.delta"):
        delta = donnees.get("delta")
        if isinstance(delta, str) and delta:
            return {"type": "delta", "delta": delta}
        return None

    if evt_type == "item.completed":
        item = donnees.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "agent_message" or (
                item_type == "message" and item.get("role", "assistant") == "assistant"
            ):
                msg_texte = _extraire_texte_item(item)
                if msg_texte:
                    return {"type": "agent_message", "text": msg_texte}
        return None

    if evt_type == "turn.completed":
        usage = donnees.get("usage") if isinstance(donnees.get("usage"), dict) else {}
        return {
            "type": "usage",
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }

    if evt_type in ("turn.failed", "error"):
        err_obj = donnees.get("error")
        if isinstance(err_obj, dict):
            msg_err = str(err_obj.get("message") or "")
        elif isinstance(err_obj, str):
            msg_err = err_obj
        else:
            msg_err = str(donnees.get("message") or "")
        if msg_err:
            return {"type": "error", "error": msg_err}
        return None

    msg_obj = donnees.get("msg")
    if isinstance(msg_obj, dict) and msg_obj.get("type") == "agent_message":
        msg_texte = msg_obj.get("message")
        if isinstance(msg_texte, str) and msg_texte:
            return {"type": "agent_message", "text": msg_texte}

    if evt_type == "message" and donnees.get("role", "assistant") == "assistant":
        msg_texte = _extraire_texte_item(donnees)
        if msg_texte:
            return {"type": "agent_message", "text": msg_texte}

    if evt_type is None:
        val = donnees.get("text") or donnees.get("content") or donnees.get("output")
        if isinstance(val, str) and val:
            return {"type": "agent_message", "text": val}

    return None


def _verifier_login_codex(codex_bin: str) -> tuple[bool, str]:
    """Vérifie la connexion Codex via ``codex login status`` sans fenêtre console."""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            [codex_bin, "login", "status"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
    except OSError as exc:
        return False, f"Impossible de lancer codex : {exc}"

    try:
        _stdout, _stderr = proc.communicate(timeout=_LOGIN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False, "Délai dépassé lors de la vérification de connexion Codex"

    if proc.returncode == 0:
        return True, "OK"
    return False, RAISON_NON_CONNECTE


class CodexEngine:
    """Moteur d'exécution basé sur OpenAI Codex CLI (``codex exec --json``)."""

    nom = "codex"

    async def disponible(self) -> tuple[bool, str]:
        """Indique si ``codex`` est installé et connecté via ``codex login status``.

        Returns:
            Tuple ``(disponible, raison)``.
        """
        codex_bin = binaries.trouver("codex")
        if not codex_bin:
            return False, RAISON_NON_INSTALLE
        return await asyncio.to_thread(_verifier_login_codex, codex_bin)

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Exécute ``codex exec --json`` dans un thread dédié et retourne le dernier message agent.

        Note : implémenté d'après la documentation CLI Codex (non testé avec un vrai compte).

        Args:
            req: Requête LLM complète.
            modele_id: Identifiant catalogue (ex. ``"codex"``).
            session_id: Ignoré (reprise non activée sans validation sur un vrai compte).
            on_delta: Callback asynchrone recevant les deltas ou la réponse finale.

        Returns:
            ``LLMResult`` contenant le dernier message agent et l'usage éventuel.

        Raises:
            EngineError: Si ``codex`` est absent, déconnecté, en timeout ou en erreur.
        """
        del session_id
        ok, raison = await self.disponible()
        if not ok:
            raise EngineError(raison, transitoire=False)

        codex_bin = binaries.trouver("codex")
        if not codex_bin:
            raise EngineError(RAISON_NON_INSTALLE, transitoire=False)

        _, cli_model, _ = resoudre_modele(modele_id, req.effort)
        prompt_effectif = f"{req.systeme}\n\n{req.prompt}" if req.systeme else req.prompt
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        debut = time.monotonic()

        async def _consommer_deltas() -> None:
            while True:
                delta = await queue.get()
                if delta is None:
                    break
                if on_delta is not None:
                    await on_delta(delta)

        consumer_task = asyncio.create_task(_consommer_deltas())

        def _executer_sync() -> LLMResult:
            config.ROOT.mkdir(parents=True, exist_ok=True)
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)

            cmd = [codex_bin, "exec", "--json"]
            if cli_model and cli_model not in ("default", "codex"):
                cmd.extend(["--model", cli_model])
            cmd.append(prompt_effectif)

            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(config.ROOT),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
            except OSError as exc:
                raise EngineError(f"Impossible de lancer codex : {exc}", transitoire=False) from exc

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

            messages_agent: list[str] = []
            deltas: list[str] = []
            erreurs: list[str] = []
            lignes_brutes: list[str] = []
            input_tokens = 0
            output_tokens = 0

            try:
                if proc.stdout is not None:
                    for ligne in proc.stdout:
                        lignes_brutes.append(ligne)
                        evt = parse_ligne(ligne)
                        if evt is None:
                            continue
                        etype = evt["type"]
                        if etype == "delta":
                            delta = str(evt["delta"])
                            deltas.append(delta)
                            loop.call_soon_threadsafe(queue.put_nowait, delta)
                        elif etype == "agent_message":
                            messages_agent.append(str(evt["text"]))
                        elif etype == "usage":
                            input_tokens = int(evt.get("input_tokens") or 0)
                            output_tokens = int(evt.get("output_tokens") or 0)
                        elif etype == "error":
                            erreurs.append(str(evt["error"]))
                proc.wait()
            finally:
                timer.cancel()
                t_err.join(timeout=2.0)

            stderr_text = "".join(stderr_lignes)
            try:
                (config.LOG_DIR / "codex-last.log").write_text(stderr_text, encoding="utf-8")
            except OSError as exc:
                logger.warning("Impossible d'écrire codex-last.log : %s", exc)

            if timed_out:
                raise EngineError(f"Expiration du délai codex ({req.timeout_s}s)", transitoire=True)

            if erreurs and not messages_agent and not deltas:
                raise EngineError(erreurs[-1], transitoire=False)

            if proc.returncode != 0 and not messages_agent and not deltas:
                msg = stderr_text.strip() or f"Échec codex (code {proc.returncode})"
                raise EngineError(msg, transitoire=False)

            if messages_agent:
                texte_final = messages_agent[-1]
            elif deltas:
                texte_final = "".join(deltas)
            else:
                texte_final = "".join(lignes_brutes).strip()

            if not deltas and texte_final:
                loop.call_soon_threadsafe(queue.put_nowait, texte_final)

            return LLMResult(
                texte=texte_final,
                moteur=self.nom,
                modele=modele_id,
                session_id=None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duree_s=round(time.monotonic() - debut, 3),
            )

        def _executer_avec_fin_queue() -> LLMResult:
            try:
                return _executer_sync()
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        try:
            return await asyncio.to_thread(_executer_avec_fin_queue)
        except BaseException:
            if not consumer_task.done():
                await queue.put(None)
            raise
        finally:
            await consumer_task
