/**
 * Contrats TypeScript de toutes les entités REST et WebSocket de Jarvis AIOS.
 * Aligne strictement les structures avec ARCHITECTURE.md et ARCHITECTURE-DOMAINES.md.
 */

/** État global de la sphère / du moteur Jarvis. */
export type EtatJarvis = 'repos' | 'ecoute' | 'reflexion' | 'parole' | 'action';

/** État temps réel d'un agent dans la barre latérale et le tableau de bord. */
export type AgentEtat = 'libre' | 'travaille' | 'attend_accord' | 'fini' | 'erreur';

/** Forme géométrique de la mascotte SVG d'un agent. */
export type AvatarForme = 'sphere' | 'cube' | 'diamant' | 'anneau' | 'goutte';

/** Style des yeux de la mascotte SVG d'un agent. */
export type AvatarYeux = 'ronds' | 'fentes' | 'points' | 'visiere';

/** Niveau d'effort de raisonnement envoyé au routeur LLM. */
export type Effort = 'low' | 'medium' | 'high' | 'xhigh';

/** Famille de moteur LLM sélectionnable dans l'UI. */
export type MoteurId = 'auto' | 'gemini' | 'claude' | 'chatgpt';

/** Configuration visuelle et état courant de l'avatar d'un agent. */
export interface AgentAvatarConfig {
  forme: AvatarForme;
  couleur: string;
  yeux: AvatarYeux;
  etat: AgentEtat;
}

/** Définition complète d'un agent AIOS (`agents/<id>.json`). */
export interface Agent {
  id: string;
  nom: string;
  titre: string;
  avatar: AgentAvatarConfig;
  instructions: string;
  modele_principal: string;
  effort_raisonnement: Effort;
  modele_secours: string;
  souvenirs: string[];
  skills_assignes: string[];
  acces_second_cerveau: boolean;
  outils: string[];
  fichiers_generes: string[];
  routines: string[];
  cree_le: string;
}

/** Définition d'un canal multi-agents (`canaux/<id>/canal.json`). */
export interface Canal {
  id: string;
  nom: string;
  description: string;
  membres: string[];
  coordinateur: string;
  max_tours_par_agent: number;
  routines: string[];
  cree_le: string;
  memoire_md?: string;
}

/** Métadonnées associées à un message d'agent ou de canal. */
export interface MessageMeta {
  moteur?: string;
  modele?: string;
  effort?: Effort;
  bascules?: string[];
  synthese?: boolean;
  [key: string]: unknown;
}

/** Message persisté en base SQLite dans une conversation agent ou canal. */
export interface Message {
  id: number | string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system' | string;
  auteur: string;
  contenu: string;
  meta?: MessageMeta | string;
  cree_le: string;
}

/** Exécution historique ou en cours d'une routine planifiée. */
export interface RunRoutine {
  id: number;
  routine_id: string;
  debut: string;
  fin: string | null;
  statut: 'en_cours' | 'ok' | 'erreur' | string;
  sortie_path?: string | null;
  resume?: string | null;
  erreur?: string | null;
  contenu_md?: string | null;
}

/** Routine planifiée (`aios/routines/<id>.md`) enrichie par l'API. */
export interface Routine {
  id: string;
  nom: string;
  cron: string;
  agent: string;
  skill?: string | null;
  sortie: string;
  actif: boolean;
  description: string;
  consigne?: string;
  prochaine_execution?: string | null;
  dernier_run?: RunRoutine | null;
}

/** Tâche du jour (`taches` SQLite). */
export interface Tache {
  id: number;
  titre: string;
  fait: boolean | number;
  date: string;
  source: string;
  ordre: number;
}

/** Carte visuelle affichée dans le tableau de bord (`cartes` SQLite). */
export interface Carte {
  id: string;
  titre: string;
  contenu_md: string;
  type: string;
  epinglee: boolean | number;
  cree_le: string;
}

/** Demande d'approbation utilisateur pour une action sensible ou irréversible. */
export interface Approbation {
  id: string;
  source: string;
  action: Record<string, unknown>;
  resume: string;
  risque: string;
  statut: 'en_attente' | 'autorisee' | 'refusee' | 'expiree' | string;
  cree_le?: string;
  decide_le?: string | null;
}

/** Seau de quota renvoyé par le script agy-quota. */
export interface QuotaBucket {
  group?: string;
  window?: string;
  /** Fraction restante entre 0 et 1 (ou pourcentage 0..100 selon source, normalisé côté UI). */
  remaining: number;
  resetsAt?: string | null;
}

/** Réponse globale des quotas LLM (`GET /api/quotas` et événement WS `quotas`). */
export interface Quotas {
  updatedAt?: string;
  buckets?: Record<string, QuotaBucket>;
  claude_cli_5h_tokens?: number;
  claude_cli_estime_pct?: number;
  claude_code_5h?: QuotaBucket;
  [key: string]: unknown;
}

/** Statistiques agrégées du tableau de bord (`GET /api/stats`). */
export interface Stats {
  notes: number;
  liens: number;
  branches: Record<string, number>;
  routines_actives: number;
  runs_7j: { ok: number; erreur: number };
  taches_jour: { faites: number; total: number };
  messages_24h: number;
  usage_llm_24h: {
    appels: number;
    tokens: number;
    par_modele: Record<string, number>;
  };
  projets_actifs: number;
  souvenirs: number;
}

