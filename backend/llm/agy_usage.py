"""Lecture et cache disque des quotas Antigravity via ``agy -p /usage --output-format json``."""

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend import config
from backend.llm import binaries
from backend.llm.jsonparse import extraire_json

logger = logging.getLogger(__name__)

CACHE_TTL_S = 600.0
TIMEOUT_S = 30.0

_ENV_EXCLU_RE = re.compile(
    r"^(GEMINI_API_KEY|GOOGLE_API_KEY|GOOGLE_GENAI_API_KEY|GOOGLE_GENERATIVE_AI_API_KEY|"
    r"ANTIGRAVITY_API_KEY|ANTIGRAVITY_TOKEN|GOOGLE_APPLICATION_CREDENTIALS|"
    r"GOOGLE_CLOUD_PROJECT|GOOGLE_GENAI_USE_VERTEXAI|VERTEX_.*|.*_GEMINI_KEY)$",
    re.IGNORECASE,
)


def chemin_cache() -> Path:
    """Retourne le chemin dynamique vers ``ROOT/runtime/agy-quota.json``."""
    return config.RUNTIME / "agy-quota.json"


def _environnement_propre() -> dict[str, str]:
    """Construit un environnement enfant expurgé des clés API Google/Gemini."""
    return {k: v for k, v in os.environ.items() if not _ENV_EXCLU_RE.match(k)}


def _extraire_groups(payload: dict[str, Any]) -> list[Any] | None:
    """Extrait la liste ``groups`` depuis les structures JSON possibles de ``agy /usage``."""
    command = payload.get("command")
    if isinstance(command, dict):
        cmd_data = command.get("data")
        if isinstance(cmd_data, dict) and isinstance(cmd_data.get("groups"), list):
            return cmd_data["groups"]

    data = payload.get("data")
    if isinstance(data, dict):
        inner_cmd = data.get("command")
        if isinstance(inner_cmd, dict):
            inner_data = inner_cmd.get("data")
            if isinstance(inner_data, dict) and isinstance(inner_data.get("groups"), list):
                return inner_data["groups"]
        if isinstance(data.get("groups"), list):
            return data["groups"]

    if isinstance(payload.get("groups"), list):
        return payload["groups"]

    return None


def parser_usage_json(brut: str | dict[str, Any]) -> dict[str, Any] | None:
    """Convertit la sortie JSON de ``agy -p /usage --output-format json`` en instantané normalisé.

    Lit ``data.command.data.groups[].buckets[]`` (``id``, ``remaining_fraction``,
    ``window``, ``reset_time``) et produit le format historique ``{"updatedAt", "buckets"}``.

    Args:
        brut: Chaîne JSON brute émise sur stdout ou dictionnaire déjà décodé.

    Returns:
        Instantané ``{"updatedAt": iso, "buckets": {id: {"group", "window", "remaining", "resetsAt"}}}``,
        ou ``None`` si la structure attendue est absente ou invalide.
    """
    payload: dict[str, Any] | None
    if isinstance(brut, dict):
        payload = brut
    elif isinstance(brut, str):
        texte = brut.strip()
        if not texte:
            return None
        try:
            decode = json.loads(texte)
            payload = decode if isinstance(decode, dict) else None
        except json.JSONDecodeError:
            payload = extraire_json(texte)
    else:
        return None

    if not isinstance(payload, dict):
        return None

    groups = _extraire_groups(payload)
    if groups is None:
        return None

    buckets: dict[str, dict[str, Any]] = {}
    for groupe in groups:
        if not isinstance(groupe, dict):
            continue
        nom_groupe_defaut = str(
            groupe.get("id") or groupe.get("group") or groupe.get("name") or groupe.get("label") or ""
        ).strip()
        liste_buckets = groupe.get("buckets")
        if not isinstance(liste_buckets, list):
            continue

        for item in liste_buckets:
            if not isinstance(item, dict):
                continue
            bucket_id = str(item.get("id") or "").strip()
            if not bucket_id:
                continue

            rem_brut = (
                item.get("remaining_fraction")
                if "remaining_fraction" in item
                else item.get("remaining")
            )
            if isinstance(rem_brut, bool) or rem_brut is None:
                continue
            try:
                remaining = float(rem_brut)
            except (TypeError, ValueError):
                continue

            nom_groupe = str(item.get("group") or nom_groupe_defaut).strip()
            window = str(item.get("window") or "").strip()
            reset_brut = item.get("reset_time") if "reset_time" in item else item.get("resetsAt")
            resets_at = (
                str(reset_brut).strip()
                if isinstance(reset_brut, str) and reset_brut.strip()
                else None
            )

            buckets[bucket_id] = {
                "group": nom_groupe,
                "window": window,
                "remaining": remaining,
                "resetsAt": resets_at,
            }

    updated_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "updatedAt": updated_at,
        "buckets": buckets,
    }


