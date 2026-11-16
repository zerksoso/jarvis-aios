"""Routes de lecture et mise à jour partielle des paramètres utilisateur."""

from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend import settings_store
from backend.llm.base import Effort
from backend.ws_hub import hub

router = APIRouter(prefix="/parametres", tags=["parametres"])

_VOIX_CHAMPS_NULLABLES = frozenset({"tts_url", "micro"})


class VoixPatch(BaseModel):
    """Sous-schéma de mise à jour partielle et validée des réglages vocaux."""

    model_config = ConfigDict(extra="allow")

    active: bool | None = None
    nom: str | None = None
    moteur_tts: Literal["edge", "windows", "http"] | None = None
    tts_url: str | None = None
    stt_modele: Literal["large-v3-turbo", "medium", "small"] | None = None
    stt_beam_final: int | None = Field(default=None, ge=1, le=10)
    declenchement: Literal["push", "continu"] | None = None
    micro: str | None = None
    rapide: bool | None = None
    modele_rapide: str | None = None
    mode: Literal["local", "web"] | None = None
    web_service: Literal["chatgpt", "gemini"] | None = None
    web_navigateur: Literal["defaut", "chrome", "edge"] | None = None

    @field_validator("nom", "modele_rapide")
    @classmethod
    def _valider_chaine_non_vide(cls, valeur: str | None) -> str | None:
        """Refuse les chaînes vides ou uniquement blanches lorsqu'elles sont fournies."""
        if valeur is None:
            return None
        propre = valeur.strip()
        if not propre:
            raise ValueError("La valeur ne peut pas être vide.")
        return propre

    @field_validator("tts_url")
    @classmethod
    def _valider_tts_url(cls, valeur: str | None) -> str | None:
        """Valide que ``tts_url`` est vide/null ou une URL HTTP(S) bien formée."""
        if valeur is None:
            return None
        propre = valeur.strip()
        if not propre:
            return None
        parsed = urlparse(propre)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("tts_url doit être une URL http:// ou https:// valide.")
        return propre.rstrip("/")

    @field_validator("micro")
    @classmethod
    def _normaliser_micro(cls, valeur: str | None) -> str | None:
        """Convertit une chaîne vide en ``None`` (microphone par défaut)."""
        if valeur is None:
            return None
        propre = valeur.strip()
        return propre or None


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


def _filtrer_patch(
    donnees: dict[str, Any],
    nullables: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Supprime les clés à ``None`` non explicitement autorisées à être effacées."""
    filtre: dict[str, Any] = {}
    for cle, val in donnees.items():
        if val is None and cle not in nullables:
            continue
        if cle == "voix" and isinstance(val, dict):
            filtre[cle] = _filtrer_patch(val, _VOIX_CHAMPS_NULLABLES)
        else:
            filtre[cle] = val
    return filtre


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
        body: Champs à modifier (tout champ omis conserve sa valeur actuelle ;
            ``voix.tts_url`` et ``voix.micro`` peuvent être explicitement réinitialisés à ``null``).

    Returns:
        Dictionnaire complet des paramètres mis à jour.

    Effets de bord:
        Persiste les paramètres sur disque et diffuse ``parametres_maj`` sur le hub WebSocket.
    """
    brut = body.model_dump(exclude_unset=True)
    partiel = _filtrer_patch(brut)
    mis_a_jour = settings_store.ecrire(partiel)
    await hub.broadcast("parametres_maj", mis_a_jour)
    return mis_a_jour
