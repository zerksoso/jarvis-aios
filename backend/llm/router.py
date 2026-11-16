"""Routeur LLM intelligent : chaînes de repli, quotas, relances et continuité de session."""

import asyncio
import logging
import sqlite3
import time
import weakref
from typing import Any

from backend import db
from backend.llm import quota
from backend.llm.agy_engine import AgyEngine
from backend.llm.base import DeltaCallback, Engine, EngineError, LLMRequest, LLMResult
from backend.llm.catalog import CATALOGUE, catalogue_public
from backend.llm.claude_engine import ClaudeEngine
from backend.llm.codex_engine import CodexEngine

logger = logging.getLogger(__name__)

SEUIL_QUOTA_MIN = 0.10
DELAIS_RELANCE: tuple[float, ...] = (3.0, 8.0)

CHAINES_REPLI: dict[str, list[str]] = {
    "auto": ["hydrogen", "flash", "claude-sonnet", "argon", "cli-sonnet", "codex"],
    "gemini": ["hydrogen", "flash", "claude-sonnet", "argon", "cli-sonnet"],
    "claude": ["claude-sonnet", "claude-opus", "cli-sonnet", "hydrogen"],
    "chatgpt": ["codex", "gpt-oss", "hydrogen", "claude-sonnet", "cli-sonnet"],
}

_MOTEURS_DEFAUT: dict[str, Engine] = {
    "agy": AgyEngine(),
    "claude_cli": ClaudeEngine(),
    "codex": CodexEngine(),
}
_TEST_ENGINES: dict[str, Engine] | None = None

_SEMAPHORES_PAR_LOOP: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


def set_engines_for_tests(engines: dict[str, Engine] | None) -> None:
    """Injecte des moteurs simulés pour les tests ou réinitialise les moteurs réels.

    Args:
        engines: Dictionnaire ``{nom_backend: Engine}`` ou ``None`` pour réinitialiser.
    """
    global _TEST_ENGINES
    _TEST_ENGINES = engines


def _obtenir_moteurs(engines: dict[str, Engine] | None = None) -> dict[str, Engine]:
    """Retourne le registre de moteurs actif par priorité paramètre > test > défaut."""
    if engines is not None:
        return engines
    if _TEST_ENGINES is not None:
        return _TEST_ENGINES
    return _MOTEURS_DEFAUT


def _obtenir_semaphore(backend: str) -> asyncio.Semaphore:
    """Retourne le sémaphore de concurrence du backend pour la boucle asyncio courante."""
    loop = asyncio.get_running_loop()
    table = _SEMAPHORES_PAR_LOOP.get(loop)
    if table is None:
        table = {
            "agy": asyncio.Semaphore(2),
            "claude_cli": asyncio.Semaphore(1),
            "codex": asyncio.Semaphore(1),
        }
        _SEMAPHORES_PAR_LOOP[loop] = table
    if backend not in table:
        table[backend] = asyncio.Semaphore(1)
    return table[backend]


def _construire_chaine(req: LLMRequest) -> list[str]:
    """Construit la chaîne ordonnée et dédupliquée des modèles à tenter."""
    candidats: list[str] = []
    if req.modele:
        candidats.append(req.modele)
    if req.modele_secours:
        if not candidats:
            repli_defaut = CHAINES_REPLI.get(req.moteur, CHAINES_REPLI["auto"])
            if repli_defaut:
                candidats.append(repli_defaut[0])
        candidats.append(req.modele_secours)
    candidats.extend(CHAINES_REPLI.get(req.moteur, CHAINES_REPLI["auto"]))

    dedup: list[str] = []
    vus: set[str] = set()
    for modele_id in candidats:
        if modele_id not in vus:
            vus.add(modele_id)
            dedup.append(modele_id)
    return dedup


