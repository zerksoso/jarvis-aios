import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Bot,
  Brain,
  CalendarClock,
  FilePlus2,
  FolderKanban,
  Hash,
  LayoutDashboard,
  Play,
  Search,
  Settings,
  Sparkles,
  X,
} from 'lucide-react';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useAgents } from '@/store/useAgents';
import { useCanaux } from '@/store/useCanaux';
import { useToasts } from '@/store/useToasts';

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

interface PaletteItem {
  id: string;
  label: string;
  category: string;
  keywords: string;
  icon: typeof LayoutDashboard;
  onSelect: () => void;
}

const BRANCHES = [
  '00-Profil',
  '01-Business-Projets',
  '02-Connaissances-Methodes',
  '03-Outils-Workflows',
  '04-Analytics-Resultats',
  '05-Erreurs-Lecons',
];

/**
 * Palette de commandes globale (`Ctrl+K`) permettant de naviguer rapidement
 * et de déclencher des actions système (nouvelle note, brief du matin, test overlay).
 */
export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const navigate = useNavigate();
  const agents = useAgents((s) => s.agents);
  const canaux = useCanaux((s) => s.canaux);
  const addToast = useToasts((s) => s.addToast);

  const [query, setQuery] = useState('');
  const [noteMode, setNoteMode] = useState(false);
  const [noteBranche, setNoteBranche] = useState('02-Connaissances-Methodes');
  const [noteTitre, setNoteTitre] = useState('');
  const [noteContenu, setNoteContenu] = useState('');
  const [savingNote, setSavingNote] = useState(false);

  useEffect(() => {
    if (!open) {
      setQuery('');
      setNoteMode(false);
      setNoteTitre('');
      setNoteContenu('');
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  const items = useMemo<PaletteItem[]>(() => {
    const baseViews: PaletteItem[] = [
      {
        id: 'view-accueil',
        label: 'Tableau de bord',
        category: 'Navigation',
        keywords: 'accueil dashboard sphere',
        icon: LayoutDashboard,
        onSelect: () => {
          navigate('/accueil');
          onClose();
        },
      },
      {
        id: 'view-routines',
        label: 'Routines',
        category: 'Navigation',
        keywords: 'cron planification taches automatiques',
        icon: CalendarClock,
        onSelect: () => {
          navigate('/routines');
          onClose();
        },
      },
      {
        id: 'view-cerveau',
        label: 'Second cerveau',
        category: 'Navigation',
        keywords: 'graphe notes obsidian memoire',
        icon: Brain,
        onSelect: () => {
          navigate('/cerveau');
          onClose();
        },
      },
      {
        id: 'view-projets',
        label: 'Projets',
        category: 'Navigation',
        keywords: 'business projets notes',
        icon: FolderKanban,
        onSelect: () => {
          navigate('/projets');
          onClose();
        },
      },
      {
        id: 'view-parametres',
        label: 'Paramètres',
        category: 'Navigation',
        keywords: 'reglages accent voix modeles memoire',
        icon: Settings,
        onSelect: () => {
          navigate('/parametres/personnalisation');
          onClose();
        },
      },
    ];

    const quickActions: PaletteItem[] = [
      {
        id: 'action-note',
        label: 'Nouvelle note',
        category: 'Actions rapides',
        keywords: 'creer note second cerveau markdown',
        icon: FilePlus2,
        onSelect: () => {
          setNoteMode(true);
        },
      },
      {
        id: 'action-brief',
        label: 'Lancer le brief du matin',
        category: 'Actions rapides',
        keywords: 'routine brief matin lancer',
        icon: Play,
        onSelect: () => {
          void api
            .post('/api/routines/brief-du-matin/lancer')
            .then(() => {
              addToast('Brief du matin lancé.', 'succes');
            })
            .catch(() => {
              // Erreur gérée par le toast global de l'API
            });
          onClose();
        },
      },
      {
        id: 'action-overlay-test',
        label: "Tester l'overlay",
        category: 'Actions rapides',
        keywords: 'overlay test guidage carte',
        icon: Sparkles,
        onSelect: () => {
          void api
            .post('/api/overlay/test')
            .then(() => {
              addToast("Test de l'overlay envoyé.", 'info');
            })
            .catch(() => {
              // Erreur gérée par le toast global
            });
          onClose();
        },
      },
    ];

    const agentItems: PaletteItem[] = agents.map((a) => ({
      id: `agent-${a.id}`,
      label: `${a.nom} — ${a.titre}`,
      category: 'Agents',
      keywords: `${a.id} ${a.nom} ${a.titre}`,
      icon: Bot,
      onSelect: () => {
        navigate(`/agent/${encodeURIComponent(a.id)}`);
        onClose();
      },
    }));

    const canalItems: PaletteItem[] = canaux.map((c) => ({
      id: `canal-${c.id}`,
      label: `# ${c.nom}`,
      category: 'Canaux',
      keywords: `${c.id} ${c.nom} ${c.description}`,
      icon: Hash,
      onSelect: () => {
        navigate(`/canal/${encodeURIComponent(c.id)}`);
        onClose();
      },
    }));

    return [...quickActions, ...baseViews, ...agentItems, ...canalItems];
  }, [agents, canaux, navigate, onClose, addToast]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.category.toLowerCase().includes(q) ||
        item.keywords.toLowerCase().includes(q),
    );
  }, [items, query]);

  const handleCreateNote = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanTitle = noteTitre.trim();
    if (!cleanTitle) return;
    const slug = cleanTitle
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '');
    const id = `${noteBranche}/${slug || `note-${Date.now()}`}`;
    const today = new Date().toISOString().slice(0, 10);
    const md = `---\ntitre: "${cleanTitle.replace(/"/g, '\\"')}"\nbranche: ${noteBranche}\nstatut: actif\ndate: ${today}\ntags: []\n---\n\n# ${cleanTitle}\n\n${noteContenu.trim()}\n`;
    setSavingNote(true);
    try {
      await api.put('/api/cerveau/note', { id, contenu_md: md });
      addToast(`Note « ${cleanTitle} » enregistrée.`, 'succes');
      onClose();
    } catch {
      setSavingNote(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-[#05070d]/75 px-4 pt-20 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Palette de commandes"
      onClick={onClose}
    >
      <div
        className="hud-panel w-full max-w-xl overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {noteMode ? (
          <form onSubmit={(e) => void handleCreateNote(e)} className="p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-sm font-semibold text-[#f8fafc]">
                Nouvelle note dans le second cerveau
              </h2>
              <button
                type="button"
                onClick={() => setNoteMode(false)}
                aria-label="Revenir à la palette"
                className="rounded p-1 text-[#94a3b8] hover:bg-white/10 hover:text-[#e2e8f0]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-xs text-[#94a3b8]">
                Branche
                <select
                  value={noteBranche}
                  onChange={(e) => setNoteBranche(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
                >
                  {BRANCHES.map((b) => (
                    <option key={b} value={b}>
                      {b}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs text-[#94a3b8]">
                Titre
                <input
                  type="text"
                  required
                  autoFocus
                  value={noteTitre}
                  onChange={(e) => setNoteTitre(e.target.value)}
                  placeholder="Titre de la note…"
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
                />
              </label>
            </div>
            <label className="block text-xs text-[#94a3b8]">
              Contenu Markdown
              <textarea
                rows={4}
                value={noteContenu}
                onChange={(e) => setNoteContenu(e.target.value)}
                placeholder="Notes, idées, liens [[Cible]]…"
                className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
              />
            </label>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setNoteMode(false)}
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-[#94a3b8] hover:text-[#e2e8f0]"
              >
                Annuler
              </button>
              <button
                type="submit"
                disabled={savingNote}
                className="rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3.5 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-50"
              >
                {savingNote ? 'Création…' : 'Créer la note'}
              </button>
            </div>
          </form>
        ) : (
          <>
            <div className="flex items-center gap-2.5 border-b border-white/10 px-4 py-3">
              <Search className="h-4 w-4 shrink-0 text-[#94a3b8]" />
              <input
                type="text"
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && filtered[0]) {
                    e.preventDefault();
                    filtered[0].onSelect();
                  }
                }}
                placeholder="Rechercher une vue, un agent, un canal ou une action…"
                className="w-full bg-transparent text-sm text-[#e2e8f0] placeholder:text-[#64748b] focus:outline-none"
              />
              <button
                type="button"
                onClick={onClose}
                aria-label="Fermer la palette"
                className="rounded p-1 text-[#94a3b8] hover:bg-white/10 hover:text-[#e2e8f0]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="max-h-80 overflow-y-auto p-2">
              {filtered.length === 0 ? (
                <p className="px-3 py-6 text-center text-xs text-[#94a3b8]">
                  Aucun résultat pour « {query} »
                </p>
              ) : (
                <ul className="space-y-1">
                  {filtered.map((item, idx) => {
                    const Icon = item.icon;
                    return (
                      <li key={item.id}>
                        <button
                          type="button"
                          onClick={item.onSelect}
                          className={cn(
                            'flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-xs transition',
                            idx === 0
                              ? 'bg-white/[0.06] text-[#f8fafc]'
                              : 'text-[#e2e8f0] hover:bg-white/[0.05]',
                          )}
                        >
                          <span className="flex items-center gap-2.5 truncate">
                            <Icon className="h-4 w-4 shrink-0 text-[var(--accent)]" />
                            <span className="truncate">{item.label}</span>
                          </span>
                          <span className="ml-3 shrink-0 font-mono text-[10px] uppercase tracking-wider text-[#64748b]">
                            {item.category}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
