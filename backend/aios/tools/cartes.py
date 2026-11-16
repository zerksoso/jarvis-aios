"""Outil d'affichage et d'épinglage de cartes visuelles dans le tableau de bord."""

import uuid
from typing import Any

from backend import db
from backend.aios.tools_registry import ContexteOutil, outil
from backend.ws_hub import hub


@outil(
    nom="carte.afficher",
    description="Crée et affiche une carte Markdown épinglée dans le tableau de bord.",
    parametres={
        "titre": "str — titre de la carte",
        "contenu_md": "str — contenu Markdown",
        "type": "str — info | alerte | synthese (optionnel)",
    },
    risque="ecriture",
)
async def afficher_carte(
    ctx: ContexteOutil,
    titre: str,
    contenu_md: str,
    type: str = "info",
) -> dict[str, Any]:
    """Insère une carte dans SQLite et diffuse l'événement WebSocket ``carte``."""
    del ctx
    propre_titre = titre.strip()
    if not propre_titre:
        raise ValueError("Le titre de la carte ne peut pas être vide.")

    carte_id = f"carte-{uuid.uuid4().hex[:10]}"
    cree_le = db.maintenant_iso()
    type_carte = type.strip() or "info"

    await db.async_execute(
        """
        INSERT INTO cartes (id, titre, contenu_md, type, epinglee, cree_le)
        VALUES (?, ?, ?, ?, 1, ?)
        """,
        (carte_id, propre_titre, contenu_md, type_carte, cree_le),
    )
    await hub.broadcast(
        "carte",
        {
            "id": carte_id,
            "titre": propre_titre,
            "contenu_md": contenu_md,
            "type": type_carte,
        },
    )
    return {
        "id": carte_id,
        "titre": propre_titre,
        "contenu_md": contenu_md,
        "type": type_carte,
        "epinglee": True,
        "cree_le": cree_le,
    }
