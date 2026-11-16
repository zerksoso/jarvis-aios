"""Lecture et mise en cache des quotas Antigravity (agy) et estimation Claude CLI."""

import asyncio
import copy
import logging
import sqlite3
import time
import weakref
from datetime import datetime, timedelta
from typing import Any

from backend import db
from backend.llm import agy_usage

logger = logging.getLogger(__name__)

CACHE_TTL_S = 600.0
_CACHE_QUOTAS: dict[str, Any] = {}
_CACHE_TS: float = 0.0
_TEST_QUOTAS: dict[str, Any] | None = None
_LOCKS_PAR_LOOP: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _obtenir_lock() -> asyncio.Lock:
    """Retourne un verrou asyncio dédié à la boucle d'événements courante."""
    loop = asyncio.get_running_loop()
    lock = _LOCKS_PAR_LOOP.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS_PAR_LOOP[loop] = lock
    return lock


def set_quotas_for_tests(quotas: dict[str, Any] | None) -> None:
    """Injecte un dictionnaire de quotas simulé pour les tests ou réinitialise l'injection.

    Args:
        quotas: Dictionnaire de quotas simulés, ou ``None`` pour revenir au mode normal.

    Effets de bord:
        Met à jour l'override de test global et réinitialise le cache si ``None``.
    """
    global _TEST_QUOTAS, _CACHE_QUOTAS, _CACHE_TS
    _TEST_QUOTAS = copy.deepcopy(quotas) if quotas is not None else None
    if quotas is None:
        _CACHE_QUOTAS = {}
        _CACHE_TS = 0.0


def estimation_claude_cli() -> dict[str, int]:
    """Calcule la consommation cumulée de ``claude_cli`` sur les 5 dernières heures.

    Returns:
        Dictionnaire avec ``tokens_5h``, ``input_tokens_5h``, ``output_tokens_5h``
        et ``appels_5h``.
    """
    seuil = (datetime.now() - timedelta(hours=5)).isoformat(timespec="seconds")
    try:
        ligne = db.query_one(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COUNT(*) AS appels
            FROM usage_llm
            WHERE moteur = 'claude_cli' AND ts >= ?
            """,
            (seuil,),
        )
    except sqlite3.Error:
        return {"tokens_5h": 0, "input_tokens_5h": 0, "output_tokens_5h": 0, "appels_5h": 0}

    if not ligne:
        return {"tokens_5h": 0, "input_tokens_5h": 0, "output_tokens_5h": 0, "appels_5h": 0}

    inp = int(ligne.get("input_tokens") or 0)
    out = int(ligne.get("output_tokens") or 0)
    return {
        "tokens_5h": inp + out,
        "input_tokens_5h": inp,
        "output_tokens_5h": out,
        "appels_5h": int(ligne.get("appels") or 0),
    }


async def lire_quotas(forcer: bool = False) -> dict[str, Any]:
    """Lit les quotas Antigravity (cache 10 min) augmentés de l'estimation Claude CLI.

    En cas d'échec de ``agy /usage``, retourne le dernier cache valide ou ``{}``.

    Args:
        forcer: Force le rafraîchissement sans tenir compte du TTL de 10 minutes.

    Returns:
        Dictionnaire des quotas ou ``{}`` si aucun cache n'existe.
    """
    global _CACHE_QUOTAS, _CACHE_TS

    if _TEST_QUOTAS is not None:
        return copy.deepcopy(_TEST_QUOTAS)

    maintenant = time.monotonic()
    if not forcer and _CACHE_QUOTAS and (maintenant - _CACHE_TS) < CACHE_TTL_S:
        return copy.deepcopy(_CACHE_QUOTAS)

    # Évite les lancements concurrents de `agy /usage` sur expiration de cache ; recrée si boucle changée.
    try:
        lock = _obtenir_lock()
        async with lock:
            maintenant = time.monotonic()
            if not forcer and _CACHE_QUOTAS and (maintenant - _CACHE_TS) < CACHE_TTL_S:
                return copy.deepcopy(_CACHE_QUOTAS)
            donnees = await asyncio.to_thread(agy_usage.lire_usage_sync, forcer)
    except RuntimeError:
        donnees = await asyncio.to_thread(agy_usage.lire_usage_sync, forcer)

    if donnees is not None:
        copie = copy.deepcopy(donnees)
        copie["claude_cli"] = await asyncio.to_thread(estimation_claude_cli)
        _CACHE_QUOTAS = copie
        _CACHE_TS = time.monotonic()
        return copy.deepcopy(_CACHE_QUOTAS)

    if _CACHE_QUOTAS:
        return copy.deepcopy(_CACHE_QUOTAS)
    return {}


def _bucket_correspond_groupe(nom_bucket: str, bucket: dict[str, Any], groupe: str) -> bool:
    """Détermine si un bucket de quota agy appartient au groupe demandé."""
    groupe_bucket = str(bucket.get("group") or "").lower()
    nom = nom_bucket.lower()
    if groupe == "gemini":
        return (nom.startswith("gemini-") and "argon" not in nom) or groupe_bucket == "gemini"
    if groupe == "3p":
        return nom.startswith("3p-") or groupe_bucket == "3p"
    if groupe == "argon":
        return "argon" in nom or groupe_bucket == "argon"
    return groupe_bucket == groupe.lower()


def restant(groupe: str, quotas: dict[str, Any] | None = None) -> float | None:
    """Retourne le quota minimal restant (``0.0`` à ``1.0``) pour un groupe de modèles.

    Args:
        groupe: Nom du groupe (``"gemini"``, ``"3p"``, ``"argon"``, ``"claude_cli"``).
        quotas: Instantané de quotas à inspecter ; sinon utilise l'état test/cache.

    Returns:
        Ratio restant entre ``0.0`` et ``1.0``, ou ``None`` si inconnu.
    """
    source = quotas if quotas is not None else (_TEST_QUOTAS if _TEST_QUOTAS is not None else _CACHE_QUOTAS)
    if not source:
        return None

    direct = source.get(groupe)
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return float(direct)

    buckets = source.get("buckets")
    if not isinstance(buckets, dict):
        return None

    valeurs: list[float] = []
    for nom_bucket, infos in buckets.items():
        if not isinstance(infos, dict):
            continue
        if _bucket_correspond_groupe(nom_bucket, infos, groupe):
            val = infos.get("remaining")
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                valeurs.append(float(val))

    if not valeurs:
        return None
    return min(valeurs)
