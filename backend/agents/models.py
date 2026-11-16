"""Modèles Pydantic des agents spécialisés, de leurs avatars et des canaux multi-agents."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from backend import db
from backend.llm.base import Effort

FormeAvatar = Literal["sphere", "cube", "diamant", "anneau", "goutte"]
YeuxAvatar = Literal["ronds", "fentes", "points", "visiere"]
EtatAgent = Literal["libre", "travaille", "attend_accord", "fini", "erreur"]

FORMES_VALIDES: tuple[FormeAvatar, ...] = ("sphere", "cube", "diamant", "anneau", "goutte")
YEUX_VALIDES: tuple[YeuxAvatar, ...] = ("ronds", "fentes", "points", "visiere")
ETATS_VALIDES: tuple[EtatAgent, ...] = ("libre", "travaille", "attend_accord", "fini", "erreur")


class _DictAccessModel(BaseModel):
    """Mixin Pydantic autorisant à la fois l'accès par attribut et par clé dictionnaire."""

    def __getitem__(self, cle: str) -> Any:
        """Permet l'accès ``modele['champ']`` compatible avec les dictionnaires."""
        try:
            return getattr(self, cle)
        except AttributeError as exc:
            raise KeyError(cle) from exc

    def __setitem__(self, cle: str, valeur: Any) -> None:
        """Permet l'affectation ``modele['champ'] = valeur``."""
        setattr(self, cle, valeur)

    def __contains__(self, cle: object) -> bool:
        """Vérifie si un champ appartient au modèle."""
        return isinstance(cle, str) and hasattr(self, cle)

    def get(self, cle: str, defaut: Any = None) -> Any:
        """Retourne la valeur d'un champ ou ``defaut`` s'il est absent."""
        return getattr(self, cle, defaut)

    def keys(self) -> Any:
        """Retourne les noms de champs du modèle."""
        return self.model_dump().keys()

    def values(self) -> Any:
        """Retourne les valeurs sérialisées du modèle."""
        return self.model_dump().values()

    def items(self) -> Any:
        """Retourne les paires ``(champ, valeur)`` sérialisées du modèle."""
        return self.model_dump().items()


class Avatar(_DictAccessModel):
    """Configuration visuelle et état temps réel de la mascotte d'un agent."""

    forme: FormeAvatar = "sphere"
    couleur: str = "#22d3ee"
    yeux: YeuxAvatar = "ronds"
    etat: EtatAgent = "libre"


class Agent(_DictAccessModel):
    """Définition complète d'un agent spécialisé persistée dans ``agents/<id>.json``."""

    id: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    titre: str = "Spécialiste"
    avatar: Avatar = Field(default_factory=Avatar)
    instructions: str = ""
    modele_principal: str = "hydrogen"
    effort_raisonnement: Effort = "medium"
    modele_secours: str = "claude-sonnet"
    souvenirs: list[str] = Field(default_factory=list)
    skills_assignes: list[str] = Field(default_factory=list)
    acces_second_cerveau: bool = True
    outils: list[str] = Field(default_factory=list)
    fichiers_generes: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)
    cree_le: str = Field(default_factory=db.maintenant_iso)


class Canal(_DictAccessModel):
    """Définition d'un canal multi-agents persistée dans ``canaux/<id>/canal.json``."""

    id: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    description: str = ""
    membres: list[str] = Field(default_factory=lambda: ["jarvis"])
    coordinateur: str = "jarvis"
    max_tours_par_agent: int = Field(default=2, ge=1, le=10)
    routines: list[str] = Field(default_factory=list)
    cree_le: str = Field(default_factory=db.maintenant_iso)
    memoire_md: str | None = None
