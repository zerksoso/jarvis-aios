# ARCHITECTURE-VOIX — conversation vocale quasi temps réel

Complète `ARCHITECTURE-DOMAINES.md` §11.11. Objectif : Jarvis transcrit pendant que l'utilisateur parle, détecte seul la fin de phrase, répond à voix haute phrase par phrase et se tait si on lui coupe la parole. Aucune clé API : Whisper local (faster-whisper, GPU si dispo), LLM par le routeur existant, voix par `tts.synthetiser` (edge-tts, repli pyttsx3). Le mode « talkie-walkie » existant (`POST /api/voix/transcrire`, `/api/voix/ecouter`) reste inchangé.

## 1. Transport : WebSocket `/ws/voix`
- URL `ws://127.0.0.1:5230/ws/voix?token=<jeton>` ; même contrôle jeton/Origin que `/ws` (refus code 4401). Une seule session vocale active à la fois : une nouvelle connexion ferme la précédente (code 4000).
- **Client → serveur** :
  - binaire : trames PCM **16 kHz, mono, int16 little-endian**, 20 ms (640 octets) idéalement ; toute longueur paire acceptée.
  - texte JSON : `{"type":"config","agent":"jarvis","voix":"fr-FR-DeniseNeural"}` (optionnel, avant l'audio), `{"type":"interrompre"}` (l'utilisateur veut couper la réponse), `{"type":"fin"}` (fermer proprement).
- **Serveur → client** (JSON texte, sauf audio) :
  - `{"type":"pret"}` après chargement du modèle Whisper (préchargé à la connexion).
  - `{"type":"parole_debut"}` dès que le VAD détecte de la voix ; si de l'audio de réponse est en cours de lecture côté client, c'est le signal de **barge-in** (le serveur annule aussi sa file TTS).
  - `{"type":"partiel","texte":str}` pendant la parole, au plus toutes les 600 ms, uniquement si le texte a changé.
  - `{"type":"final","texte":str}` à la fin de l'énoncé ; texte vide → `{"type":"rien"}` et retour à l'écoute.
  - `{"type":"reponse_delta","texte":str}` relais des deltas LLM de l'agent (pour affichage).
  - `{"type":"audio_debut","seq":int,"mime":"audio/mpeg","phrase":str}` suivi immédiatement d'**une trame binaire** = fichier audio complet de la phrase `seq` (mp3 edge-tts, ou wav si repli pyttsx3 avec `mime:"audio/wav"`). Les `seq` sont croissants par réponse, en partant de 0 pour chaque nouveau tour.
  - `{"type":"reponse_fin"}` quand le LLM a fini et que toutes les phrases ont été envoyées ; `{"type":"stop_audio"}` quand le serveur annule la réponse en cours (barge-in ou `interrompre`).
  - `{"type":"erreur","message":str,"code":str|null}` (ex. `code:"aucun_moteur"`), la session continue d'écouter.

## 2. Serveur (`backend/voice/stream.py`, route dans `backend/api/voix_ws.py`)
- **Tampon + VAD** : Silero VAD fourni par faster-whisper (`faster_whisper.vad`, onnxruntime déjà installé), fenêtres de 512 échantillons à 16 kHz, seuil 0,5. Début de parole après ≥ 200 ms de voix ; **fin d'énoncé après 700 ms de silence** ; énoncé max 30 s (coupé et finalisé). 300 ms de pré-roll conservés avant le début détecté.
- **Partiels** : pendant la parole, toutes les ~600 ms, `transcribe` sur l'audio accumulé (numpy float32, `language="fr"`, `beam_size=1`, `vad_filter=False`, `condition_on_previous_text=False`) dans un thread (`asyncio.to_thread`), jamais deux transcriptions en parallèle pour une session (si une est en cours, on saute le partiel). **Final** : `beam_size=5`.
- `stt.py` : ajouter `transcrire_tableau(audio: np.ndarray, rapide: bool) -> str` réutilisant le singleton `_modele` et son verrou, et `precharger()`.
- **Tour de conversation** : au `final` non vide, appeler le pipeline de chat existant (`demarrer_chat(ChatRequest(cible="agent", id=<agent>, message=texte))`) pour que le message et la réponse apparaissent aussi dans le chat du tableau de bord. Écouter les événements via un **écouteur interne du hub** à ajouter : `hub.ajouter_ecouteur(fn)` / `hub.retirer_ecouteur(fn)`, `fn(msg_type, data)` appelé (sans bloquer) pour chaque `broadcast`. Filtrer `chat_delta` / `chat_fin` / `chat_erreur` de la conversation `agent:<agent>`.
- **Découpage en phrases** : accumuler les deltas ; dès qu'une phrase est complète (`.`, `!`, `?`, `…`, `:` suivis d'un espace ou fin, ou saut de ligne ; ignorer les points des nombres/abréviations courantes « M. », « etc. », « ex. », « 1.5 »), la nettoyer (`tts.nettoyer_markdown_pour_tts`), ignorer blocs de code et lignes vides, regrouper les phrases de moins de 25 caractères avec la suivante. Première phrase envoyée le plus tôt possible.
- **File TTS** : une tâche asyncio par session synthétise les phrases **dans l'ordre** (`tts.synthetiser`, cache existant) et envoie `audio_debut` + binaire. Annulation (barge-in, `interrompre`, nouvelle question) : vider la file, annuler la synthèse en cours, envoyer `stop_audio`. La génération LLM n'est pas annulée (elle finit dans le chat) mais ses phrases restantes ne sont plus lues.
- **Pendant la lecture** : le serveur continue d'écouter ; une détection de parole de ≥ 300 ms pendant une réponse déclenche le barge-in.
- `etat_jarvis` diffusé comme aujourd'hui (`ecoute`, `reflexion`, `parole`, `repos`) pour la sphère et l'overlay.
- Robustesse : toute exception d'une session est journalisée et renvoyée en `erreur` ; la déconnexion libère tout (tâches annulées, écouteur du hub retiré). Aucune donnée audio écrite sur disque sauf le cache TTS existant.

