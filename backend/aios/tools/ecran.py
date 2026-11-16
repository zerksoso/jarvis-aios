"""Outils AIOS d'observation écran, contrôle PC sécurisé, mode Professeur et suppression corbeille."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from send2trash import send2trash

from backend import config
from backend.aios.tools_registry import ContexteOutil, accord_deja_obtenu, outil
from backend.jarvis_engine import guardrails, hands, teacher, uia, vision


@outil(
    nom="ecran.capturer",
    description="Prend une capture d'écran (écran principal par défaut) et renvoie chemin et dimensions.",
    parametres={
        "ecran": "int — numéro d'écran (1 = principal, 0 = bureau virtuel, optionnel)",
        "largeur_max": "int — largeur maximale en pixels (défaut 1280, optionnel)",
    },
    risque="lecture",
)
async def capturer_ecran(
    ctx: ContexteOutil,
    ecran: int = 1,
    largeur_max: int = 1280,
) -> dict[str, Any]:
    """Capture l'écran via ``mss`` et retourne les métadonnées de l'image PNG enregistrée."""
    del ctx
    cap = await asyncio.to_thread(vision.capturer, int(ecran), int(largeur_max))
    return {
        "nom": cap.chemin.name,
        "chemin": str(cap.chemin),
        "url": f"/api/ecran/image/{cap.chemin.name}",
        "largeur": cap.largeur,
        "hauteur": cap.hauteur,
        "echelle": cap.echelle,
        "origine_x": cap.origine_x,
        "origine_y": cap.origine_y,
    }


@outil(
    nom="ecran.fenetre_active",
    description="Lit le titre, le processus, le PID et le rectangle de la fenêtre Windows active.",
    parametres={
        "inclure_elements": "bool — inclure jusqu'à 150 éléments UIA visibles (défaut false, optionnel)",
    },
    risque="lecture",
)
async def lire_fenetre_active(
    ctx: ContexteOutil,
    inclure_elements: bool = False,
) -> dict[str, Any]:
    """Retourne les informations de la fenêtre active et optionnellement ses contrôles UIA."""
    del ctx
    fenetre = await asyncio.to_thread(uia.fenetre_active)
    resultat = dict(fenetre)
    if inclure_elements:
        resultat["elements"] = await asyncio.to_thread(uia.elements, 150)
    return resultat


@outil(
    nom="ecran.trouver",
    description="Localise un élément d'interface à l'écran par son objectif (UIA + OCR + repli vision).",
    parametres={"objectif": "str — description de l'élément à repérer à l'écran"},
    risque="lecture",
)
async def trouver_element_ecran(ctx: ContexteOutil, objectif: str) -> dict[str, Any]:
    """Localise un élément visuel ou UIA sur l'écran sans effectuer de clic."""
    del ctx
    return await teacher.trouver(objectif)


@outil(
    nom="pc.action",
    description=(
        "Exécute une action PC sécurisée (cliquer, double_cliquer, taper, touche, defiler, "
        "ouvrir_application) sous contrôle strict des garde-fous."
    ),
    parametres={
        "action": "dict — {type, x?, y?, bouton?, texte?, combinaison?, dy?, nom?}",
        "attendu": "dict | None — {titre_contient?, processus?} fenêtre attendue au premier plan",
    },
    risque="action_pc",
)
async def executer_action_pc(
    ctx: ContexteOutil,
    action: dict[str, Any],
    attendu: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Exécute une action PC via ``hands.executer_action`` avec approbation si requise."""
    return await hands.executer_action(
        action,
        attendu,
        source=ctx.agent_id,
        demander_accord_fn=ctx.demander_accord,
    )


@outil(
    nom="professeur.guider",
    description="Encadre visuellement les éléments sur le calque HUD étape par étape sans cliquer.",
    parametres={
        "objectif": "str — objectif du guidage",
        "etapes": "list[str] | None — liste ordonnée d'étapes visuelles (optionnel)",
    },
    risque="lecture",
)
async def guider_professeur(
    ctx: ContexteOutil,
    objectif: str,
    etapes: list[str] | None = None,
) -> dict[str, Any]:
    """Lance le mode Professeur visuel et attend les clics de l'utilisateur."""
    del ctx
    return await teacher.guider(objectif, etapes=etapes)


@outil(
    nom="fichier.supprimer",
    description="Envoie un fichier ou dossier autorisé vers la corbeille système après accord utilisateur.",
    parametres={"chemin": "str — chemin du fichier ou dossier à mettre à la corbeille"},
    risque="irreversible",
)
async def supprimer_fichier(ctx: ContexteOutil, chemin: str) -> dict[str, Any]:
    """Vérifie le chemin via ``guardrails.verifier_chemin_suppression``, demande l'accord et envoie à la corbeille."""
    decision = guardrails.verifier_chemin_suppression(chemin)
    if decision.statut == "interdit":
        raise PermissionError(decision.raison)

    if not accord_deja_obtenu():
        accord = await ctx.demander_accord(
            f"Envoyer à la corbeille : {chemin}",
            {"outil": "fichier.supprimer", "args": {"chemin": chemin}},
            "irreversible",
        )
        if not accord:
            raise PermissionError("Refusé par l'utilisateur")

    p_obj = Path(str(chemin).strip()).expanduser()
    cible = p_obj.resolve() if p_obj.is_absolute() else (config.ROOT.resolve() / p_obj).resolve()
    if not cible.exists():
        raise FileNotFoundError(f"Chemin introuvable : {cible}")

    await asyncio.to_thread(send2trash, str(cible))
    return {"ok": True, "chemin": str(cible), "corbeille": True}
