"""Moteur LLM Claude Code CLI (``claude.cmd``) avec streaming et gestion OAuth."""

import asyncio
import json
import logging
import os
import re
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

logger = logging.getLogger(__name__)

MAX_PROMPT_CLI_CHARS = 24_000
COOLDOWN_OAUTH_S = 30 * 60.0
RAISON_OAUTH_EXPIREE = "Connexion Claude Code expirée : lance `claude` puis /login"

_ENV_EXCLU_RE = re.compile(r"^(CLAUDE_CODE_.*|ANTHROPIC_.*|CLAUDECODE)$", re.IGNORECASE)

_indisponible_jusqua: float = 0.0
_raison_indisponible: str = ""


def reset_etat_pour_tests() -> None:
    """Réinitialise le cooldown d'authentification Claude CLI pour les tests."""
    global _indisponible_jusqua, _raison_indisponible
    _indisponible_jusqua = 0.0
    _raison_indisponible = ""


def _environnement_propre() -> dict[str, str]:
    """Construit un environnement enfant expurgé des variables Claude/Anthropic."""
    return {k: v for k, v in os.environ.items() if not _ENV_EXCLU_RE.match(k)}


def _trouver_binaire_claude() -> str | None:
    """Localise ``claude`` via ``binaries.trouver``."""
    return binaries.trouver("claude")


def parse_ligne(ligne: str) -> dict[str, Any] | None:
    """Parse une ligne ``stream-json`` émise par Claude Code CLI.

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

    msg_type = donnees.get("type")
    if msg_type == "system" and donnees.get("session_id"):
        return {
            "type": "init",
            "session_id": donnees.get("session_id"),
        }

    if msg_type == "stream_event":
        event = donnees.get("event")
        if not isinstance(event, dict):
            return None
        if event.get("type") == "content_block_delta":
            delta = event.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                contenu = delta.get("text")
                if isinstance(contenu, str) and contenu:
                    return {"type": "delta", "delta": contenu}
        return None

    if msg_type == "result":
        usage = donnees.get("usage") if isinstance(donnees.get("usage"), dict) else {}
        return {
            "type": "result",
            "result": str(donnees.get("result") or ""),
            "session_id": donnees.get("session_id"),
            "is_error": bool(donnees.get("is_error", False)),
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }

    return None


def _marquer_oauth_expire() -> None:
    """Place le moteur ``claude_cli`` en indisponibilité temporaire de 30 minutes."""
    global _indisponible_jusqua, _raison_indisponible
    _indisponible_jusqua = time.monotonic() + COOLDOWN_OAUTH_S
    _raison_indisponible = RAISON_OAUTH_EXPIREE


def _contient_erreur_oauth(texte: str) -> bool:
    """Détecte une erreur d'authentification OAuth expirée dans un message."""
    bas = texte.lower()
    return "oauth" in bas or "authenticate" in bas


