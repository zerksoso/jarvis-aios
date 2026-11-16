import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, getToken, setApiErrorHandler } from '@/lib/api';
import type {
  AgentEtat,
  Approbation,
  Carte,
  EtatJarvis,
  Quotas,
  SanteReponse,
  ToastItem,
} from '@/lib/types';
import { wsClient } from '@/lib/ws';
import { useAgents } from '@/store/useAgents';
import { useApprobations } from '@/store/useApprobations';
import { useCanaux } from '@/store/useCanaux';
import { useCartes } from '@/store/useCartes';
import { useChats } from '@/store/useChats';
import { useJarvis } from '@/store/useJarvis';
import { useMaj } from '@/store/useMaj';
import { useMoteurs } from '@/store/useMoteurs';
import { useParametres } from '@/store/useParametres';
import { useQuotas } from '@/store/useQuotas';
import {
  hasSetupRedirectedThisSession,
  markSetupRedirectedThisSession,
  useSetup,
} from '@/store/useSetup';
import { useStats } from '@/store/useStats';
import { useTaches } from '@/store/useTaches';
import { useToasts } from '@/store/useToasts';

/**
 * Joue un fichier audio TTS renvoyé par `/api/voix/parler` dans le navigateur
 * lorsque l'overlay bureau n'est pas connecté.
 *
 * @param texte - Texte à synthétiser en parole.
 */
async function speakInBrowser(texte: string): Promise<void> {
  if (!texte.trim()) return;
  try {
    const res = await api.post<{ url: string }>(
      '/api/voix/parler',
      { texte },
      { silent: true },
    );
    if (!res?.url) return;
    const token = getToken();
    const sep = res.url.includes('?') ? '&' : '?';
    const audioUrl = token
      ? `${res.url}${sep}token=${encodeURIComponent(token)}`
      : res.url;
    const audio = new Audio(audioUrl);
    await audio.play().catch(() => {
      // Lecture bloquée par la politique autoplay du navigateur : ignorée silencieusement.
    });
  } catch {
    // Erreur TTS non bloquante pour le flux de chat.
  }
}

/**
 * Pont unique entre le WebSocket, les appels initiaux REST et les stores Zustand.
 * À monter une seule fois à la racine de la coque applicative (`AppShell`).
 *
 * Effets de bord :
 * - Ouvre la connexion WebSocket unique et s'abonne à tous les événements serveur.
 * - Hydrate paramètres, agents, canaux, quotas, moteurs, approbations, tâches, cartes et santé.
 * - Enregistre le gestionnaire global de toasts d'erreur pour `api`.
 */
