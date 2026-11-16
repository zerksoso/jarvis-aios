"""Outils d'accès au second cerveau (recherche, lecture et écriture de notes)."""

import asyncio
from typing import Any

from backend.aios.tools_registry import ContexteOutil, outil
from backend.brain import indexer, search
from backend.brain.parser import lire_note
from backend.brain.paths import chemin_sur, id_depuis_chemin
from backend.ws_hub import hub


def _verifier_acces_cerveau(ctx: ContexteOutil) -> None:
    """Refuse l'opération si l'agent n'a pas accès au second cerveau."""
    if not ctx.acces_cerveau:
        raise PermissionError("Accès au second cerveau refusé pour cet agent.")


@outil(
    nom="cerveau.chercher",
    description="Recherche des notes dans le second cerveau par mots-clés (FTS5).",
    parametres={"q": "str — requête de recherche", "limite": "int — nombre max de résultats (optionnel, défaut 10)"},
    risque="lecture",
)
async def chercher_cerveau(
    ctx: ContexteOutil,
    q: str,
    limite: int = 10,
) -> list[dict[str, Any]]:
    """Recherche des notes pertinentes dans le second cerveau."""
    _verifier_acces_cerveau(ctx)
    return await asyncio.to_thread(search.rechercher, q, int(limite))


@outil(
    nom="cerveau.lire",
    description="Lit le contenu complet d'une note du second cerveau par son identifiant.",
    parametres={"id": "str — identifiant relatif de la note (sans .md)"},
    risque="lecture",
)
async def lire_cerveau(ctx: ContexteOutil, id: str) -> dict[str, Any]:
    """Lit une note sous ``BRAIN_DIR`` après validation sécurisée du chemin."""
    _verifier_acces_cerveau(ctx)
    chemin = chemin_sur(id)
    if not chemin.is_file():
        raise FileNotFoundError(f"Note introuvable : {id}")
    note = await asyncio.to_thread(lire_note, chemin)
    return {
        "id": note["id"],
        "titre": note["titre"],
        "branche": note["branche"],
        "tags": note["tags"],
        "statut": note["statut"],
        "date": note["date"],
        "contenu_md": note["contenu_md"],
    }


@outil(
    nom="cerveau.ecrire",
    description="Crée ou met à jour une note Markdown dans le second cerveau et la réindexe.",
    parametres={
        "id": "str — identifiant relatif de la note (ex. 01-Business-Projets/suivi)",
        "contenu_md": "str — contenu Markdown complet (avec frontmatter YAML recommandé)",
    },
    risque="ecriture",
)
async def ecrire_cerveau(
    ctx: ContexteOutil,
    id: str,
    contenu_md: str,
) -> dict[str, Any]:
    """Écrit une note dans ``BRAIN_DIR``, met à jour l'index FTS5 et diffuse ``cerveau_maj``."""
    _verifier_acces_cerveau(ctx)
    chemin = chemin_sur(id)

    def _ecrire() -> str:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(contenu_md, encoding="utf-8")
        indexer.indexer_fichier(chemin)
        return id_depuis_chemin(chemin)

    note_id = await asyncio.to_thread(_ecrire)
    await hub.broadcast("cerveau_maj", {})
    return {"ok": True, "id": note_id}
