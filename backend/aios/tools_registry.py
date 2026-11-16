"""Registre centralisé des outils AIOS, contrôle de risque et exécution encadrée."""

import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

RisqueOutil = Literal["lecture", "ecriture", "action_pc", "irreversible"]

_MAX_RESULTAT_CHARS = 6000
_accord_deja_obtenu: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_accord_deja_obtenu", default=False
)


@dataclass
class Outil:
    """Définition d'un outil appelable par les agents AIOS."""

    nom: str
    description: str
    parametres: dict[str, str]
    risque: RisqueOutil
    fonction: Callable[..., Awaitable[Any]]


@dataclass
class ContexteOutil:
    """Contexte d'exécution transmis à chaque invocation d'outil."""

    agent_id: str
    conversation_id: str | None
    acces_cerveau: bool
    demander_accord: Callable[[str, dict[str, Any], str], Awaitable[bool]]


registre: dict[str, Outil] = {}


def outil(
    nom: str,
    description: str,
    parametres: dict[str, str],
    risque: RisqueOutil,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Décorateur enregistrant une coroutine asynchrone dans le registre d'outils.

    Args:
        nom: Nom canonique de l'outil (ex. ``"cerveau.chercher"``).
        description: Description concise pour les prompts des agents.
        parametres: Mapping ``{nom_param: "type — description"}``.
        risque: Niveau de risque (``lecture``, ``ecriture``, ``action_pc``, ``irreversible``).

    Returns:
        La fonction asynchrone décorée, inchangée.
    """

    def _decorateur(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        registre[nom] = Outil(
            nom=nom,
            description=description,
            parametres=parametres,
            risque=risque,
            fonction=fn,
        )
        return fn

    return _decorateur


def charger_outils() -> dict[str, Outil]:
    """Importe l'ensemble des modules d'outils du socle pour peupler ``registre``.

    Returns:
        Référence vers le dictionnaire global ``registre``.
    """
    from backend.aios.tools import (  # noqa: F401
        agents,
        cartes,
        cerveau,
        ecran,
        memoire,
        scripts,
        skills,
        taches,
        web,
    )

    return registre


def _tronquer_resultat(valeur: Any, limite: int = _MAX_RESULTAT_CHARS) -> Any:
    """Tronque une valeur de retour si sa représentation dépasse ``limite`` caractères."""
    suffixe = "\n… [tronqué]"
    if isinstance(valeur, str):
        if len(valeur) <= limite:
            return valeur
        return valeur[: limite - len(suffixe)] + suffixe

    try:
        serialise = json.dumps(valeur, ensure_ascii=False, default=str)
    except TypeError:
        serialise = str(valeur)

    if len(serialise) <= limite:
        return valeur
    return serialise[: limite - len(suffixe)] + suffixe


async def executer(nom: str, args: dict[str, Any], ctx: ContexteOutil) -> dict[str, Any]:
    """Exécute un outil enregistré avec gestion d'accord utilisateur et troncature.

    Tout outil de risque ``irreversible`` demande d'abord confirmation via
    ``ctx.demander_accord``. En cas de refus, retourne ``{"ok": False, "erreur": "Refusé par l'utilisateur"}``.

    Args:
        nom: Nom de l'outil dans ``registre``.
        args: Arguments nommés à passer à l'outil.
        ctx: Contexte d'autorisation et de traçabilité de l'appel.

    Returns:
        Dictionnaire ``{"ok": bool, "resultat"|"erreur": ..., "duree": float}``.
    """
    if not registre:
        charger_outils()

    t0 = time.monotonic()
    outil_obj = registre.get(nom)
    if outil_obj is None:
        return {"ok": False, "erreur": f"Outil inconnu : {nom}", "duree": 0.0}

    args_propres = dict(args or {})

    if nom == "fichier.supprimer":
        from backend.jarvis_engine import guardrails

        decision_suppression = guardrails.verifier_chemin_suppression(
            str(args_propres.get("chemin") or "")
        )
        if decision_suppression.statut == "interdit":
            return {
                "ok": False,
                "erreur": decision_suppression.raison,
                "duree": round(time.monotonic() - t0, 3),
            }

    if outil_obj.risque == "irreversible":
        resume_args = json.dumps(args_propres, ensure_ascii=False, default=str)[:160]
        accord = await ctx.demander_accord(
            f"Exécuter l'outil {nom} ({resume_args})",
            {"outil": nom, "args": args_propres},
            outil_obj.risque,
        )
        if not accord:
            return {
                "ok": False,
                "erreur": "Refusé par l'utilisateur",
                "duree": round(time.monotonic() - t0, 3),
            }

    token = _accord_deja_obtenu.set(outil_obj.risque == "irreversible")
    try:
        brut = await outil_obj.fonction(ctx, **args_propres)
        return {
            "ok": True,
            "resultat": _tronquer_resultat(brut),
            "duree": round(time.monotonic() - t0, 3),
        }
    except Exception as exc:
        logger.warning("Échec de l'outil %s : %s", nom, exc)
        return {
            "ok": False,
            "erreur": str(exc),
            "duree": round(time.monotonic() - t0, 3),
        }
    finally:
        _accord_deja_obtenu.reset(token)


def accord_deja_obtenu() -> bool:
    """Indique si l'exécution courante provient de ``executer()`` après accord validé."""
    return _accord_deja_obtenu.get(False)


def description_pour_prompt(noms: list[str]) -> str:
    """Formate la documentation des outils autorisés pour l'injecter dans un prompt agent.

    Args:
        noms: Liste des noms d'outils autorisés pour l'agent.

    Returns:
        Texte structuré décrivant chaque outil disponible et ses paramètres.
    """
    if not registre:
        charger_outils()

    blocs: list[str] = []
    for nom in noms:
        outil_obj = registre.get(nom)
        if outil_obj is None:
            continue
        params_txt = ", ".join(f"{k}: {v}" for k, v in outil_obj.parametres.items())
        blocs.append(
            f"- {outil_obj.nom} [risque: {outil_obj.risque}] : {outil_obj.description}\n"
            f"  Paramètres : {params_txt or 'aucun'}"
        )
    return "\n".join(blocs)
