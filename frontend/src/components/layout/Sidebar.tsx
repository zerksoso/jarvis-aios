import { useMemo, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  Brain,
  CalendarClock,
  Cpu,
  Database,
  FolderKanban,
  Hash,
  LayoutDashboard,
  Mic,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  X,
} from 'lucide-react';
import { AgentAvatar } from '@/components/AgentAvatar';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import type {
  Agent,
  AgentEtat,
  AvatarForme,
  AvatarYeux,
  Canal,
  Effort,
} from '@/lib/types';
import { useAgents } from '@/store/useAgents';
import { useCanaux } from '@/store/useCanaux';
import { useMoteurs } from '@/store/useMoteurs';
import { useToasts } from '@/store/useToasts';

interface SidebarProps {
  collapsed: boolean;
  mobileOpen: boolean;
  onToggleCollapse: () => void;
  onCloseMobile: () => void;
}

const ETAT_DOT_CLASS: Record<AgentEtat, string> = {
  libre: 'bg-[#34d399]',
  travaille: 'bg-[var(--accent)] animate-pulse',
  attend_accord: 'bg-[#f59e0b] animate-ping',
  fini: 'bg-[#34d399]',
  erreur: 'bg-[#f87171]',
};

const FORMES: Array<{ id: AvatarForme; label: string }> = [
  { id: 'sphere', label: 'Sphère' },
  { id: 'cube', label: 'Cube' },
  { id: 'diamant', label: 'Diamant' },
  { id: 'anneau', label: 'Anneau' },
  { id: 'goutte', label: 'Goutte' },
];

const YEUX: Array<{ id: AvatarYeux; label: string }> = [
  { id: 'ronds', label: 'Ronds' },
  { id: 'fentes', label: 'Fentes' },
  { id: 'points', label: 'Points' },
  { id: 'visiere', label: 'Visière' },
];

const COULEURS_AVATAR = ['#22d3ee', '#a78bfa', '#34d399', '#f59e0b', '#f472b6', '#60a5fa'];

/**
 * Barre latérale repliable (tiroir sur mobile) contenant la recherche rapide,
 * la navigation principale, les agents, les canaux et les raccourcis de paramètres.
 */
