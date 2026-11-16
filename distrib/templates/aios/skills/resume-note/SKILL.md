---
nom: resume-note
description: Résume une note du second cerveau, en extrait les décisions et propose des liens wiki.
declencheurs:
  - résume la note
  - resume la note
  - synthétise cette note
  - points clés de la note
outils:
  - cerveau.chercher
  - cerveau.lire
  - memoriser
agent_par_defaut: jarvis
---

# Résumé de note

Condense rapidement n'importe quelle note du second cerveau en points actionnables.

## Étapes

1. Localiser la note demandée via `cerveau.chercher` puis lire son contenu complet avec `cerveau.lire`.
2. Extraire l'idée centrale, les décisions prises et les actions en suspens en 5 puces maximum.
3. Identifier les notes connexes du coffre pouvant être reliées par `[[lien]]`.
4. Si une règle ou préférence durable ressort, proposer ou enregistrer un souvenir via `memoriser`.