class ClaudeEngine:
    """Moteur d'exécution basé sur Claude Code CLI en mode non-interactif."""

    nom = "claude_cli"

    async def disponible(self) -> tuple[bool, str]:
        """Indique si ``claude`` est installé et sans cooldown d'expiration OAuth.

        Returns:
            Tuple ``(disponible, raison)``.
        """
        if time.monotonic() < _indisponible_jusqua:
            return False, _raison_indisponible or RAISON_OAUTH_EXPIREE
        if not _trouver_binaire_claude():
            return False, "Claude Code CLI introuvable dans le PATH"
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Exécute ``claude -p`` dans un thread dédié avec streaming ``stream-json``.

        Args:
            req: Requête LLM complète.
            modele_id: Identifiant catalogue (``cli-sonnet``, ``cli-opus``, ``cli-haiku``).
            session_id: Identifiant de session à reprendre via ``--resume``, ou ``None``.
            on_delta: Coroutine appelée à chaque fragment de texte reçu.

        Returns:
            ``LLMResult`` contenant la réponse et les métadonnées.

        Raises:
            EngineError: En cas d'échec, expiration OAuth ou expiration du délai.
        """
        ok, raison = await self.disponible()
        if not ok:
            raise EngineError(raison, transitoire=False)

        claude_bin = _trouver_binaire_claude()
        if not claude_bin:
            raise EngineError("Claude Code CLI introuvable dans le PATH", transitoire=False)

        _, cli_model, effort_effectif = resoudre_modele(modele_id, req.effort)
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
            config.CLAUDE_SANDBOX.mkdir(parents=True, exist_ok=True)
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)

            fichiers_temp: list[Path] = []
            try:
                prompt_effectif = req.prompt
                systeme_inline = req.systeme

                if req.images:
                    prompt_effectif += (
                        "\n\nFichiers image joints (utilise Read pour les consulter) : "
                        + ", ".join(req.images)
                    )

                # Si le prompt système ou utilisateur dépasse la limite Windows, regroupe dans un fichier.
                systeme_trop_long = len(systeme_inline) > MAX_PROMPT_CLI_CHARS
                if systeme_trop_long:
                    prompt_effectif = f"<<SYSTEME>>\n{systeme_inline}\n<</SYSTEME>>\n\n{prompt_effectif}"
                    systeme_inline = ""

                prompt_fichier = len(prompt_effectif) > MAX_PROMPT_CLI_CHARS
                arg_prompt = prompt_effectif
                if prompt_fichier:
                    nom_prompt = f"prompt-{uuid.uuid4().hex}.md"
                    chemin_prompt = config.CLAUDE_SANDBOX / nom_prompt
                    chemin_prompt.write_text(prompt_effectif, encoding="utf-8")
                    fichiers_temp.append(chemin_prompt)
                    arg_prompt = (
                        f"Lis le fichier {nom_prompt} du dossier courant "
                        "et exécute les instructions qu'il contient."
                    )

                besoin_read = bool(req.images) or prompt_fichier
                cmd = [
                    claude_bin,
                    "-p",
                    "--model",
                    cli_model,
                    "--effort",
                    effort_effectif,
                    "--setting-sources",
                    "project",
                    "--strict-mcp-config",
                    "--tools",
                    "Read" if besoin_read else "",
                    "--output-format",
                    "stream-json",
                    "--verbose",
                    "--include-partial-messages",
                ]
                if systeme_inline:
                    cmd.extend(["--system-prompt", systeme_inline])

                dossiers_ajoutes: set[str] = set()
                for img_str in req.images:
                    dossier = str(Path(img_str).resolve().parent)
                    if dossier not in dossiers_ajoutes:
                        dossiers_ajoutes.add(dossier)
                        cmd.extend(["--add-dir", dossier])
                if prompt_fichier:
                    sandbox_str = str(config.CLAUDE_SANDBOX.resolve())
                    if sandbox_str not in dossiers_ajoutes:
                        cmd.extend(["--add-dir", sandbox_str])

                if session_id:
                    cmd.extend(["--resume", session_id])
                cmd.append(arg_prompt)

                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(config.CLAUDE_SANDBOX),
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

                sess_out: str | None = session_id
                deltas: list[str] = []
                result_event: dict[str, Any] | None = None

                try:
                    if proc.stdout is not None:
                        for ligne in proc.stdout:
                            evt = parse_ligne(ligne)
                            if evt is None:
                                continue
                            etype = evt["type"]
                            if etype == "init" and evt.get("session_id"):
                                sess_out = str(evt["session_id"])
                            elif etype == "delta":
                                delta = str(evt["delta"])
                                deltas.append(delta)
                                loop.call_soon_threadsafe(queue.put_nowait, delta)
                            elif etype == "result":
                                result_event = evt
                                if evt.get("session_id"):
                                    sess_out = str(evt["session_id"])
                    proc.wait()
                finally:
                    timer.cancel()
                    t_err.join(timeout=2.0)

                stderr_text = "".join(stderr_lignes)
                try:
                    (config.LOG_DIR / "claude-last.log").write_text(stderr_text, encoding="utf-8")
                except OSError as exc:
                    logger.warning("Impossible d'écrire claude-last.log : %s", exc)

                if timed_out:
                    raise EngineError(f"Expiration du délai claude ({req.timeout_s}s)", transitoire=True)

                res_text = str((result_event or {}).get("result") or "")
                is_error = bool((result_event or {}).get("is_error", False))

                if _contient_erreur_oauth(stderr_text) or (is_error and _contient_erreur_oauth(res_text)):
                    _marquer_oauth_expire()
                    raise EngineError(RAISON_OAUTH_EXPIREE, transitoire=False)

                if is_error or (proc.returncode != 0 and not result_event and not deltas):
                    msg = res_text or stderr_text.strip() or f"Échec claude (code {proc.returncode})"
                    raise EngineError(msg, transitoire=False)

                texte_final = res_text or "".join(deltas)
                return LLMResult(
                    texte=texte_final,
                    moteur=self.nom,
                    modele=modele_id,
                    session_id=sess_out,
                    input_tokens=int((result_event or {}).get("input_tokens") or 0),
                    output_tokens=int((result_event or {}).get("output_tokens") or 0),
                    duree_s=round(time.monotonic() - debut, 3),
                )
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