def _lire_cache_disque(ignorer_ttl: bool = False) -> dict[str, Any] | None:
    """Lit ``ROOT/runtime/agy-quota.json`` s'il existe et respecte le TTL."""
    chemin = chemin_cache()
    if not chemin.is_file():
        return None

    try:
        if not ignorer_ttl:
            age_s = time.time() - chemin.stat().st_mtime
            if age_s < 0 or age_s >= CACHE_TTL_S:
                return None
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.debug("Cache quota disque illisible (%s).", exc)
        return None

    if not isinstance(donnees, dict) or not isinstance(donnees.get("buckets"), dict):
        return None
    return donnees


def _ecrire_cache_disque(donnees: dict[str, Any]) -> None:
    """Écrit l'instantané de quotas dans ``ROOT/runtime/agy-quota.json`` de façon atomique."""
    chemin = chemin_cache()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_name(f"{chemin.stem}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(donnees, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(chemin)
    except OSError as exc:
        logger.warning("Impossible d'écrire %s : %s", chemin, exc)
    finally:
        tmp.unlink(missing_ok=True)


def _executer_agy_usage_sync() -> dict[str, Any] | None:
    """Exécute ``agy -p /usage --output-format json`` (timeout 30 s, sans console) et parse le JSON."""
    agy_bin = binaries.trouver("agy")
    if not agy_bin:
        logger.debug("CLI agy introuvable pour la lecture des quotas.")
        return None

    config.AGY_SANDBOX.mkdir(parents=True, exist_ok=True)
    cmd = [agy_bin, "-p", "/usage", "--output-format", "json"]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
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
    except OSError as exc:
        logger.warning("Échec de lancement de agy /usage : %s", exc)
        return None

    try:
        stdout, stderr = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
        logger.warning("Expiration du délai agy /usage (%.0fs).", TIMEOUT_S)
        return None

    if proc.returncode != 0:
        logger.warning("agy /usage a retourné %s : %s", proc.returncode, (stderr or "").strip())
        return None

    donnees = parser_usage_json(stdout or "")
    if donnees is None:
        logger.warning("Sortie JSON invalide ou incomplète de agy /usage.")
        return None
    return donnees


def lire_usage_sync(forcer: bool = False) -> dict[str, Any] | None:
    """Lit les quotas Antigravity avec cache disque ``ROOT/runtime/agy-quota.json`` (TTL 600 s).

    Args:
        forcer: Ignore la fraîcheur du cache disque et interroge ``agy /usage``.

    Returns:
        Instantané ``{"updatedAt", "buckets"}``, ou dernier cache valide en cas d'échec,
        ou ``None`` si aucune donnée n'est disponible.

    Effets de bord:
        Peut lancer ``agy -p /usage --output-format json`` et mettre à jour ``agy-quota.json``.
    """
    if not forcer:
        cache_frais = _lire_cache_disque(ignorer_ttl=False)
        if cache_frais is not None:
            return cache_frais

    donnees = _executer_agy_usage_sync()
    if donnees is not None:
        _ecrire_cache_disque(donnees)
        return donnees

    return _lire_cache_disque(ignorer_ttl=True)


async def lire_usage(forcer: bool = False) -> dict[str, Any] | None:
    """Version asynchrone de ``lire_usage_sync`` exécutée dans un thread dédié.

    Args:
        forcer: Ignore la fraîcheur du cache disque et interroge ``agy /usage``.

    Returns:
        Instantané ``{"updatedAt", "buckets"}`` ou ``None``.
    """
    return await asyncio.to_thread(lire_usage_sync, forcer)
