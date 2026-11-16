import { Check } from 'lucide-react';
import { cn } from '@/lib/cn';
import type { AgentAvatarConfig, AvatarForme, AvatarYeux } from '@/lib/types';

export interface AgentAvatarProps {
  avatar: AgentAvatarConfig;
  size?: 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZE_CLASSES: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'h-6 w-6',
  md: 'h-10 w-10',
  lg: 'h-16 w-16',
};

/**
 * Rendu géométrique simple des yeux selon le style choisi.
 */
function AvatarEyes({ yeux, isWorking }: { yeux: AvatarYeux; isWorking: boolean }) {
  const groupClass = isWorking ? 'animate-avatar-eyes' : undefined;
  switch (yeux) {
    case 'fentes':
      return (
        <g className={groupClass}>
          <rect x="19" y="29" width="9" height="4" rx="2" fill="#05070d" />
          <rect x="36" y="29" width="9" height="4" rx="2" fill="#05070d" />
        </g>
      );
    case 'points':
      return (
        <g className={groupClass}>
          <circle cx="24" cy="31" r="2.6" fill="#05070d" />
          <circle cx="40" cy="31" r="2.6" fill="#05070d" />
        </g>
      );
    case 'visiere':
      return (
        <g className={groupClass}>
          <rect
            x="17"
            y="27"
            width="30"
            height="8"
            rx="4"
            fill="#05070d"
            stroke="#e2e8f0"
            strokeOpacity="0.45"
            strokeWidth="1.5"
          />
          <rect x="22" y="30" width="20" height="2" rx="1" fill="#e2e8f0" fillOpacity="0.85" />
        </g>
      );
    case 'ronds':
    default:
      return (
        <g className={groupClass}>
          <circle cx="24" cy="31" r="4.2" fill="#05070d" />
          <circle cx="40" cy="31" r="4.2" fill="#05070d" />
          <circle cx="25.2" cy="29.8" r="1.4" fill="#f8fafc" />
          <circle cx="41.2" cy="29.8" r="1.4" fill="#f8fafc" />
        </g>
      );
  }
}

/**
 * Forme de base géométrique de la mascotte SVG (5 variantes).
 */
function AvatarShape({ forme, couleur }: { forme: AvatarForme; couleur: string }) {
  switch (forme) {
    case 'cube':
      return (
        <rect
          x="10"
          y="10"
          width="44"
          height="44"
          rx="11"
          fill={couleur}
          fillOpacity="0.22"
          stroke={couleur}
          strokeWidth="2.5"
        />
      );
    case 'diamant':
      return (
        <polygon
          points="32,6 58,32 32,58 6,32"
          fill={couleur}
          fillOpacity="0.22"
          stroke={couleur}
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
      );
    case 'anneau':
      return (
        <>
          <circle
            cx="32"
            cy="32"
            r="23"
            fill={couleur}
            fillOpacity="0.14"
            stroke={couleur}
            strokeWidth="5"
          />
          <circle
            cx="32"
            cy="32"
            r="14"
            fill="#05070d"
            fillOpacity="0.7"
            stroke={couleur}
            strokeOpacity="0.45"
            strokeWidth="1.5"
          />
        </>
      );
    case 'goutte':
      return (
        <path
          d="M32 7 C43 20 53 29 53 39 A21 21 0 1 1 11 39 C11 29 21 20 32 7 Z"
          fill={couleur}
          fillOpacity="0.22"
          stroke={couleur}
          strokeWidth="2.5"
          strokeLinejoin="round"
        />
      );
    case 'sphere':
    default:
      return (
        <circle
          cx="32"
          cy="32"
          r="23"
          fill={couleur}
          fillOpacity="0.22"
          stroke={couleur}
          strokeWidth="2.5"
        />
      );
  }
}

/**
 * Mascotte SVG procédurale d'un agent selon son avatar et son état temps réel.
 *
 * @param props.avatar - Configuration forme, couleur, yeux et état courant.
 * @param props.size - Taille prédéfinie (`sm`, `md`, `lg`).
 * @param props.className - Classes CSS additionnelles.
 */
export function AgentAvatar({
  avatar,
  size = 'md',
  className,
}: AgentAvatarProps) {
  const couleur = avatar.etat === 'erreur' ? '#f87171' : avatar.couleur || '#22d3ee';
  const isWorking = avatar.etat === 'travaille';
  const isAmber = avatar.etat === 'attend_accord';
  const isDone = avatar.etat === 'fini';
  const isError = avatar.etat === 'erreur';

  return (
    <div
      className={cn(
        'relative inline-flex shrink-0 items-center justify-center rounded-full select-none',
        SIZE_CLASSES[size],
        avatar.etat === 'libre' && 'animate-avatar-breathe',
        isWorking && 'animate-avatar-work',
        isAmber && 'animate-avatar-amber ring-2 ring-[#f59e0b]/80',
        isError && 'ring-2 ring-[#f87171]/80 bg-[#f87171]/10',
        className,
      )}
    >
      <svg
        viewBox="0 0 64 64"
        className="h-full w-full overflow-visible"
        aria-hidden="true"
      >
        <AvatarShape forme={avatar.forme} couleur={couleur} />
        <AvatarEyes yeux={avatar.yeux} isWorking={isWorking} />
      </svg>

      {isDone && (
        <span
          className="absolute -right-0.5 -bottom-0.5 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-[#34d399] text-[#05070d] shadow"
          aria-label="Terminé"
        >
          <Check className="h-2.5 w-2.5" strokeWidth={3} />
        </span>
      )}
    </div>
  );
}
