---
id: brief-du-matin
nom: Brief du matin
cron: "0 6 * * *"
agent: jarvis
skill: brief-du-matin
sortie: 04-Analytics-Resultats/briefs/{date}-brief-du-matin.md
actif: true
description: Synthèse quotidienne à 06h00 des tâches, notes actives, pistes de veille et métriques du cerveau.
---

Prépare le brief du matin complet en français, structuré en Markdown :
1. Synthèse des tâches en cours et priorités du jour.
2. Rappel des notes actives récentes du second cerveau (projets et méthodes).
3. 3 points d'attention concrets pour tes projets en cours ([[mes-projets]]).
4. État synthétique des métriques du cerveau (notes, liens, souvenirs, runs récents).
5. Termine impérativement par une section `## Tâches du jour` contenant 3 à 5 actions concrètes au format `- [ ] Intitulé de la tâche`.
