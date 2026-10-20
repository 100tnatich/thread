import { type Langue, changerLangue, tr, useLangue } from '../i18n'

const LANGUES: { code: Langue; libelle: string; nom: string }[] = [
  { code: 'fr', libelle: 'FR', nom: 'Français' },
  { code: 'en', libelle: 'EN', nom: 'English' },
]

/** Bouton FR | EN: change la langue de l'interface (mémorisée dans le navigateur). */
export default function SelecteurLangue({ className = '' }: { className?: string }) {
  const actuelle = useLangue()
  return (
    <div
      role="group"
      aria-label={tr('Langue de l\'interface')}
      className={`inline-flex rounded-xl border border-bordure bg-surface p-0.5 text-xs ${className}`}
    >
      {LANGUES.map((l) => (
        <button
          key={l.code}
          type="button"
          lang={l.code}
          title={l.nom}
          aria-pressed={actuelle === l.code}
          onClick={() => changerLangue(l.code)}
          className={`min-h-9 min-w-10 rounded-[10px] px-2.5 font-medium transition-colors ${
            actuelle === l.code
              ? 'bg-accent-doux text-accent'
              : 'text-texte-doux hover:text-texte'
          }`}
        >
          {l.libelle}
        </button>
      ))}
    </div>
  )
}
