import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { DataBridge } from '@/components/DataBridge';
import { ApprovalModal } from './ApprovalModal';
import { CommandPalette } from './CommandPalette';
import { MajModal } from './MajModal';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';

/**
 * Coque applicative principale de Jarvis AIOS.
 * Monte une fois le pont de données WebSocket, la barre du haut, la barre latérale,
 * la zone de vue routée, la fenêtre d'approbation globale et les toasts.
 */
export function AppShell() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div className="flex min-h-screen w-full flex-col overflow-x-hidden bg-[#05070d] text-[#e2e8f0]">
      <DataBridge />

      <TopBar
        onOpenPalette={() => setPaletteOpen(true)}
        onToggleMobileSidebar={() => setMobileOpen((prev) => !prev)}
      />

      <div className="flex min-h-0 flex-1">
        <Sidebar
          collapsed={sidebarCollapsed}
          mobileOpen={mobileOpen}
          onToggleCollapse={() => setSidebarCollapsed((prev) => !prev)}
          onCloseMobile={() => setMobileOpen(false)}
        />

        <main className="min-w-0 flex-1 overflow-y-auto p-3 sm:p-4 lg:p-5">
          <Outlet />
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ApprovalModal />
      <MajModal />
    </div>
  );
}