export function Sidebar({
  collapsed,
  mobileOpen,
  onToggleCollapse,
  onCloseMobile,
}: SidebarProps) {
  const navigate = useNavigate();
  const agents = useAgents((s) => s.agents);
  const upsertAgent = useAgents((s) => s.upsertAgent);
  const canaux = useCanaux((s) => s.canaux);
  const upsertCanal = useCanaux((s) => s.upsertCanal);
  const modeles = useMoteurs((s) => s.modeles);
  const addToast = useToasts((s) => s.addToast);

  const [filterText, setFilterText] = useState('');
  const [agentModalOpen, setAgentModalOpen] = useState(false);
  const [canalModalOpen, setCanalModalOpen] = useState(false);

  // État modale création d'agent
  const [agentTab, setAgentTab] = useState<'phrase' | 'perso'>('phrase');
  const [agentPhrase, setAgentPhrase] = useState('');
  const [agentNom, setAgentNom] = useState('');
  const [agentTitre, setAgentTitre] = useState('');
  const [agentForme, setAgentForme] = useState<AvatarForme>('sphere');
  const [agentCouleur, setAgentCouleur] = useState('#22d3ee');
  const [agentYeux, setAgentYeux] = useState<AvatarYeux>('ronds');
  const [agentInstructions, setAgentInstructions] = useState('');
  const [agentModele, setAgentModele] = useState('hydrogen');
  const [agentEffort, setAgentEffort] = useState<Effort>('medium');
  const [creatingAgent, setCreatingAgent] = useState(false);

  // État modale création de canal
  const [canalNom, setCanalNom] = useState('');
  const [canalDesc, setCanalDesc] = useState('');
  const [canalMembres, setCanalMembres] = useState<string[]>(['jarvis']);
  const [creatingCanal, setCreatingCanal] = useState(false);

  const navItems = useMemo(
    () => [
      { to: '/accueil', label: 'Tableau de bord', icon: LayoutDashboard },
      { to: '/routines', label: 'Routines', icon: CalendarClock },
      { to: '/cerveau', label: 'Second cerveau', icon: Brain },
      { to: '/projets', label: 'Projets', icon: FolderKanban },
    ],
    [],
  );

  const paramItems = useMemo(
    () => [
      { to: '/parametres/personnalisation', label: 'Personnalisation', icon: Palette },
      { to: '/parametres/voix', label: 'Voix & Audio', icon: Mic },
      { to: '/parametres/memoire', label: 'Mémoire', icon: Database },
      { to: '/parametres/modeles', label: 'Modèles par défaut', icon: Cpu },
      { to: '/parametres/maj', label: 'Mises à jour', icon: RefreshCw },
    ],
    [],
  );

  const q = filterText.trim().toLowerCase();
  const filteredNav = useMemo(
    () => (q ? navItems.filter((n) => n.label.toLowerCase().includes(q)) : navItems),
    [navItems, q],
  );
  const filteredAgents = useMemo(
    () =>
      q
        ? agents.filter(
            (a) =>
              a.nom.toLowerCase().includes(q) ||
              a.titre.toLowerCase().includes(q) ||
              a.id.toLowerCase().includes(q),
          )
        : agents,
    [agents, q],
  );
  const filteredCanaux = useMemo(
    () =>
      q
        ? canaux.filter(
            (c) =>
              c.nom.toLowerCase().includes(q) ||
              c.description.toLowerCase().includes(q) ||
              c.id.toLowerCase().includes(q),
          )
        : canaux,
    [canaux, q],
  );

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && filterText.trim()) {
      e.preventDefault();
      navigate(`/cerveau?q=${encodeURIComponent(filterText.trim())}`);
      onCloseMobile();
    }
  };

  const resetAgentForm = () => {
    setAgentPhrase('');
    setAgentNom('');
    setAgentTitre('');
    setAgentForme('sphere');
    setAgentCouleur('#22d3ee');
    setAgentYeux('ronds');
    setAgentInstructions('');
    setAgentModele('hydrogen');
    setAgentEffort('medium');
  };

  const handleCreateAgent = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreatingAgent(true);
    try {
      let created: Agent;
      if (agentTab === 'phrase') {
        if (!agentPhrase.trim()) return;
        created = await api.post<Agent>('/api/agents/creer-depuis-texte', {
          description: agentPhrase.trim(),
        });
      } else {
        if (!agentNom.trim()) return;
        const slug = agentNom
          .trim()
          .toLowerCase()
          .normalize('NFD')
          .replace(/[\u0300-\u036f]/g, '')
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/(^-|-$)/g, '');
        created = await api.post<Agent>('/api/agents', {
          id: slug || `agent-${Date.now()}`,
          nom: agentNom.trim(),
          titre: agentTitre.trim() || 'Spécialiste',
          avatar: {
            forme: agentForme,
            couleur: agentCouleur,
            yeux: agentYeux,
            etat: 'libre',
          },
          instructions: agentInstructions.trim(),
          modele_principal: agentModele,
          effort_raisonnement: agentEffort,
          modele_secours: 'claude-sonnet',
          souvenirs: [],
          skills_assignes: [],
          acces_second_cerveau: true,
          outils: ['cerveau.chercher'],
          fichiers_generes: [],
          routines: [],
        });
      }
      upsertAgent(created);
      addToast(`Agent « ${created.nom} » créé.`, 'succes');
      resetAgentForm();
      setAgentModalOpen(false);
      navigate(`/agent/${encodeURIComponent(created.id)}`);
      onCloseMobile();
    } catch {
      // Erreur affichée par le gestionnaire global API
    } finally {
      setCreatingAgent(false);
    }
  };

  const handleCreateCanal = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canalNom.trim()) return;
    setCreatingCanal(true);
    try {
      const slug = canalNom
        .trim()
        .toLowerCase()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/(^-|-$)/g, '');
      const membres = canalMembres.length > 0 ? canalMembres : ['jarvis'];
      const created = await api.post<Canal>('/api/canaux', {
        id: slug || `canal-${Date.now()}`,
        nom: canalNom.trim(),
        description: canalDesc.trim(),
        membres,
        coordinateur: membres.includes('jarvis') ? 'jarvis' : membres[0],
        max_tours_par_agent: 2,
        routines: [],
      });
      upsertCanal(created);
      addToast(`Canal #${created.nom} créé.`, 'succes');
      setCanalNom('');
      setCanalDesc('');
      setCanalMembres(['jarvis']);
      setCanalModalOpen(false);
      navigate(`/canal/${encodeURIComponent(created.id)}`);
      onCloseMobile();
    } catch {
      // Erreur gérée par API toast
    } finally {
      setCreatingCanal(false);
    }
  };

  const toggleCanalMembre = (agentId: string) => {
    setCanalMembres((prev) =>
      prev.includes(agentId) ? prev.filter((id) => id !== agentId) : [...prev, agentId],
    );
  };

  const sidebarContent = (isCompact: boolean) => (
    <div className="flex h-full flex-col overflow-hidden bg-[#0b1020]/95 backdrop-blur-md">
      <div className="flex items-center justify-between border-b border-white/10 px-3 py-2.5">
        {!isCompact && (
          <span className="hud-label font-medium text-[#94a3b8]">Navigation</span>
        )}
        <button
          type="button"
          onClick={onToggleCollapse}
          aria-label={isCompact ? 'Déplier la barre latérale' : 'Replier la barre latérale'}
          className="hidden h-7 w-7 items-center justify-center rounded-lg text-[#94a3b8] transition hover:bg-white/10 hover:text-[#e2e8f0] lg:inline-flex"
        >
          {isCompact ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>
        <button
          type="button"
          onClick={onCloseMobile}
          aria-label="Fermer le menu latéral"
          className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-[#94a3b8] transition hover:bg-white/10 hover:text-[#e2e8f0] lg:hidden"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {!isCompact && (
        <div className="px-3 pt-3">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-[#64748b]" />
            <input
              type="search"
              value={filterText}
              onChange={(e) => setFilterText(e.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder="Filtrer ou chercher (Entrée)…"
              aria-label="Rechercher dans Jarvis ou filtrer le menu"
              className="w-full rounded-lg border border-white/10 bg-[#05070d]/80 py-1.5 pr-2.5 pl-8 text-xs text-[#e2e8f0] placeholder:text-[#64748b] focus:border-[var(--accent)]/60 focus:outline-none"
            />
          </div>
        </div>
      )}

      <nav
        aria-label="Menu principal"
        className="flex-1 space-y-5 overflow-y-auto px-2.5 py-3"
      >
        {/* Vues principales */}
        <div className="space-y-1">
          {filteredNav.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                onClick={onCloseMobile}
                title={isCompact ? item.label : undefined}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-xs font-medium transition',
                    isCompact && 'justify-center px-2',
                    isActive
                      ? 'border border-[var(--accent)]/35 bg-[var(--accent)]/12 text-[#f8fafc]'
                      : 'border border-transparent text-[#94a3b8] hover:bg-white/[0.05] hover:text-[#e2e8f0]',
                  )
                }
              >
                <Icon className="h-4 w-4 shrink-0 text-[var(--accent)]" />
                {!isCompact && <span className="truncate">{item.label}</span>}
              </NavLink>
            );
          })}
        </div>

        {/* Agents */}
        <div>
          <div
            className={cn(
              'mb-1.5 flex items-center px-2',
              isCompact ? 'justify-center' : 'justify-between',
            )}
          >
            {!isCompact && <span className="hud-label">Agents</span>}
            <button
              type="button"
              onClick={() => setAgentModalOpen(true)}
              aria-label="Créer un agent"
              title="Créer un agent"
              className="inline-flex h-5 w-5 items-center justify-center rounded border border-white/10 text-[#94a3b8] transition hover:border-[var(--accent)]/50 hover:text-[var(--accent)]"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="space-y-1">
            {filteredAgents.length === 0 ? (
              !isCompact && (
                <p className="px-2.5 py-1 text-[11px] text-[#64748b]">Aucun agent</p>
              )
            ) : (
              filteredAgents.map((agent) => (
                <NavLink
                  key={agent.id}
                  to={`/agent/${encodeURIComponent(agent.id)}`}
                  onClick={onCloseMobile}
                  title={isCompact ? `${agent.nom} (${agent.avatar.etat})` : undefined}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-xs transition',
                      isCompact && 'justify-center',
                      isActive
                        ? 'border border-[var(--accent)]/35 bg-[var(--accent)]/12 text-[#f8fafc]'
                        : 'border border-transparent text-[#cbd5e1] hover:bg-white/[0.05]',
                    )
                  }
                >
                  <AgentAvatar avatar={agent.avatar} size="sm" />
                  {!isCompact && (
                    <>
                      <span className="min-w-0 flex-1 truncate">{agent.nom}</span>
                      <span
                        className={cn(
                          'h-2 w-2 shrink-0 rounded-full',
                          ETAT_DOT_CLASS[agent.avatar.etat] ?? 'bg-slate-500',
                        )}
                        title={agent.avatar.etat}
                      />
                    </>
                  )}
                </NavLink>
              ))
            )}
          </div>
        </div>

        {/* Canaux */}
        <div>
          <div
            className={cn(
              'mb-1.5 flex items-center px-2',
              isCompact ? 'justify-center' : 'justify-between',
            )}
          >
            {!isCompact && <span className="hud-label">Canaux</span>}
            <button
              type="button"
              onClick={() => setCanalModalOpen(true)}
              aria-label="Créer un canal"
              title="Créer un canal"
              className="inline-flex h-5 w-5 items-center justify-center rounded border border-white/10 text-[#94a3b8] transition hover:border-[var(--accent)]/50 hover:text-[var(--accent)]"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </div>
          <div className="space-y-1">
            {filteredCanaux.length === 0 ? (
              !isCompact && (
                <p className="px-2.5 py-1 text-[11px] text-[#64748b]">Aucun canal</p>
              )
            ) : (
              filteredCanaux.map((canal) => (
                <NavLink
                  key={canal.id}
                  to={`/canal/${encodeURIComponent(canal.id)}`}
                  onClick={onCloseMobile}
                  title={isCompact ? `# ${canal.nom}` : undefined}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs transition',
                      isCompact && 'justify-center px-2',
                      isActive
                        ? 'border border-[var(--accent)]/35 bg-[var(--accent)]/12 text-[#f8fafc]'
                        : 'border border-transparent text-[#94a3b8] hover:bg-white/[0.05] hover:text-[#e2e8f0]',
                    )
                  }
                >
                  <Hash className="h-3.5 w-3.5 shrink-0 text-[var(--accent)]" />
                  {!isCompact && <span className="truncate">{canal.nom}</span>}
                </NavLink>
              ))
            )}
          </div>
        </div>

        {/* Paramètres */}
        <div>
          {!isCompact && <div className="hud-label mb-1.5 px-2">Paramètres</div>}
          <div className="space-y-1">
            {paramItems.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  onClick={onCloseMobile}
                  title={isCompact ? item.label : undefined}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-xs transition',
                      isCompact && 'justify-center px-2',
                      isActive
                        ? 'border border-[var(--accent)]/35 bg-[var(--accent)]/12 text-[#f8fafc]'
                        : 'border border-transparent text-[#94a3b8] hover:bg-white/[0.05] hover:text-[#e2e8f0]',
                    )
                  }
                >
                  <Icon className="h-3.5 w-3.5 shrink-0" />
                  {!isCompact && <span className="truncate">{item.label}</span>}
                </NavLink>
              );
            })}
          </div>
        </div>
      </nav>
    </div>
  );

  return (
    <>
      {/* Barre latérale fixe sur desktop */}
      <aside
        className={cn(
          'hidden shrink-0 border-r border-white/10 transition-[width] duration-200 lg:block',
          collapsed ? 'w-16' : 'w-64',
        )}
      >
        {sidebarContent(collapsed)}
      </aside>

      {/* Tiroir mobile */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 flex lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Menu de navigation"
        >
          <div
            className="fixed inset-0 bg-[#05070d]/75 backdrop-blur-xs"
            onClick={onCloseMobile}
          />
          <div className="relative z-10 h-full w-72 max-w-[85vw] border-r border-white/10 shadow-2xl">
            {sidebarContent(false)}
          </div>
        </div>
      )}

      {/* Modale création d'agent */}
      {agentModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[#05070d]/80 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-agent-title"
          onClick={() => setAgentModalOpen(false)}
        >
          <div
            className="hud-panel max-h-[90vh] w-full max-w-lg overflow-y-auto p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2
                id="create-agent-title"
                className="font-display text-base font-semibold text-[#f8fafc]"
              >
                Nouvel agent AIOS
              </h2>
              <button
                type="button"
                onClick={() => setAgentModalOpen(false)}
                aria-label="Fermer"
                className="rounded p-1 text-[#94a3b8] hover:bg-white/10 hover:text-[#e2e8f0]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mt-4 flex gap-2 border-b border-white/10 pb-2">
              <button
                type="button"
                onClick={() => setAgentTab('phrase')}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition',
                  agentTab === 'phrase'
                    ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
                    : 'text-[#94a3b8] hover:text-[#e2e8f0]',
                )}
              >
                <Sparkles className="h-3.5 w-3.5" />
                Décrire en une phrase
              </button>
              <button
                type="button"
                onClick={() => setAgentTab('perso')}
                className={cn(
                  'rounded-lg px-3 py-1.5 text-xs font-medium transition',
                  agentTab === 'perso'
                    ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
                    : 'text-[#94a3b8] hover:text-[#e2e8f0]',
                )}
              >
                Personnaliser
              </button>
            </div>

            <form onSubmit={(e) => void handleCreateAgent(e)} className="mt-4 space-y-3">
              {agentTab === 'phrase' ? (
                <label className="block text-xs text-[#94a3b8]">
                  Décrivez le rôle, le ton et les outils souhaités :
                  <textarea
                    required
                    rows={4}
                    value={agentPhrase}
                    onChange={(e) => setAgentPhrase(e.target.value)}
                    placeholder="Ex. : Un analyste financier nommé Plume qui résume mes comptes et lit mes notes business."
                    className="mt-1.5 w-full rounded-lg border border-white/10 bg-[#05070d] p-3 text-xs text-[#e2e8f0] focus:border-[var(--accent)]/60 focus:outline-none"
                  />
                </label>
              ) : (
                <>
                  <div className="flex items-center gap-4 rounded-xl border border-white/10 bg-[#05070d]/60 p-3">
                    <AgentAvatar
                      avatar={{
                        forme: agentForme,
                        couleur: agentCouleur,
                        yeux: agentYeux,
                        etat: 'libre',
                      }}
                      size="lg"
                    />
                    <div className="min-w-0 flex-1 space-y-2">
                      <label className="block text-xs text-[#94a3b8]">
                        Nom
                        <input
                          type="text"
                          required
                          value={agentNom}
                          onChange={(e) => setAgentNom(e.target.value)}
                          placeholder="Atlas"
                          className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                        />
                      </label>
                      <label className="block text-xs text-[#94a3b8]">
                        Titre / Rôle
                        <input
                          type="text"
                          value={agentTitre}
                          onChange={(e) => setAgentTitre(e.target.value)}
                          placeholder="Stratège Business"
                          className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                        />
                      </label>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className="block text-xs text-[#94a3b8]">
                      Forme
                      <select
                        value={agentForme}
                        onChange={(e) => setAgentForme(e.target.value as AvatarForme)}
                        className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                      >
                        {FORMES.map((f) => (
                          <option key={f.id} value={f.id}>
                            {f.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-xs text-[#94a3b8]">
                      Yeux
                      <select
                        value={agentYeux}
                        onChange={(e) => setAgentYeux(e.target.value as AvatarYeux)}
                        className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                      >
                        {YEUX.map((y) => (
                          <option key={y.id} value={y.id}>
                            {y.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div>
                    <span className="block text-xs text-[#94a3b8]">Couleur</span>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      {COULEURS_AVATAR.map((hex) => (
                        <button
                          key={hex}
                          type="button"
                          onClick={() => setAgentCouleur(hex)}
                          aria-label={`Couleur ${hex}`}
                          className={cn(
                            'h-6 w-6 rounded-full border transition',
                            agentCouleur === hex
                              ? 'scale-110 border-white ring-2 ring-white/40'
                              : 'border-white/20',
                          )}
                          style={{ backgroundColor: hex }}
                        />
                      ))}
                      <input
                        type="color"
                        value={agentCouleur}
                        onChange={(e) => setAgentCouleur(e.target.value)}
                        aria-label="Couleur personnalisée"
                        className="h-6 w-8 cursor-pointer rounded border border-white/15 bg-transparent"
                      />
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className="block text-xs text-[#94a3b8]">
                      Modèle principal
                      <select
                        value={agentModele}
                        onChange={(e) => setAgentModele(e.target.value)}
                        className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                      >
                        {modeles.length > 0 ? (
                          modeles.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.libelle}
                            </option>
                          ))
                        ) : (
                          <option value="hydrogen">Gemini 4 Hydrogen</option>
                        )}
                      </select>
                    </label>
                    <label className="block text-xs text-[#94a3b8]">
                      Effort
                      <select
                        value={agentEffort}
                        onChange={(e) => setAgentEffort(e.target.value as Effort)}
                        className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                      >
                        <option value="low">Rapide</option>
                        <option value="medium">Équilibré</option>
                        <option value="high">Approfondi</option>
                        <option value="xhigh">Maximum</option>
                      </select>
                    </label>
                  </div>

                  <label className="block text-xs text-[#94a3b8]">
                    Instructions
                    <textarea
                      rows={3}
                      value={agentInstructions}
                      onChange={(e) => setAgentInstructions(e.target.value)}
                      placeholder="Persona, mission, contraintes…"
                      className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] p-2.5 text-xs text-[#e2e8f0]"
                    />
                  </label>
                </>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setAgentModalOpen(false)}
                  className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-[#94a3b8] hover:text-[#e2e8f0]"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={creatingAgent}
                  className="rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-4 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-50"
                >
                  {creatingAgent ? 'Création…' : 'Créer'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Modale création de canal */}
      {canalModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-[#05070d]/80 p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="create-canal-title"
          onClick={() => setCanalModalOpen(false)}
        >
          <div
            className="hud-panel w-full max-w-md p-5 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h2
                id="create-canal-title"
                className="font-display text-base font-semibold text-[#f8fafc]"
              >
                Nouveau canal multi-agents
              </h2>
              <button
                type="button"
                onClick={() => setCanalModalOpen(false)}
                aria-label="Fermer"
                className="rounded p-1 text-[#94a3b8] hover:bg-white/10 hover:text-[#e2e8f0]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={(e) => void handleCreateCanal(e)} className="mt-4 space-y-3">
              <label className="block text-xs text-[#94a3b8]">
                Nom du canal
                <input
                  type="text"
                  required
                  value={canalNom}
                  onChange={(e) => setCanalNom(e.target.value)}
                  placeholder="strategie"
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                />
              </label>

              <label className="block text-xs text-[#94a3b8]">
                Description
                <input
                  type="text"
                  value={canalDesc}
                  onChange={(e) => setCanalDesc(e.target.value)}
                  placeholder="Discussions stratégiques et décisions…"
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                />
              </label>

              <div>
                <span className="block text-xs text-[#94a3b8]">Membres</span>
                <div className="mt-1.5 grid max-h-40 grid-cols-2 gap-1.5 overflow-y-auto rounded-lg border border-white/10 bg-[#05070d]/60 p-2">
                  {agents.map((a) => (
                    <label
                      key={a.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs text-[#e2e8f0] hover:bg-white/5"
                    >
                      <input
                        type="checkbox"
                        checked={canalMembres.includes(a.id)}
                        onChange={() => toggleCanalMembre(a.id)}
                        className="accent-[var(--accent)]"
                      />
                      <span className="truncate">{a.nom}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setCanalModalOpen(false)}
                  className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-[#94a3b8] hover:text-[#e2e8f0]"
                >
                  Annuler
                </button>
                <button
                  type="submit"
                  disabled={creatingCanal}
                  className="rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-4 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-50"
                >
                  {creatingCanal ? 'Création…' : 'Créer le canal'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