def _lire_session(session_cle: str, modele_id: str) -> str | None:
    """Lit l'identifiant de session enregistré pour ``(session_cle, modele_id)``."""
    try:
        ligne = db.query_one(
            "SELECT session_id FROM sessions_moteur WHERE cle = ? AND modele = ?",
            (session_cle, modele_id),
        )
    except sqlite3.Error:
        return None
    if not ligne:
        return None
    val = ligne.get("session_id")
    return str(val) if val else None


def _sauver_session(session_cle: str, modele_id: str, session_id: str) -> None:
    """Enregistre ou met à jour l'identifiant de session dans ``sessions_moteur``."""
    try:
        db.execute(
            """
            INSERT INTO sessions_moteur (cle, modele, session_id, maj_le)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cle, modele) DO UPDATE SET
                session_id = excluded.session_id,
                maj_le = excluded.maj_le
            """,
            (session_cle, modele_id, session_id, db.maintenant_iso()),
        )
    except sqlite3.Error as exc:
        logger.warning("Impossible de sauvegarder la session moteur : %s", exc)


def _enregistrer_usage(
    moteur: str,
    modele: str,
    input_tokens: int,
    output_tokens: int,
    duree_s: float,
    ok: bool,
    erreur: str | None = None,
) -> None:
    """Journalise chaque tentative d'appel LLM (succès ou échec) dans ``usage_llm``."""
    try:
        db.execute(
            """
            INSERT INTO usage_llm (ts, moteur, modele, input_tokens, output_tokens, duree, ok, erreur)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                db.maintenant_iso(),
                moteur,
                modele,
                int(input_tokens),
                int(output_tokens),
                round(duree_s, 3),
                1 if ok else 0,
                erreur,
            ),
        )
    except sqlite3.Error as exc:
        logger.warning("Impossible d'enregistrer usage_llm : %s", exc)


async def etat_moteurs(engines: dict[str, Engine] | None = None) -> dict[str, dict[str, Any]]:
    """Retourne la disponibilité et la raison associée pour chaque moteur backend.

    Args:
        engines: Registre optionnel de moteurs (pratique en test).

    Returns:
        Dictionnaire ``{backend: {"ok": bool, "raison": str}}``.
    """
    moteurs = _obtenir_moteurs(engines)
    etats: dict[str, dict[str, Any]] = {}
    for nom in ("agy", "claude_cli", "codex"):
        moteur = moteurs.get(nom)
        if moteur is None:
            etats[nom] = {"ok": False, "raison": "Moteur non configuré"}
            continue
        ok, raison = await moteur.disponible()
        etats[nom] = {"ok": bool(ok), "raison": str(raison)}
    return etats


async def _executer_avec_relances(
    moteur: Engine,
    backend: str,
    req: LLMRequest,
    modele_id: str,
    session_id: str | None,
    on_delta: DeltaCallback,
) -> LLMResult:
    """Appelle ``moteur.generer`` avec sémaphore, relances transitoires et suivi usage_llm."""
    sem = _obtenir_semaphore(backend)
    nb_essais = 1 + len(DELAIS_RELANCE)
    session_courante = session_id
    a_retente_sans_session = False

    essai = 0
    while essai < nb_essais:
        t0 = time.monotonic()
        try:
            async with sem:
                res = await moteur.generer(req, modele_id, session_courante, on_delta)
            duree = res.duree_s or (time.monotonic() - t0)
            await asyncio.to_thread(
                _enregistrer_usage,
                backend,
                modele_id,
                res.input_tokens,
                res.output_tokens,
                duree,
                True,
                None,
            )
            return res
        except EngineError as exc:
            duree = time.monotonic() - t0
            await asyncio.to_thread(
                _enregistrer_usage,
                backend,
                modele_id,
                0,
                0,
                duree,
                False,
                exc.message,
            )

            # Si une session reprise échoue de façon non transitoire, on retente une fois sans session.
            if session_courante is not None and not exc.transitoire and not a_retente_sans_session:
                logger.info(
                    "Échec de reprise de session %s sur %s (%s) ; nouvelle tentative sans session.",
                    session_courante,
                    modele_id,
                    exc.message,
                )
                session_courante = None
                a_retente_sans_session = True
                continue

            if exc.transitoire and essai < len(DELAIS_RELANCE):
                attente = DELAIS_RELANCE[essai]
                logger.info(
                    "Erreur transitoire sur %s (%s) ; relance dans %.1fs.",
                    modele_id,
                    exc.message,
                    attente,
                )
                if attente > 0:
                    await asyncio.sleep(attente)
                essai += 1
                continue

            raise

    raise EngineError("Nombre maximal de tentatives atteint", transitoire=False)


async def generer(
    req: LLMRequest,
    on_delta: DeltaCallback = None,
    engines: dict[str, Engine] | None = None,
) -> LLMResult:
    """Exécute une requête LLM en suivant la chaîne de repli, les quotas et les sessions.

    Ne propose que les moteurs installés et connectés (``disponible() == True``). Si aucun
    moteur n'est utilisable, lève ``EngineError(..., code="aucun_moteur")``.

    Args:
        req: Requête LLM décrivant prompt, modèle/moteur, effort et continuité.
        on_delta: Callback asynchrone appelé à chaque delta de texte streamé.
        engines: Moteurs injectés optionnels (pour tests unitaires).

    Returns:
        ``LLMResult`` du premier maillon qui réussit, avec la liste des ``bascules``.

    Raises:
        EngineError: Si tous les maillons de la chaîne ont été sautés ou ont échoué.
    """
    moteurs = _obtenir_moteurs(engines)
    quotas = await quota.lire_quotas()
    chaine = _construire_chaine(req)
    bascules: list[str] = []
    dispos_cache: dict[str, tuple[bool, str]] = {}
    au_moins_un_moteur_dispo = False

    for modele_id in chaine:
        info = CATALOGUE.get(modele_id)
        if info is None:
            bascules.append(f"{modele_id}: modèle inconnu du catalogue")
            continue

        restant_groupe = quota.restant(info.groupe_quota, quotas)
        if restant_groupe is not None and restant_groupe < SEUIL_QUOTA_MIN:
            bascules.append(f"{modele_id}: quota < 10 %")
            continue

        moteur = moteurs.get(info.backend)
        if moteur is None:
            bascules.append(f"{modele_id}: moteur {info.backend} absent")
            continue

        if info.backend not in dispos_cache:
            dispos_cache[info.backend] = await moteur.disponible()
        ok, raison = dispos_cache[info.backend]
        if not ok:
            bascules.append(f"{modele_id}: {raison}")
            continue
        au_moins_un_moteur_dispo = True

        session_id = (
            await asyncio.to_thread(_lire_session, req.session_cle, modele_id)
            if req.session_cle
            else None
        )

        try:
            res = await _executer_avec_relances(
                moteur=moteur,
                backend=info.backend,
                req=req,
                modele_id=modele_id,
                session_id=session_id,
                on_delta=on_delta,
            )
        except EngineError as exc:
            bascules.append(f"{modele_id}: {exc.message}")
            continue

        if req.session_cle and res.session_id:
            await asyncio.to_thread(_sauver_session, req.session_cle, modele_id, res.session_id)

        res.moteur = info.backend
        res.modele = modele_id
        res.bascules = bascules
        return res

    if not au_moins_un_moteur_dispo:
        for backend_nom, moteur in moteurs.items():
            if backend_nom not in dispos_cache:
                dispos_cache[backend_nom] = await moteur.disponible()
            if dispos_cache[backend_nom][0]:
                au_moins_un_moteur_dispo = True
                break

    code_err = None if au_moins_un_moteur_dispo else "aucun_moteur"
    raise EngineError(
        f"Aucun moteur disponible ({'; '.join(bascules) or 'chaîne vide'})",
        transitoire=False,
        code=code_err,
    )


__all__ = [
    "catalogue_public",
    "etat_moteurs",
    "generer",
    "set_engines_for_tests",
]
