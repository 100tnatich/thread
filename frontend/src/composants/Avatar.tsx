import type { Role } from '../api'
import { tr } from '../i18n'
import { LIBELLE_ROLE } from '../role'

// Vert = standard, bleu = étendu, rouge = admin (tokens du thème, jamais d'hexadécimal).
const COULEUR: Record<Role, string> = {
  standard: 'text-bien border-bien/40 bg-bien/10',
  etendu: 'text-facile border-facile/40 bg-facile/10',
  admin: 'text-rate border-rate/40 bg-rate/10',
}

/** Petit personnage du compte connecté; sa couleur dit le rôle. */
export default function Avatar({ identifiant, role }: { identifiant: string; role: Role }) {
  const titre = `${identifiant} · ${tr(LIBELLE_ROLE[role])}`
  return (
    <span
      role="img"
      aria-label={titre}
      title={titre}
      data-role={role}
      className={`inline-grid size-10 shrink-0 place-items-center rounded-full border ${COULEUR[role]}`}
    >
      <svg viewBox="0 0 24 24" className="size-5" fill="currentColor" aria-hidden>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 20c0-4.2 3.6-6.5 8-6.5s8 2.3 8 6.5v.5H4z" />
      </svg>
    </span>
  )
}
