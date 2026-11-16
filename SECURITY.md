# Politique de sécurité

## Modèle de sécurité de Jarvis AIOS
- Serveur web et WebSocket limités à l'interface locale `127.0.0.1:5230`.
- Jeton local aléatoire généré à la première exécution (`runtime/token.txt`), comparé à temps constant, requis sur les routes mutantes, les routes sensibles et `/ws`.
- Contrôle strict des en-têtes `Host` et `Origin` pour bloquer les attaques par DNS rebinding ou CSRF.
- Garde-fous codés en dur sur le pilotage PC (actions interdites et demandes d'accord explicites).
- Mises à jour restreintes aux hôtes GitHub autorisés en HTTPS avec vérification obligatoire de l'empreinte SHA-256.

## Signaler une vulnérabilité
Merci de **ne pas ouvrir d'issue publique** pour une faille de sécurité.

Utilisez l'onglet **Security → Advisories** (*Private Vulnerability Reporting*) du dépôt GitHub en précisant :
- la version concernée ;
- les étapes de reproduction ;
- l'impact potentiel observé.
