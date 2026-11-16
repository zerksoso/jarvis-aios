"""Session persistante (chaude) du CLI ``agy`` en mode ``stream-json`` bidirectionnel."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
import threading
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from backend import config
from backend.llm import agy_engine, binaries
from backend.llm.base import EngineError
from backend.llm.catalog import resoudre_modele

logger = logging.getLogger(__name__)

MODELE_RAPIDE_DEFAUT = "gemini-4-hydrogen-low"
DELAI_INACTIVITE_DEFAUT_S = 600.0
TIMEOUT_TOUR_DEFAUT_S = 120.0


def _normaliser_commande_binaire(executable: str | Sequence[str] | None) -> list[str]:
    """Résout le préfixe de commande exécutable pour ``agy`` (ou script de test Python)."""
    if executable is None:
        trouve = binaries.trouver("agy")
        if not trouve:
            raise EngineError("CLI agy introuvable", transitoire=False)
        return [trouve]

    if isinstance(executable, str):
        propre = executable.strip()
        if not propre:
            raise EngineError("Chemin agy vide", transitoire=False)
        if Path(propre).suffix.lower() == ".py":
            return [sys.executable, propre]
        return [propre]

    cmd = [str(part) for part in executable if str(part)]
    if not cmd:
        raise EngineError("Commande agy vide", transitoire=False)
    return cmd


def _ecrire_stdin_sync(proc: subprocess.Popen[str], ligne: str) -> None:
    """Écrit une ligne NDJSON sur stdin du sous-processus et force le vidage tampon."""
    if proc.stdin is None:
        raise BrokenPipeError("stdin du processus agy indisponible")
    proc.stdin.write(ligne)
    proc.stdin.flush()


def _arreter_processus_sync(
    proc: subprocess.Popen[str],
    threads: tuple[threading.Thread, ...],
) -> None:
    """Ferme proprement un sous-processus ``agy`` et attend ses threads de lecture."""
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass

    if proc.poll() is None:
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    for thread in threads:
        if thread.is_alive():
            thread.join(timeout=1.0)


class SessionAgyChaude:
    """Gère un processus ``agy`` gardé ouvert en ``stream-json`` pour la voie vocale rapide.

    Maintient un processus ``agy --input-format stream-json --output-format stream-json``
    sans fenêtre console et avec environnement expurgé des clés API. Sérialise les tours
    (un seul tour à la fois), redémarre automatiquement si le processus s'interrompt et
    s'arrête automatiquement après 10 minutes d'inactivité.
    """

    def __init__(
        self,
        *,
        modele: str = MODELE_RAPIDE_DEFAUT,
        inactivite_s: float = DELAI_INACTIVITE_DEFAUT_S,
        timeout_tour_s: float = TIMEOUT_TOUR_DEFAUT_S,
        executable: str | Sequence[str] | None = None,
    ) -> None:
        """Initialise une session ``agy`` chaude sans lancer le processus immédiatement.

        Args:
            modele: Identifiant de modèle catalogue ou technique ``agy`` (défaut ``gemini-4-hydrogen-low``).
            inactivite_s: Délai d'inactivité en secondes avant arrêt automatique (défaut 600 s).
            timeout_tour_s: Délai maximal d'attente d'un tour complet avant erreur.
            executable: Surcharge de binaire/commande (utile pour les tests unitaires).
        """
        modele_brut = (modele or MODELE_RAPIDE_DEFAUT).strip() or MODELE_RAPIDE_DEFAUT
        _, modele_resolu, _ = resoudre_modele(modele_brut, "low")
        self._modele = modele_resolu
        self._inactivite_s = float(inactivite_s)
        self._timeout_tour_s = float(timeout_tour_s)
        self._executable = executable

        self._tour_lock = asyncio.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._threads: tuple[threading.Thread, ...] = ()
        self._stderr_lignes: list[str] = []
        self._generation = 0
        self._queue: asyncio.Queue[tuple[int, dict[str, Any] | None]] | None = None
        self._tache_inactivite: asyncio.Task[None] | None = None
        self._avec_projet = agy_engine._lire_utiliser_projet()
        self._conversation_id: str | None = None
        self._tours_sur_processus = 0

    @property
    def modele(self) -> str:
        """Identifiant technique de modèle passé à ``agy --model``."""
        return self._modele

    @property
    def est_active(self) -> bool:
        """Indique si le sous-processus ``agy`` est actuellement vivant."""
        return self._proc is not None and self._proc.poll() is None

    @property
    def tours_sur_processus(self) -> int:
        """Nombre de tours réussis sur le processus courant (0 avant le 1er tour ou après redémarrage)."""
        return self._tours_sur_processus

    @property
    def conversation_id(self) -> str | None:
        """Identifiant de conversation renvoyé par ``agy`` (``init`` / ``result``), ou ``None``."""
        return self._conversation_id

    def _annuler_inactivite(self) -> None:
        """Annule le minuteur d'inactivité en cours s'il ne s'agit pas de la tâche courante."""
        tache = self._tache_inactivite
        self._tache_inactivite = None
        if tache is not None and not tache.done() and tache is not asyncio.current_task():
            tache.cancel()

    def _planifier_inactivite(self) -> None:
        """Programme l'arrêt automatique du processus après ``inactivite_s`` secondes."""
        self._annuler_inactivite()
        if self._inactivite_s <= 0 or not self.est_active:
            return
        self._tache_inactivite = asyncio.create_task(self._attendre_et_arreter_inactivite())

    async def _attendre_et_arreter_inactivite(self) -> None:
        """Attend l'expiration du délai d'inactivité puis arrête la session si libre."""
        try:
            await asyncio.sleep(self._inactivite_s)
        except asyncio.CancelledError:
            return
        if self._tour_lock.locked():
            return
        logger.info("Arrêt de la session agy chaude après %.0fs d'inactivité.", self._inactivite_s)
        await self.arreter()

    async def _arreter_processus_courant(self) -> None:
        """Stoppe le sous-processus actif et réinitialise les compteurs associés."""
        proc = self._proc
        threads = self._threads
        self._proc = None
        self._threads = ()
        self._tours_sur_processus = 0
        self._conversation_id = None
        if proc is not None:
            await asyncio.to_thread(_arreter_processus_sync, proc, threads)

    async def _demarrer_sans_verrou(self) -> None:
        """Démarre le sous-processus ``agy`` en mode ``stream-json`` (appelé sous ``_tour_lock``)."""
        if self.est_active:
            return

        await self._arreter_processus_courant()

        if agy_engine._desactive_facturation:
            raise EngineError("Moteur agy désactivé (facturation API détectée)", transitoire=False)

        cmd = _normaliser_commande_binaire(self._executable)
        if self._avec_projet:
            cmd.extend(["--project", agy_engine.PROJET_DEFAUT])
        cmd.extend(
            [
                "--add-dir",
                str(config.AGY_SANDBOX),
                "--sandbox",
                "--disable-slash-commands",
                "--model",
                self._modele,
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--print",
                "",
            ]
        )

        config.AGY_SANDBOX.mkdir(parents=True, exist_ok=True)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(config.AGY_SANDBOX),
                env=agy_engine._environnement_propre(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise EngineError(f"Impossible de démarrer agy : {exc}", transitoire=False) from exc

        self._generation += 1
        gen = self._generation
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[int, dict[str, Any] | None]] = asyncio.Queue()
        self._queue = queue
        self._proc = proc
        self._stderr_lignes = []
        self._tours_sur_processus = 0
        self._conversation_id = None

        def _pousser(evt: dict[str, Any] | None) -> None:
            if not loop.is_closed():
                loop.call_soon_threadsafe(queue.put_nowait, (gen, evt))

        def _lire_stdout() -> None:
            if proc.stdout is not None:
                try:
                    for ligne in proc.stdout:
                        evt = agy_engine.parse_ligne(ligne)
                        if evt is not None:
                            _pousser(evt)
                except OSError:
                    pass
            _pousser(None)

        def _lire_stderr() -> None:
            if proc.stderr is None:
                return
            try:
                for ligne_err in proc.stderr:
                    self._stderr_lignes.append(ligne_err)
                    if len(self._stderr_lignes) > 200:
                        del self._stderr_lignes[: len(self._stderr_lignes) - 200]
                    try:
                        agy_engine._verifier_facturation(ligne_err)
                    except EngineError as exc:
                        _pousser({"type": "_facturation_error", "error": exc.message})
            except OSError:
                pass

        t_out = threading.Thread(target=_lire_stdout, daemon=True, name=f"agy-warm-out-{gen}")
        t_err = threading.Thread(target=_lire_stderr, daemon=True, name=f"agy-warm-err-{gen}")
        t_out.start()
        t_err.start()
        self._threads = (t_out, t_err)

    async def demarrer(self) -> None:
        """Démarre (ou réutilise) le processus ``agy`` chaud et arme le délai d'inactivité.

        Raises:
            EngineError: Si ``agy`` est introuvable, désactivé ou échoue au lancement.

        Effets de bord:
            Lance un processus ``agy`` sans fenêtre console et deux threads de lecture.
        """
        async with self._tour_lock:
            await self._demarrer_sans_verrou()
            self._planifier_inactivite()

    async def arreter(self) -> None:
        """Arrête proprement le processus ``agy`` chaud et annule le minuteur d'inactivité.

        Effets de bord:
            Ferme stdin, termine/tue le processus si nécessaire et libère les ressources.
        """
        self._annuler_inactivite()
        await self._arreter_processus_courant()

    async def envoyer(self, texte: str) -> AsyncIterator[str]:
        """Envoie un tour utilisateur NDJSON et génère les deltas texte jusqu'à ``result``.

        Sérialise les tours via un verrou exclusif. Redémarre automatiquement le processus
        s'il est mort avant le tour ou s'interrompt avant d'avoir émis un delta.

        Args:
            texte: Message utilisateur ou prompt complet du premier tour.

        Yields:
            Fragments textuels ``delta`` émis par ``step_update`` (ou ``result.response`` si absent).

        Raises:
            EngineError: Si ``agy`` échoue de manière irrécupérable ou expire.

        Effets de bord:
            Écrit une ligne NDJSON sur stdin, démarre/redémarre ``agy`` si besoin,
            et réinitialise le minuteur d'inactivité à la fin du tour.
        """
        if not texte or not texte.strip():
            return

        payload = json.dumps(
            {"event": "user", "message": {"role": "user", "content": texte}},
            ensure_ascii=False,
        ) + "\n"

        async with self._tour_lock:
            self._annuler_inactivite()
            try:
                essais_redemarrage = 0
                repli_projet_tente = False

                while True:
                    if not self.est_active:
                        await self._demarrer_sans_verrou()

                    proc = self._proc
                    queue = self._queue
                    gen_courante = self._generation
                    if proc is None or queue is None:
                        raise EngineError("Session agy chaude non initialisée", transitoire=False)

                    try:
                        await asyncio.to_thread(_ecrire_stdin_sync, proc, payload)
                    except OSError as exc:
                        await self._arreter_processus_courant()
                        if essais_redemarrage < 1:
                            essais_redemarrage += 1
                            logger.info("Processus agy fermé avant écriture (%s) ; redémarrage.", exc)
                            continue
                        raise EngineError(f"Échec d'écriture vers agy : {exc}", transitoire=True) from exc

                    deltas_emis = False
                    doit_redemarrer = False
                    debut_tour = asyncio.get_running_loop().time()

                    while True:
                        restant_s = self._timeout_tour_s - (asyncio.get_running_loop().time() - debut_tour)
                        if restant_s <= 0:
                            await self._arreter_processus_courant()
                            raise EngineError(
                                f"Expiration du tour agy chaud ({int(self._timeout_tour_s)}s)",
                                transitoire=True,
                            )

                        try:
                            gen_evt, evt = await asyncio.wait_for(queue.get(), timeout=restant_s)
                        except TimeoutError as exc:
                            await self._arreter_processus_courant()
                            raise EngineError(
                                f"Expiration du tour agy chaud ({int(self._timeout_tour_s)}s)",
                                transitoire=True,
                            ) from exc

                        if gen_evt != gen_courante:
                            continue

                        if evt is None:
                            stderr_txt = "".join(self._stderr_lignes)
                            await self._arreter_processus_courant()
                            agy_engine._verifier_facturation(stderr_txt)

                            if not deltas_emis and self._avec_projet and not repli_projet_tente:
                                if agy_engine._est_erreur_projet_absent(stderr_txt):
                                    self._avec_projet = False
                                    agy_engine._ecrire_utiliser_projet(False)
                                    repli_projet_tente = True
                                    doit_redemarrer = True
                                    break

                            if not deltas_emis and essais_redemarrage < 1:
                                essais_redemarrage += 1
                                doit_redemarrer = True
                                logger.info("Processus agy interrompu pendant le tour ; redémarrage auto.")
                                break

                            msg = stderr_txt.strip() or "Processus agy terminé avant la fin du tour"
                            raise EngineError(msg, transitoire=True)

                        etype = evt.get("type")
                        if etype == "_facturation_error":
                            await self._arreter_processus_courant()
                            raise EngineError(
                                str(evt.get("error") or "facturation API détectée"),
                                transitoire=False,
                            )

                        if etype == "init":
                            if evt.get("conversation_id"):
                                self._conversation_id = str(evt["conversation_id"])
                            continue

                        if etype == "delta":
                            delta = str(evt.get("delta") or "")
                            if delta:
                                deltas_emis = True
                                yield delta
                            continue

                        if etype == "result":
                            if evt.get("conversation_id"):
                                self._conversation_id = str(evt["conversation_id"])

                            err_res = str(evt.get("error") or "")
                            if err_res:
                                agy_engine._verifier_facturation(err_res)

                            status = str(evt.get("status") or "")
                            if status == "ERROR":
                                if (
                                    not deltas_emis
                                    and self._avec_projet
                                    and not repli_projet_tente
                                    and agy_engine._est_erreur_projet_absent(err_res)
                                ):
                                    self._avec_projet = False
                                    agy_engine._ecrire_utiliser_projet(False)
                                    repli_projet_tente = True
                                    doit_redemarrer = True
                                    await self._arreter_processus_courant()
                                    break
                                raise EngineError(
                                    err_res or "Erreur renvoyée par agy en session chaude",
                                    transitoire=False,
                                )

                            if not deltas_emis:
                                reponse = str(evt.get("response") or "")
                                if reponse:
                                    yield reponse

                            self._tours_sur_processus += 1
                            return

                    if doit_redemarrer:
                        continue
            finally:
                if self.est_active:
                    self._planifier_inactivite()
