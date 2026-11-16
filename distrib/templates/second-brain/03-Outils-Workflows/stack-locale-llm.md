---
titre: Stack locale et routage LLM
tags: [technique, llm, routage, quotas]
branche: 03-Outils-Workflows
statut: actif
date: 2026-11-11
---

# Stack locale et routage LLM

Architecture d'exécution des modèles utilisée par [[outils-jarvis-et-skills]] et [[automatisation-workflows-ia]].

## Chaînes de repli

- Modèle principal par défaut : `hydrogen` (Gemini 4 Hydrogen).
- Replis automatiques selon quotas (< 10 %) ou indisponibilité : `flash` → `claude-sonnet` → `argon` → `cli-sonnet`.
- Continuité de session conservée en SQLite (`sessions_moteur`) et suivi des coûts/tokens dans [[metriques-productivite]].

## Règles de fiabilité

- Relances automatiques sur erreurs transitoires.
- Détection stricte de toute fuite d'authentification API payante.
- Voir [[lecon-limiter-complexite-automatisations]] pour garder la configuration maintenable.
