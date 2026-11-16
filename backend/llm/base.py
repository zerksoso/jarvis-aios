"""Contrats de données, erreurs et protocole d'interface des moteurs LLM."""

from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

from pydantic import BaseModel, Field

Effort = Literal["low", "medium", "high", "xhigh"]


class LLMRequest(BaseModel):
    """Requête normalisée envoyée au routeur de moteurs LLM."""

    prompt: str
    systeme: str = ""
    moteur: Literal["auto", "gemini", "claude", "chatgpt"] = "auto"
    modele: str | None = None
    modele_secours: str | None = None
    effort: Effort = "medium"
    session_cle: str | None = None
    images: list[str] = Field(default_factory=list)
    timeout_s: int = 180


class LLMResult(BaseModel):
    """Résultat complet d'une génération LLM incluant métadonnées et bascules."""

    texte: str
    moteur: str
    modele: str
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    duree_s: float = 0.0
    bascules: list[str] = Field(default_factory=list)


class EngineError(Exception):
    """Erreur levée par un moteur LLM avec indicateur transitoire et code optionnel."""

    def __init__(
        self,
        message: str = "",
        transitoire: bool = False,
        code: str | None = None,
    ) -> None:
        """Initialise l'erreur moteur.

        Args:
            message: Description lisible de l'échec.
            transitoire: ``True`` si une relance sur le même maillon est pertinente.
            code: Code machine optionnel (ex. ``"aucun_moteur"``).
        """
        super().__init__(message)
        self.transitoire = transitoire
        self.message = message
        self.code = code


DeltaCallback = Callable[[str], Awaitable[None]] | None


class Engine(Protocol):
    """Protocole structurel commun à tous les moteurs d'exécution LLM."""

    nom: str

    async def disponible(self) -> tuple[bool, str]:
        """Vérifie si le moteur est prêt à recevoir des requêtes."""
        ...

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Exécute une génération LLM et diffuse les deltas via ``on_delta``."""
        ...