## 3. Client (tableau de bord)
- `frontend/public/pcm-worklet.js` : AudioWorklet qui reçoit le flux micro, **rééchantillonne en 16 kHz mono int16** et poste des trames de 20 ms.
- `frontend/src/lib/voixTempsReel.ts` : classe `ConversationVocale` (connexion WS, `getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}})`, envoi des trames, file de lecture audio via Web Audio `decodeAudioData` jouée **dans l'ordre des `seq`**, arrêt immédiat sur `stop_audio`/`parole_debut` pendant la lecture, état observable : `inactif | connexion | ecoute | parole | reflexion | reponse | erreur`, texte partiel, dernier final).
- `Accueil.tsx` : bouton **« Conversation »** à côté du micro existant (mode mains libres activable/désactivable), sous-titre en direct du texte partiel sous la sphère, état de la sphère synchronisé, bouton « Couper » pendant la réponse. Le micro talkie-walkie existant reste disponible.

## 4. Latence visée (à mesurer, pas promise)
Partiel affiché < 1 s après le début de la parole ; début de la voix de réponse ≈ fin d'énoncé (0,7 s) + transcription finale + premier token du LLM + synthèse de la 1re phrase. Le premier token via Antigravity (~20 k tokens de contexte) reste le poste dominant.

## 5. Voie rapide (mesurée le 2026-11-16)
Mesures réelles : agy à froid = 6,6 s avant le 1er mot (même question triviale) ; agent Jarvis complet (JSON pensée/actions/réponse) = ~18 s avant la voix ; **processus agy gardé chaud** = 2 à 4 s.
- `backend/llm/agy_session.py` : `SessionAgyChaude` = un processus `agy --project default-cli-project --add-dir <sandbox> --sandbox --disable-slash-commands --model <modele> --input-format stream-json --output-format stream-json --print ""` (env nettoyé des variables de clé API comme `agy_engine`, sans fenêtre console) gardé ouvert pendant la session vocale. Envoi d'un tour : une ligne NDJSON `{"event":"user","message":{"role":"user","content":"<texte>"}}` sur stdin. Sortie : événements `{"event":"init"|"step_update"|"result", ...}` ; les deltas de texte sont dans `step_update` (même extraction que `agy_engine`), le tour se termine sur `result`. Redémarrage automatique si le processus meurt ; arrêt après 10 min d'inactivité et à la fin de la session ; un seul tour à la fois (verrou).
- `SessionVocale` utilise la voie rapide quand `voix.rapide` est vrai (défaut) : le premier message de la session agy contient la consigne vocale (français oral, phrases courtes, pas de Markdown, pas de listes, 1 à 4 phrases sauf demande explicite) + les souvenirs globaux et le profil (`00-Profil/profil-utilisateur.md`, tronqué à 3 000 caractères). Les deltas alimentent directement le découpeur de phrases → la 1re phrase part dès qu'elle est complète.
- **Actions** : si la demande nécessite d'agir (PC, fichiers, second cerveau, skills, agents, routines), la voie rapide doit répondre par une ligne unique `[[AGIR]] <consigne reformulée>` ; la session dit alors « Je m'en occupe. » et confie la consigne au pipeline complet (`demarrer_chat` sur l'agent, outils et approbations habituels), puis lit la réponse finale de l'agent phrase par phrase.
- Le tour vocal (question + réponse) est enregistré dans la conversation `agent:<agent>` comme un échange de chat normal (visible dans le tableau de bord), auteur `jarvis`, `meta.moteur="agy"`, `meta.modele=<modele>`, `meta.voie="rapide"`.
- Transcription finale : `beam_size` réglable (`voix.stt_beam_final`, défaut 1 avec `large-v3-turbo`).

## 6. Réglages `parametres.json` → bloc `voix`
`{"active":bool,"nom":str (voix TTS, défaut "fr-FR-HenriNeural"),"moteur_tts":"edge"|"windows"|"http","tts_url":str|null,"stt_modele":"large-v3-turbo"|"medium"|"small" (défaut large-v3-turbo si CUDA, sinon small),"stt_beam_final":int,"declenchement":"push"|"continu","micro":str|null,"rapide":bool,"modele_rapide":"gemini-4-hydrogen-low"}` — clés existantes (`active`, `nom`) conservées, valeurs absentes = défauts.
- `moteur_tts:"http"` : serveur local de synthèse compatible OpenAI (`POST <tts_url>/v1/audio/speech` `{"model","input","voice","response_format":"mp3"}`), prévu pour KugelAudio ou tout serveur équivalent ; repli edge-tts si injoignable.
- API : `GET /api/voix/micros` → `[{"index","nom","defaut"}]` (périphériques d'entrée sounddevice) ; `GET /api/voix/etat` → `{"stt_modele","stt_peripherique":"cuda"|"cpu","stt_charge":bool,"tts_moteur","tts_http_joignable":bool|null}` ; `POST /api/voix/tester` `{"texte"?}` → audio de test (`audio/mpeg` ou `audio/wav`) pour lecture dans le navigateur.