/** Entrée du catalogue de modèles LLM exposée par `GET /api/moteurs`. */
export interface ModeleCatalogue {
  id: string;
  libelle: string;
  moteur_ui: 'gemini' | 'claude' | 'chatgpt' | string;
  backend: 'agy' | 'claude_cli' | string;
  groupe_quota?: string;
  disponible?: boolean;
  raison?: string | null;
}

/** État de disponibilité d'un moteur backend. */
export interface MoteurDisponibilite {
  ok: boolean;
  raison?: string | null;
}

/** Réponse complète de `GET /api/moteurs` (supporte tableau direct ou objet structuré). */
export interface MoteursPayload {
  modeles?: ModeleCatalogue[];
  catalogue?: ModeleCatalogue[];
  moteurs?: Record<string, MoteurDisponibilite>;
}

/** Moteur de synthèse vocale utilisé pour les réponses parlées. */
export type MoteurTts = 'edge' | 'windows' | 'http';

/** Modèle Whisper local utilisé pour la transcription. */
export type SttModele = 'large-v3-turbo' | 'medium' | 'small';

/** Mode de déclenchement de l'écoute vocale. */
export type DeclenchementVoix = 'push' | 'continu';

/** Paramètres vocaux et audio (`parametres.json` -> `voix`). */
export interface VoixParametres {
  active: boolean;
  nom: string;
  moteur_tts: MoteurTts;
  tts_url: string | null;
  stt_modele: SttModele;
  stt_beam_final: number;
  declenchement: DeclenchementVoix;
  micro: string | null;
  rapide: boolean;
  modele_rapide: string;
}

/** Périphérique microphone renvoyé par `GET /api/voix/micros`. */
export interface VoixMicro {
  index: number;
  nom: string;
  defaut: boolean;
}

/** État du pipeline vocal renvoyé par `GET /api/voix/etat`. */
export interface VoixEtat {
  stt_modele: SttModele | string;
  stt_peripherique: 'cuda' | 'cpu' | string;
  stt_charge: boolean;
  tts_moteur: MoteurTts | string;
  tts_http_joignable: boolean | null;
}

/** Paramètres globaux persistés (`RUNTIME/parametres.json`). */
export interface Parametres {
  moteur: MoteurId;
  modele: string;
  effort: Effort;
  verbosite: 'normal' | 'concis' | 'detaille';
  accent: string;
  voix: VoixParametres;
  raccourci: string;
  memoire_auto: boolean;
}

/** Résumé d'une note du second cerveau (`GET /api/cerveau/notes`). */
export interface NoteResume {
  id: string;
  titre: string;
  branche: string;
  tags: string[];
  statut: string;
  date: string;
  extrait: string;
}

/** Contenu complet d'une note du second cerveau (`GET /api/cerveau/note`). */
export interface NoteDetail {
  id: string;
  titre: string;
  branche: string;
  tags: string[];
  statut: string;
  date: string;
  contenu_md: string;
  liens_sortants: string[];
  liens_entrants: string[];
  chemin_absolu: string;
}

/** Branche thématique du graphe du second cerveau (`GET /api/cerveau/graphe`). */
export interface CerveauBranche {
  id: string;
  libelle: string;
  couleur: string;
  nb: number;
}

/** Nœud (note) du graphe du second cerveau (`GET /api/cerveau/graphe`). */
export interface CerveauNoeud {
  id: string;
  titre: string;
  branche: string;
  tags: string[];
  poids: number;
  actif: boolean;
  date: string;
}

/** Lien orienté (résolu ou cassé) entre deux notes du graphe. */
export interface CerveauLien {
  source: string;
  cible: string;
}

/** Résultat d'une recherche plein texte FTS5 (`GET /api/cerveau/recherche`). */
export interface CerveauRechercheItem {
  id: string;
  titre: string;
  branche: string;
  extrait: string;
}

/** Graphe du second cerveau (`GET /api/cerveau/graphe`). */
export interface Graphe {
  branches: CerveauBranche[];
  noeuds: CerveauNoeud[];
  liens: CerveauLien[];
  liens_casses: CerveauLien[];
}

/** Skill AIOS (`GET /api/skills`). */
export interface Skill {
  nom: string;
  description: string;
  declencheurs: string[];
  outils: string[];
  nb_etapes?: number;
  agent_par_defaut?: string;
  contenu_md?: string;
  etapes?: string[];
}

/** Souvenir persisté dans `second-brain/memories.json`. */
export interface Memoire {
  id: string;
  type: 'preference' | 'correction' | 'regle' | 'fait';
  texte: string;
  source?: string;
  agent?: string;
  cree_le: string;
}

/** Réponse de santé du backend (`GET /api/sante`). */
export interface SanteReponse {
  ok: boolean;
  version: string;
  overlay_connecte: boolean;
  moteurs: {
    agy: boolean;
    claude_cli: { ok: boolean; raison?: string };
    codex: boolean;
  };
}

/** Action cliquable optionnelle affichée dans un toast (lien de route ou callback). */
export interface ToastAction {
  label: string;
  to?: string;
  onClick?: () => void;
}

/** Notification éphémère de l'interface. */
export interface ToastItem {
  id: string;
  texte: string;
  niveau: 'info' | 'succes' | 'avertissement' | 'erreur';
  action?: ToastAction;
}

/** Enveloppe générique reçue via WebSocket. */
export interface WSEnvelope<T = Record<string, unknown>> {
  type: string;
  data: T;
  ts: string;
}
