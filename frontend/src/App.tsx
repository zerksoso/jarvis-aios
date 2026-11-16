import {
  createHashRouter,
  Navigate,
  Outlet,
  RouterProvider,
} from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { Toasts } from '@/components/layout/Toasts';
import { Accueil } from '@/views/Accueil';
import { AgentView } from '@/views/AgentView';
import { Bienvenue } from '@/views/Bienvenue';
import { CanalView } from '@/views/CanalView';
import CerveauView from '@/views/CerveauView';
import { ParametresView } from '@/views/ParametresView';
import { ProjetsView } from '@/views/ProjetsView';
import { RoutinesView } from '@/views/RoutinesView';

/**
 * Enveloppe racine fournissant la pile de notifications toast aussi bien
 * pour la coque principale que pour l'assistant hors coque (`/bienvenue`).
 */
function RootLayout() {
  return (
    <>
      <Outlet />
      <Toasts />
    </>
  );
}

const router = createHashRouter([
  {
    element: <RootLayout />,
    children: [
      { path: '/bienvenue', element: <Bienvenue /> },
      {
        path: '/',
        element: <AppShell />,
        children: [
          { index: true, element: <Navigate to="/accueil" replace /> },
          { path: 'accueil', element: <Accueil /> },
          { path: 'cerveau', element: <CerveauView /> },
          { path: 'routines', element: <RoutinesView /> },
          { path: 'projets', element: <ProjetsView /> },
          { path: 'agent/:id', element: <AgentView /> },
          { path: 'canal/:id', element: <CanalView /> },
          {
            path: 'parametres',
            element: <Navigate to="/parametres/personnalisation" replace />,
          },
          { path: 'parametres/:section', element: <ParametresView /> },
          { path: '*', element: <Navigate to="/accueil" replace /> },
        ],
      },
    ],
  },
]);

/**
 * Racine applicative fournissant le routeur hash de Jarvis AIOS.
 */
export default function App() {
  return <RouterProvider router={router} />;
}