export function DataBridge(): null {
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;

    setApiErrorHandler((message, code) => {
      if (code === 'aucun_moteur') {
        useToasts.getState().addToast(
          message || "Aucun moteur d'IA n'est encore configuré.",
          'avertissement',
          { label: 'Assistant de bienvenue', to: '/bienvenue' },
        );
        return;
      }
      useToasts.getState().addToast(message, 'erreur');
    });

    // Chargements initiaux en parallèle
    void useParametres.getState().fetchParametres();
    void useAgents.getState().fetchAgents();
    void useCanaux.getState().fetchCanaux();
    void useQuotas.getState().fetchQuotas();
    void useMoteurs.getState().fetchMoteurs();
    void useApprobations.getState().fetchApprobations();
    void useTaches.getState().fetchTaches();
    void useCartes.getState().fetchCartes(true);
    void useStats.getState().fetchStats();
    void useMaj.getState().fetchEtat({ silent: true });
    void useSetup
      .getState()
      .fetchEtat({ silent: true })
      .then((etat) => {
        if (cancelled || !etat || etat.termine !== false) return;
        if (hasSetupRedirectedThisSession()) return;
        markSetupRedirectedThisSession();
        navigate('/bienvenue', { replace: true });
      });
    void api
      .get<SanteReponse>('/api/sante', { silent: true })
      .then((sante) => {
        if (sante && typeof sante.overlay_connecte === 'boolean') {
          useJarvis.getState().setOverlayConnecte(sante.overlay_connecte);
        }
      })
      .catch(() => {
        // Le WS mettra à jour l'état dès qu'il sera disponible.
      });

    wsClient.connect();
    const unsubStatus = wsClient.subscribeStatus((status) => {
      useJarvis.getState().setWsStatut(status);
    });

    const unsubs: Array<() => void> = [
      unsubStatus,

      wsClient.subscribe('etat_jarvis', (data) => {
        const etat = data.etat as EtatJarvis | undefined;
        if (etat) {
          useJarvis.getState().setEtatJarvis(etat);
        }
      }),

      wsClient.subscribe('narration', (data, envelope) => {
        const texte = typeof data.texte === 'string' ? data.texte : '';
        const niveau = typeof data.niveau === 'string' ? data.niveau : 'info';
        if (texte) {
          useJarvis.getState().setNarration({ texte, niveau, ts: envelope.ts });
        }
      }),

      wsClient.subscribe('overlay_statut', (data) => {
        if (typeof data.connecte === 'boolean') {
          useJarvis.getState().setOverlayConnecte(data.connecte);
        }
      }),

      wsClient.subscribe('agent_etat', (data) => {
        const id = typeof data.id === 'string' ? data.id : '';
        const etat = data.etat as AgentEtat | undefined;
        if (id && etat) {
          useAgents.getState().setAgentEtat(id, etat);
        }
      }),

      wsClient.subscribe('chat_debut', (data) => {
        const convId =
          typeof data.conversation_id === 'string' ? data.conversation_id : '';
        const auteur = typeof data.auteur === 'string' ? data.auteur : '';
        if (convId) {
          useChats.getState().handleChatDebut(convId, auteur);
        }
      }),

      wsClient.subscribe('chat_delta', (data) => {
        const convId =
          typeof data.conversation_id === 'string' ? data.conversation_id : '';
        const auteur = typeof data.auteur === 'string' ? data.auteur : '';
        const delta = typeof data.delta === 'string' ? data.delta : '';
        if (convId && delta) {
          useChats.getState().handleChatDelta(convId, auteur, delta);
        }
      }),

      wsClient.subscribe('chat_fin', (data) => {
        const convId =
          typeof data.conversation_id === 'string' ? data.conversation_id : '';
        const auteur = typeof data.auteur === 'string' ? data.auteur : '';
        const contenu = typeof data.contenu === 'string' ? data.contenu : '';
        const code = typeof data.code === 'string' ? data.code : '';
        if (code === 'aucun_moteur') {
          useToasts.getState().addToast(
            contenu || "Aucun moteur d'IA n'est encore configuré.",
            'avertissement',
            { label: 'Assistant de bienvenue', to: '/bienvenue' },
          );
        }
        if (!convId) return;

        useChats.getState().handleChatFin({
          conversation_id: convId,
          auteur,
          message_id:
            typeof data.message_id === 'number' || typeof data.message_id === 'string'
              ? data.message_id
              : undefined,
          contenu,
          moteur: typeof data.moteur === 'string' ? data.moteur : undefined,
          modele: typeof data.modele === 'string' ? data.modele : undefined,
          bascules: Array.isArray(data.bascules)
            ? data.bascules.filter((b): b is string => typeof b === 'string')
            : undefined,
          synthese: typeof data.synthese === 'boolean' ? data.synthese : undefined,
        });

        // Synthèse vocale navigateur si demande vocale sur Jarvis et overlay déconnecté
        const isVoice = useChats.getState().consumeVoicePending(convId);
        const { parametres } = useParametres.getState();
        const { overlay_connecte } = useJarvis.getState();
        if (
          isVoice &&
          auteur === 'jarvis' &&
          parametres.voix.active &&
          !overlay_connecte
        ) {
          void speakInBrowser(contenu);
        }
      }),

      wsClient.subscribe('chat_erreur', (data) => {
        const convId =
          typeof data.conversation_id === 'string' ? data.conversation_id : '';
        const auteur = typeof data.auteur === 'string' ? data.auteur : '';
        const erreur =
          typeof data.erreur === 'string' ? data.erreur : 'Erreur de génération';
        const code = typeof data.code === 'string' ? data.code : '';
        if (convId) {
          useChats.getState().consumeVoicePending(convId);
          useChats.getState().handleChatErreur(convId, auteur, erreur);
        }
        if (code === 'aucun_moteur') {
          useToasts.getState().addToast(
            erreur || "Aucun moteur d'IA n'est encore configuré.",
            'avertissement',
            { label: 'Assistant de bienvenue', to: '/bienvenue' },
          );
          return;
        }
        useToasts.getState().addToast(erreur, 'erreur');
      }),

      wsClient.subscribe('quotas', (data) => {
        useQuotas.getState().setQuotas(data as unknown as Quotas);
      }),

      wsClient.subscribe('taches_maj', () => {
        void useTaches.getState().fetchTaches();
        void useStats.getState().fetchStats();
      }),

      wsClient.subscribe('routine_etat', (data) => {
        const id = typeof data.id === 'string' ? data.id : '';
        const statut = typeof data.statut === 'string' ? data.statut : '';
        const run_id = typeof data.run_id === 'number' ? data.run_id : undefined;
        if (id && statut) {
          useStats.getState().setLastRoutineEvent({ id, statut, run_id });
        }
        void useStats.getState().fetchStats();
      }),

      wsClient.subscribe('carte', (data) => {
        const carte: Carte = {
          id: typeof data.id === 'string' ? data.id : `carte-${Date.now()}`,
          titre: typeof data.titre === 'string' ? data.titre : 'Carte',
          contenu_md: typeof data.contenu_md === 'string' ? data.contenu_md : '',
          type: typeof data.type === 'string' ? data.type : 'info',
          epinglee: true,
          cree_le: new Date().toISOString(),
        };
        useCartes.getState().addCarte(carte);
      }),

      wsClient.subscribe('approbation_demande', (data) => {
        const approbation: Approbation = {
          id: typeof data.id === 'string' ? data.id : `app-${Date.now()}`,
          source: typeof data.source === 'string' ? data.source : 'jarvis',
          resume: typeof data.resume === 'string' ? data.resume : 'Action sensible',
          risque: typeof data.risque === 'string' ? data.risque : 'irreversible',
          action:
            data.action && typeof data.action === 'object'
              ? (data.action as Record<string, unknown>)
              : {},
          statut: 'en_attente',
          cree_le: new Date().toISOString(),
        };
        useApprobations.getState().pushApprobation(approbation);
      }),

      wsClient.subscribe('approbation_resultat', (data) => {
        const id = typeof data.id === 'string' ? data.id : '';
        const statut = typeof data.statut === 'string' ? data.statut : undefined;
        if (id) {
          useApprobations.getState().resolveApprobation(id, statut);
        }
      }),

      wsClient.subscribe('toast', (data) => {
        const texte = typeof data.texte === 'string' ? data.texte : '';
        const rawNiveau = typeof data.niveau === 'string' ? data.niveau : 'info';
        const niveau: ToastItem['niveau'] =
          rawNiveau === 'succes' ||
          rawNiveau === 'avertissement' ||
          rawNiveau === 'erreur'
            ? rawNiveau
            : 'info';
        if (texte) {
          useToasts.getState().addToast(texte, niveau);
        }
      }),

      wsClient.subscribe('maj_disponible', (data) => {
        const version = typeof data.version === 'string' ? data.version : '';
        const notes = typeof data.notes === 'string' ? data.notes : '';
        const legere = typeof data.legere === 'boolean' ? data.legere : undefined;
        useMaj.getState().handleWsMajDisponible({ version, notes, legere });
        const cleanVer = version.replace(/^v/i, '');
        useToasts.getState().addToast(
          cleanVer
            ? `Mise à jour v${cleanVer} disponible.`
            : 'Une mise à jour de Jarvis est disponible.',
          'info',
          {
            label: 'Voir',
            onClick: () => useMaj.getState().setModalOpen(true),
          },
        );
      }),
    ];

    return () => {
      cancelled = true;
      setApiErrorHandler(null);
      for (const unsub of unsubs) {
        unsub();
      }
      wsClient.disconnect();
    };
  }, [navigate]);

  return null;
}
