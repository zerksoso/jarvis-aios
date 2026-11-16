---
id: veille-hebdo
nom: Veille hebdomadaire
cron: "0 8 * * mon"
agent: atlas
skill: veille-web
sortie: 04-Analytics-Resultats/veilles/{date}-veille-hebdo.md
actif: false
description: Veille hebdomadaire du lundi à 08h00 (désactivée par défaut : active-la dans l'onglet Routines).
---

Réalise une synthèse hebdomadaire de veille sur les sujets liés aux projets et à l'activité décrits dans le second cerveau ([[profil-utilisateur]], [[mes-projets]]) :
- Évolutions importantes du secteur et de la réglementation, avec date et source officielle.
- Outils et workflows d'automatisation IA utiles.
- Pour chaque point : en quoi il change ton travail, avec liens `[[notes]]` existantes.
