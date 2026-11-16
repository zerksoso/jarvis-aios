---
nom: audit-ecran
description: Capture l'écran actif, liste les éléments visibles et propose des améliorations sans cliquer.
declencheurs:
  - audit écran
  - audit ecran
  - analyse mon écran
  - analyse mon ecran
  - inspecte l'écran
outils:
  - ecran.capturer
  - ecran.fenetre_active
  - carte.afficher
agent_par_defaut: jarvis
---

# Audit écran (lecture seule)

Observe l'interface affichée à l'écran et formule des recommandations sans exécuter d'action PC.

## Étapes

1. Identifier la fenêtre active via `ecran.fenetre_active` et prendre une capture via `ecran.capturer`.
2. Lister les zones principales et les éléments d'interface visibles sur la capture.
3. Relever les points de friction ergonomiques, erreurs affichées ou pistes d'amélioration.
4. Restituer un diagnostic structuré à l'utilisateur sans effectuer aucun clic ni frappe clavier.
