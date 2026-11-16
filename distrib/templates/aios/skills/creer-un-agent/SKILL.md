---
nom: creer-un-agent
description: Conçoit un nouvel agent spécialisé Jarvis à partir d'une description en langage naturel.
declencheurs:
  - créer un agent
  - creer un agent
  - nouvel agent
  - recruter un agent
outils:
  - agent.creer
  - cerveau.chercher
agent_par_defaut: jarvis
---

# Créer un agent

Définit le rôle, les instructions, le modèle et les outils d'un nouvel agent AIOS.

## Étapes

1. Clarifier le rôle métier de l'agent, ses responsabilités et ses limites d'accès au second cerveau.
2. Vérifier quels skills et outils existants correspondent à sa mission.
3. Appeler `agent.creer` avec la description structurée pour générer sa fiche JSON et son avatar.
4. Présenter à l'utilisateur l'agent créé, ses outils autorisés et comment l'interpeller.
