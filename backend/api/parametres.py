"""Routes de lecture et mise à jour partielle des paramètres utilisateur."""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from backend import settings_store
from backend.llm.base import Effort

router = APIRouter(prefix="/parametres", tags=["parametres"])


class VoixPatch(BaseModel):
    """Sous-schéma de mise à jour partielle des réglages vocaux."""

    model_config = ConfigDict(extra="allow")

    active: bool | None = None
    nom: str | None = None


class ParametresPatch(BaseModel):
    """Schéma de mise à jour partielle des paramètres Jarvis."""

    model_config = ConfigDict(extra="allow")

    moteur: Literal["auto", "gemini", "claude", "chatgpt"] | None = None
    modele: str | None = None
    effort: Effort | None = None
    verbosite: Literal["normal", "concis", "detaille"] | None = None
    accent: str | None = None
    voix: VoixPatch | None = None
    raccourci: str | None = None
    memoire_auto: bool | None = None


@router.get("")
async def lire_parametres() -> dict[str, Any]:
    """Retourne l'ensemble des paramètres courants (fusionnés avec les défauts).

    Returns:
        Dictionnaire complet des paramètres utilisateur.
    """
    return settings_store.lire()


@router.put("")
async def mettre_a_jour_parametres(body: ParametresPatch) -> dict[str, Any]:
    """Applique une fusion partielle sur ``RUNTIME/parametres.json`` et retourne le résultat.

    Args:
        body: Champs à modifier (tout champ omis ou ``None`` conserve sa valeur actuelle).

    Returns:
        Dictionnaire complet des paramètres mis à jour.
    """
    partiel = body.model_dump(exclude_none=True)
    return settings_store.ecrire(partiel)
