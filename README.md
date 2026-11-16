# Jarvis AIOS

**Jarvis AIOS** est un assistant personnel local pour Windows combinant un routeur multi-moteurs IA (sans clé API payante), un second cerveau compatible Obsidian, un système AIOS (skills, routines planifiées, outils), le pilotage sécurisé du PC (vision, mains avec garde-fous, mode Professeur), une équipe d'agents et des canaux multi-agents. Il s'utilise via son interface web locale (`http://127.0.0.1:5230`) et son overlay HUD transparent (`Ctrl + Espace`).

---

## Installation en 3 étapes (Windows)

1. **Télécharger l'installeur** : récupérez `JarvisAIOS-Setup-X.Y.Z.exe` depuis la page **Releases** du dépôt GitHub.
2. **Exécuter l'installation** : lancez l'installeur (installation par utilisateur dans `%LOCALAPPDATA%\Programs\JarvisAIOS`, aucun droit administrateur requis). Si Windows SmartScreen affiche un avertissement (binaire non signé), cliquez sur *Informations complémentaires* → *Exécuter quand même*.
3. **Finaliser l'assistant de premier lancement** : au démarrage, l'assistant `/#/bienvenue` vous guide pour connecter un moteur IA et initialiser votre profil dans `%USERPROFILE%\JarvisAIOS`.

---

## Connexion des moteurs IA

Jarvis AIOS s'appuie sur vos outils CLI et abonnements existants, sans facturation API séparée :

- **Antigravity (`agy`)** : Gemini 4 Hydrogen / Flash / Argon, Claude via Antigravity et repli GPT-OSS.
- **Claude Code (`claude`)** : connexion via `claude auth login` (installation guidée possible depuis l'assistant).
- **Codex (`codex`)** : connexion via `codex login` (nécessite Node.js / `npm`).

Depuis l'assistant de premier lancement (ou les paramètres), un clic sur **Connecter** ouvre la fenêtre console officielle de l'outil choisi ; une fois connecté, le routeur bascule automatiquement entre les moteurs disponibles selon l'état et les quotas.

---

## Mises à jour

- Jarvis vérifie périodiquement les nouvelles versions sur GitHub Releases (comportement réglable dans *Paramètres → Mises à jour* : *demander*, *automatique* ou *jamais*).
- **Mise à jour légère** (`JarvisAIOS-app-X.Y.Z.zip`) : lorsque le runtime Python embarqué est identique, seuls le code, le frontend et les modèles sont remplacés, avec sonde de santé sur 90 s et **restauration automatique** en cas d'échec.
- **Mise à jour complète** (`JarvisAIOS-Setup-X.Y.Z.exe`) : lorsque le runtime Python évolue, l'installeur complet met à jour l'environnement.
- Tout téléchargement est limité aux hôtes GitHub autorisés en HTTPS et vérifié par empreinte **SHA-256**.

---

## Désinstallation

1. Ouvrez **Paramètres Windows → Applications → Jarvis AIOS** (ou le raccourci de désinstallation du menu Démarrer).
2. Le désinstalleur arrête proprement Jarvis (`Jarvis.exe --arreter`) et supprime le dossier programme (`%LOCALAPPDATA%\Programs\JarvisAIOS`) ainsi que l'entrée de démarrage automatique éventuelle.
3. **Vos données personnelles (`%USERPROFILE%\JarvisAIOS`) sont conservées par défaut** ; une case à cocher (décochée par défaut) permet de les supprimer explicitement si vous le souhaitez.

---

## Limites connues

- **SmartScreen (installeur et lanceur non signés)** : aucune signature Authenticode n'est incluse ; Windows SmartScreen peut afficher un avertissement au premier lancement.
- **Connexions manuelles aux comptes** : l'authentification auprès de Google/Antigravity, Claude ou ChatGPT/Codex reste toujours effectuée manuellement par l'utilisateur dans le terminal officiel.
- **Synthèse vocale `edge-tts`** : ce moteur utilise un service Microsoft non officiel susceptible d'être modifié ou limité ; le moteur local Windows (`pyttsx3`) reste disponible dans les paramètres comme alternative hors ligne.
- **Projet `agy` par défaut à valider sur compte neuf** : l'utilisation de `--project default-cli-project` (avec repli automatique sans `--project`) n'a pas encore été validée sur un compte Antigravity vierge.
- **Codex CLI non testé avec un compte réel** : le moteur `codex` (`codex exec --json`) est implémenté sans avoir été testé avec un compte réel (repli sur GPT-OSS via Antigravity sinon).
- **GPU optionnel** : la transcription vocale locale (`faster-whisper`) et l'OCR fonctionnent sur CPU par défaut ; un GPU compatible CUDA est entièrement optionnel.

---

## Documentation et contribution

- Architecture et contrats de distribution : [ARCHITECTURE-DISTRIB.md](ARCHITECTURE-DISTRIB.md)
- Contribuer : [CONTRIBUTING.md](CONTRIBUTING.md)
- Sécurité : [SECURITY.md](SECURITY.md)
- Licence : [LICENSE](LICENSE) (MIT)
