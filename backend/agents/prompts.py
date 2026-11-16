"""Règles absolues de sécurité, format JSON imposé et assemblage des prompts système d'agents."""

from typing import Any

from backend.agents.models import Agent

REGLES_ABSOLUES: str = (
    "RÈGLES ABSOLUES ET NON NÉGOCIABLES :\n"
    "1. Ne jamais publier quoi que ce soit sur un réseau social ou une plateforme externe.\n"
    "2. Ne jamais valider un paiement, un achat, une commande ou un abonnement.\n"
    "3. Ne jamais supprimer de fichier système ou critique ; toute suppression passe par la corbeille et demande l'accord.\n"
    "4. Avant toute action irréversible (envoi, validation, suppression, exécution de script), s'arrêter et demander l'accord de l'utilisateur.\n"
    "5. Ne jamais saisir de mot de passe, de numéro de carte ou d'identifiant officiel."
)

CONSIGNE_FORMAT_JSON: str = (
    "FORMAT DE RÉPONSE OBLIGATOIRE :\n"
    "Tu DOIS répondre exclusivement sous la forme d'un objet JSON valide respectant ce schéma :\n"
    '{"pensee": "ton raisonnement interne concis", '
    '"actions": [{"outil": "nom.outil", "args": {"param": "valeur"}}], '
    '"reponse": "ta réponse en Markdown destinée à l\'utilisateur", '
    '"termine": true}\n'
    "- Si tu as besoin d'appeler un ou plusieurs outils avant de conclure, renseigne la liste \"actions\", "
    'laisse "reponse" vide ("") et mets "termine": false.\n'
    '- Lorsque tu as tous les éléments pour répondre à l\'utilisateur, laisse "actions": [], '
    'rédige la réponse complète en français dans "reponse" et mets "termine": true.\n'
    "- IMPORTANT : tu n'as AUCUN accès direct aux fichiers, au terminal, au navigateur ni à tes outils "
    "intégrés. N'essaie jamais de lire un fichier, d'ouvrir un dossier ou d'exécuter une commande toi-même : "
    "tes SEULS moyens d'agir sont les outils listés ci-dessus, demandés dans \"actions\". Le système les "
    "exécute pour toi et te renvoie leurs résultats (« Résultats des outils : … »). Pour lire une note, "
    "utilise `cerveau.lire` avec l'id renvoyé par `cerveau.chercher`."
)

_CONSIGNES_VERBOSITE: dict[str, str] = {
    "concis": "Style de réponse : CONCIS. Va droit à l'essentiel, privilégie les listes courtes et les synthèses directes.",
    "normal": "Style de réponse : ÉQUILIBRÉ. Sois clair, structuré et précis sans longueurs inutiles.",
    "detaille": "Style de réponse : DÉTAILLÉ. Développe le contexte, les justifications, les exemples et les étapes complètes.",
}


def construire_prompt_systeme(
    agent: Agent,
    verbosite: str = "normal",
    souvenirs_globaux: list[dict[str, Any]] | None = None,
    skills_resumes: list[dict[str, Any]] | None = None,
    skills_actifs_details: list[dict[str, Any]] | None = None,
    description_outils: str = "",
    extraits_cerveau: list[dict[str, Any]] | None = None,
    contexte_supplementaire: str | None = None,
) -> str:
    """Assemble le prompt système complet d'un agent conformément à ARCHITECTURE-DOMAINES §11.7.

    Args:
        agent: Modèle de l'agent appelé.
        verbosite: Niveau de verbosité issu des paramètres (``concis``, ``normal``, ``detaille``).
        souvenirs_globaux: Souvenirs globaux récents issus de ``memories.json``.
        skills_resumes: Résumés (nom + description) des skills assignés à l'agent.
        skills_actifs_details: Définitions complètes (avec étapes) des skills déclenchés.
        description_outils: Documentation formatée des outils autorisés.
        extraits_cerveau: Résultats FTS pertinents du second cerveau.
        contexte_supplementaire: Consigne ou mémoire de canal additionnelle.

    Returns:
        Chaîne du prompt système prête à être transmise au moteur LLM.
    """
    sections: list[str] = [
        f"Tu es {agent.nom} ({agent.titre}).",
    ]
    if agent.instructions.strip():
        sections.append(f"INSTRUCTIONS ET PERSONA :\n{agent.instructions.strip()}")

    sections.append(REGLES_ABSOLUES)
    sections.append(_CONSIGNES_VERBOSITE.get(verbosite, _CONSIGNES_VERBOSITE["normal"]))

    # Souvenirs (propres à l'agent + 20 souvenirs globaux récents)
    lignes_souvenirs: list[str] = []
    for souv_agent in agent.souvenirs:
        if souv_agent and str(souv_agent).strip():
            lignes_souvenirs.append(f"- [agent] {str(souv_agent).strip()}")
    if souvenirs_globaux:
        for souv in souvenirs_globaux[-20:]:
            texte_s = str(souv.get("texte") or "").strip()
            type_s = str(souv.get("type") or "fait").strip()
            if texte_s:
                lignes_souvenirs.append(f"- [{type_s}] {texte_s}")
    if lignes_souvenirs:
        sections.append("SOUVENIRS PERSISTANTS :\n" + "\n".join(lignes_souvenirs))

    # Skills assignés et skills déclenchés
    if skills_resumes:
        lignes_skills = [
            f"- {s.get('nom')}: {s.get('description', '')}"
            for s in skills_resumes
            if s.get("nom")
        ]
        if lignes_skills:
            sections.append("SKILLS ASSIGNÉS :\n" + "\n".join(lignes_skills))

    if skills_actifs_details:
        blocs_actifs: list[str] = []
        for sk in skills_actifs_details:
            nom_sk = sk.get("nom") or "skill"
            etapes = sk.get("etapes") or []
            if etapes:
                etapes_txt = "\n".join(f"  {i}. {etape}" for i, etape in enumerate(etapes, start=1))
                blocs_actifs.append(f"Skill actif '{nom_sk}' — Étapes à suivre :\n{etapes_txt}")
            elif sk.get("contenu_md"):
                blocs_actifs.append(f"Skill actif '{nom_sk}' :\n{sk['contenu_md']}")
        if blocs_actifs:
            sections.append("PROCÉDURE DE SKILL DÉCLENCHÉE :\n" + "\n\n".join(blocs_actifs))

    # Outils autorisés
    if description_outils.strip():
        sections.append(f"OUTILS AUTORISÉS :\n{description_outils.strip()}")
    else:
        sections.append("OUTILS AUTORISÉS : aucun outil externe (laisse \"actions\": []).")

    # Extraits du second cerveau
    if agent.acces_second_cerveau and extraits_cerveau:
        lignes_cerveau = [
            f"- [{ext.get('id')}] ({ext.get('titre')}) : {ext.get('extrait', '')}"
            for ext in extraits_cerveau[:3]
        ]
        if lignes_cerveau:
            sections.append("EXTRAITS PERTINENTS DU SECOND CERVEAU :\n" + "\n".join(lignes_cerveau))

    if contexte_supplementaire and contexte_supplementaire.strip():
        sections.append(contexte_supplementaire.strip())

    sections.append(CONSIGNE_FORMAT_JSON)
    return "\n\n".join(sections)
