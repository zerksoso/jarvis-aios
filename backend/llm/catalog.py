"""Catalogue des modèles LLM exposés à l'UI et correspondance avec les backends CLI."""

from dataclasses import dataclass
from typing import Any, Literal

from backend.llm.base import Effort

MoteurUI = Literal["gemini", "claude", "chatgpt"]
BackendMoteur = Literal["agy", "claude_cli", "codex"]
GroupeQuota = Literal["gemini", "3p", "argon", "claude_cli", "codex"]


@dataclass(frozen=True, slots=True)
class ModeleCatalogue:
    """Description immuable d'un modèle disponible dans le catalogue Jarvis."""

    id: str
    libelle: str
    moteur_ui: MoteurUI
    backend: BackendMoteur
    groupe_quota: GroupeQuota
    agy_ids: dict[str, str]
    efforts: tuple[Effort, ...]


CATALOGUE: dict[str, ModeleCatalogue] = {
    "hydrogen": ModeleCatalogue(
        id="hydrogen",
        libelle="Gemini 4 Hydrogen",
        moteur_ui="gemini",
        backend="agy",
        groupe_quota="gemini",
        agy_ids={
            "low": "gemini-4-hydrogen-low",
            "medium": "gemini-4-hydrogen-medium",
            "high": "gemini-4-hydrogen-high",
            "xhigh": "gemini-4-hydrogen-xhigh",
        },
        efforts=("low", "medium", "high", "xhigh"),
    ),
    "flash": ModeleCatalogue(
        id="flash",
        libelle="Gemini 3.8 Flash",
        moteur_ui="gemini",
        backend="agy",
        groupe_quota="gemini",
        agy_ids={
            "low": "gemini-3.8-flash-low",
            "medium": "gemini-3.8-flash-medium",
            "high": "gemini-3.8-flash-high",
            "xhigh": "gemini-3.8-flash-high",
        },
        efforts=("low", "medium", "high"),
    ),
    "argon": ModeleCatalogue(
        id="argon",
        libelle="Gemini 4 Argon (réserve)",
        moteur_ui="gemini",
        backend="agy",
        groupe_quota="argon",
        agy_ids={
            "low": "gemini-4-argon-low",
            "medium": "gemini-4-argon-medium",
            "high": "gemini-4-argon-high",
            "xhigh": "gemini-4-argon-high",
        },
        efforts=("low", "medium", "high"),
    ),
    "claude-sonnet": ModeleCatalogue(
        id="claude-sonnet",
        libelle="Claude Sonnet 5.5 (Antigravity)",
        moteur_ui="claude",
        backend="agy",
        groupe_quota="3p",
        agy_ids={
            "low": "claude-sonnet-5-5-low",
            "medium": "claude-sonnet-5-5-medium",
            "high": "claude-sonnet-5-5-high",
            "xhigh": "claude-sonnet-5-5-high",
        },
        efforts=("low", "medium", "high"),
    ),
    "claude-opus": ModeleCatalogue(
        id="claude-opus",
        libelle="Claude Opus 5.5 (Antigravity)",
        moteur_ui="claude",
        backend="agy",
        groupe_quota="3p",
        agy_ids={
            "low": "claude-opus-5-5-low",
            "medium": "claude-opus-5-5-medium",
            "high": "claude-opus-5-5-high",
            "xhigh": "claude-opus-5-5-high",
        },
        efforts=("low", "medium", "high"),
    ),
    "gpt-oss": ModeleCatalogue(
        id="gpt-oss",
        libelle="GPT-OSS 120B (Antigravity)",
        moteur_ui="chatgpt",
        backend="agy",
        groupe_quota="3p",
        agy_ids={
            "low": "gpt-oss-120b-medium",
            "medium": "gpt-oss-120b-medium",
            "high": "gpt-oss-120b-medium",
            "xhigh": "gpt-oss-120b-medium",
        },
        efforts=("medium",),
    ),
    "codex": ModeleCatalogue(
        id="codex",
        libelle="ChatGPT / Codex (Codex CLI)",
        moteur_ui="chatgpt",
        backend="codex",
        groupe_quota="codex",
        agy_ids={
            "low": "default",
            "medium": "default",
            "high": "default",
            "xhigh": "default",
        },
        efforts=("low", "medium", "high"),
    ),
    "cli-sonnet": ModeleCatalogue(
        id="cli-sonnet",
        libelle="Claude Sonnet (Claude Code CLI)",
        moteur_ui="claude",
        backend="claude_cli",
        groupe_quota="claude_cli",
        agy_ids={
            "low": "sonnet",
            "medium": "sonnet",
            "high": "sonnet",
            "xhigh": "sonnet",
        },
        efforts=("low", "medium", "high"),
    ),
    "cli-opus": ModeleCatalogue(
        id="cli-opus",
        libelle="Claude Opus (Claude Code CLI)",
        moteur_ui="claude",
        backend="claude_cli",
        groupe_quota="claude_cli",
        agy_ids={
            "low": "opus",
            "medium": "opus",
            "high": "opus",
            "xhigh": "opus",
        },
        efforts=("low", "medium", "high"),
    ),
    "cli-haiku": ModeleCatalogue(
        id="cli-haiku",
        libelle="Claude Haiku (Claude Code CLI)",
        moteur_ui="claude",
        backend="claude_cli",
        groupe_quota="claude_cli",
        agy_ids={
            "low": "haiku",
            "medium": "haiku",
            "high": "haiku",
            "xhigh": "haiku",
        },
        efforts=("low", "medium", "high"),
    ),
}


def resoudre_modele(modele_id: str, effort: Effort) -> tuple[ModeleCatalogue | None, str, Effort]:
    """Résout l'identifiant technique backend et l'effort effectif d'un modèle du catalogue.

    Args:
        modele_id: Identifiant catalogue (ex. ``"hydrogen"``) ou identifiant CLI brut.
        effort: Niveau d'effort souhaité.

    Returns:
        Tuple ``(entrée_catalogue_ou_None, id_modele_backend, effort_effectif)``.
    """
    entree = CATALOGUE.get(modele_id)
    if entree is None:
        effort_repli: Effort = "high" if effort == "xhigh" else effort
        return None, modele_id, effort_repli

    effort_effectif: Effort = effort
    if effort_effectif not in entree.efforts:
        effort_effectif = "high" if effort == "xhigh" and "high" in entree.efforts else entree.efforts[0]

    id_backend = entree.agy_ids.get(effort_effectif) or next(iter(entree.agy_ids.values()))
    return entree, id_backend, effort_effectif


def catalogue_public() -> list[dict[str, Any]]:
    """Retourne la liste des modèles du catalogue formatée pour l'API et le frontend.

    Returns:
        Liste de dictionnaires contenant ``id``, ``libelle``, ``moteur_ui``,
        ``backend``, ``groupe_quota`` et ``efforts``.
    """
    return [
        {
            "id": m.id,
            "libelle": m.libelle,
            "moteur_ui": m.moteur_ui,
            "backend": m.backend,
            "groupe_quota": m.groupe_quota,
            "efforts": list(m.efforts),
        }
        for m in CATALOGUE.values()
    ]
