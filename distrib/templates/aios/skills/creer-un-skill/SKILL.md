---
nom: creer-un-skill
description: Méta-skill permettant de transformer une procédure réussie en un skill réutilisable.
declencheurs:
  - créer un skill
  - creer un skill
  - automatiser cette procédure
  - enregistrer ce workflow
outils:
  - skill.creer
  - cerveau.chercher
  - memoriser
agent_par_defaut: jarvis
---

# Créer un skill

Transforme une conversation ou une méthode réussie en un skill Markdown durable dans `aios/skills/`.

## Étapes

1. Analyser la conversation récente et identifier les étapes reproductibles qui ont mené au succès.
2. Choisir un nom court en slug, rédiger une description claire et définir 3 à 5 déclencheurs en français.
3. Sélectionner uniquement les outils réellement nécessaires parmi le registre AIOS existant.
4. Appeler l'outil `skill.creer` avec le nom, la description, les déclencheurs, les outils et les étapes numérotées.
5. Confirmer à l'utilisateur le nom du skill créé et comment le déclencher à l'avenir.
