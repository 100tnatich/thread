/** Identité de Thread: un T fait d'un fil. La barre finit en boucle (le bout du
 *  fil), la tige se défait en points de couture dont l'écart grandit, comme les
 *  intervalles d'une révision espacée. Même dessin que `public/favicon.svg`. */
import { useId } from 'react'

/** Le T seul, sans tuile. `fil` = trait de la barre et de la tige. */
export function Fil({ className = '', titre }: { className?: string; titre?: string }) {
  const id = useId()
  return (
    <svg
      viewBox="0 0 64 64"
      className={className}
      role={titre ? 'img' : undefined}
      aria-label={titre}
      aria-hidden={titre ? undefined : true}
    >
      <defs>
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="14" y1="14" x2="40" y2="56">
          <stop offset="0" stopColor="var(--color-accent)" />
          <stop offset=".55" stopColor="var(--color-accent)" />
          <stop offset="1" stopColor="var(--color-facile)" />
        </linearGradient>
      </defs>
      <g stroke={`url(#${id})`} strokeWidth="6" strokeLinecap="round" fill="none">
        <path d="M11 19 C21 13 43 13 51 18 C56 21 55 27 50 27" />
        <path d="M31 17 V30" />
        <path d="M31 38.5 V41" />
      </g>
      <circle cx="31" cy="51" r="3" fill="var(--color-facile)" />
    </svg>
  )
}

/** Marque: le T dans sa tuile arrondie (icône d'appli, en-tête). */
export function Marque({ className = '', titre }: { className?: string; titre?: string }) {
  return (
    <span
      className={`inline-grid place-items-center rounded-[22%] border border-bordure bg-surface-haute ${className}`}
    >
      <Fil className="size-full" titre={titre} />
    </span>
  )
}

/** Logo complet: marque + nom. `grand` pour la page de connexion. */
export default function Logo({ grand = false }: { grand?: boolean }) {
  return (
    <span className="inline-flex items-center gap-3">
      <Marque className={grand ? 'size-14' : 'size-9'} />
      <span
        className={`font-semibold tracking-[-0.035em] text-texte ${
          grand ? 'text-4xl' : 'text-xl'
        }`}
      >
        Thread
      </span>
    </span>
  )
}

/** Décor de la page de connexion: un fil qui traverse l'écran puis se défait en
 *  points de couture de plus en plus espacés (la répétition espacée, en image). */
export function FilDeFond() {
  return (
    <svg
      aria-hidden
      viewBox="0 0 1200 800"
      preserveAspectRatio="xMidYMid slice"
      className="pointer-events-none fixed inset-0 size-full"
    >
      <g fill="none" strokeLinecap="round" strokeWidth="5">
        <path
          className="fil-tisse"
          pathLength={1}
          stroke="var(--color-accent)"
          opacity=".5"
          d="M-40 690 C 140 760 260 560 420 600 C 560 635 600 770 760 700 C 880 648 880 540 980 520"
        />
        <path
          className="fil-coutures"
          stroke="var(--color-facile)"
          opacity=".45"
          strokeDasharray="7 16 7 26 7 40 7 62 7 96 7 150"
          d="M980 520 C 1070 502 1150 430 1260 300"
        />
      </g>
    </svg>
  )
}
