---
titre: Méthodologie du second cerveau
tags: [methode, second-cerveau, obsidian, organisation]
branche: 02-Connaissances-Methodes
statut: actif
date: 2026-11-16
---

# Méthodologie du second cerveau

Ce coffre Markdown sert de mémoire partagée entre l'utilisateur ([[profil-utilisateur]]) et [[outils-jarvis-et-skills]].

## Structure des 6 branches

- `00-Profil` : identité, préférences, objectifs et [[principes-decision]].
- `01-Business-Projets` : projets actifs ([[jarvis-aios-roadmap]], [[prospection-clients-q4]]) et archives.
- `02-Connaissances-Methodes` : fiches de méthodes durables ([[methode-prospection]], [[methode-veille-technologique]]).
- `03-Outils-Workflows` : documentation technique, outils et routines ([[workflow-brief-du-matin]], [[stack-locale-llm]]).
- `04-Analytics-Resultats` : bilans, briefs générés et métriques ([[tableau-suivi-resultats]]).
- `05-Erreurs-Lecons` : retours d'expérience pour éviter de répéter les erreurs ([[lecon-verifier-avant-affirmer]]).

## Format du frontmatter YAML

Chaque note déclare au minimum :

- `titre` : libellé lisible affiché dans le graphe et les recherches.
- `tags` : liste de mots-clés sans `#`.
- `branche` : identifiant du dossier racine.
- `statut` : `actif`, `brouillon` ou `archive`.
- `date` : date au format `AAAA-MM-JJ`.

## Liens wiki et graphe

Relier les notes avec `[[nom-de-note]]` ou `[[nom-de-note|alias]]` pour renforcer la densité du graphe. Les liens sans cible apparaissent comme liens cassés à réparer ou créer lors de la revue.

## Cycle : Capture → Tri → Synthèse → Action

1. **Capture** : noter rapidement les idées, comptes-rendus ou résultats de veille.
2. **Tri** : classer dans la bonne branche et renseigner le frontmatter.
3. **Synthèse** : condenser en principes réutilisables (voir aussi [[prise-de-notes-atomiques]]).
4. **Action** : transformer en tâches concrètes ou en skill dans [[outils-jarvis-et-skills]].

## Revue hebdomadaire

Chaque fin de semaine, alimenter [[bilan-semaine-46]] (ou le bilan de la semaine en cours), archiver les projets terminés et vérifier les métriques dans [[metriques-productivite]].
