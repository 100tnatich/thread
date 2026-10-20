/** Briques d'interface partagées. */
import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type Ref,
  useEffect,
  useRef,
} from 'react'
import katex from 'katex'

import { tr } from '../i18n'

type Variante = 'accent' | 'neutre' | 'danger' | 'fantome'

const VARIANTES: Record<Variante, string> = {
  accent: 'bg-accent text-fond hover:brightness-110 font-semibold',
  neutre:
    'bg-surface-haute text-texte border border-bordure hover:border-accent/60',
  danger: 'bg-rate/15 text-rate border border-rate/40 hover:bg-rate/25',
  fantome: 'text-texte-doux hover:text-texte hover:bg-surface-haute',
}

export function Bouton({
  variante = 'neutre',
  selectionne = false,
  className = '',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variante?: Variante
  selectionne?: boolean
}) {
  // `selectionne` REMPLACE les classes de la variante au lieu de les
  // compléter: deux utilitaires Tailwind de même propriété (border-color)
  // entrent en conflit, et c'est l'ordre dans la feuille de style — pas
  // l'ordre dans la chaîne — qui tranche.
  const style = selectionne
    ? 'bg-accent-doux text-accent border border-accent font-medium'
    : VARIANTES[variante]

  return (
    <button
      {...props}
      aria-pressed={props['aria-pressed'] ?? (selectionne || undefined)}
      className={`min-h-11 rounded-xl px-4 py-2.5 text-sm transition-all duration-150 disabled:cursor-not-allowed disabled:opacity-40 ${style} ${className}`}
    />
  )
}

export function Champ({
  className = '',
  ref,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & {
  ref?: Ref<HTMLInputElement>
}) {
  return (
    <input
      ref={ref}
      {...props}
      className={`min-h-11 w-full rounded-xl border border-bordure bg-surface px-3.5 py-2.5 text-texte placeholder:text-texte-doux/60 transition-colors focus:border-accent focus:outline-none ${className}`}
    />
  )
}

export function Carte({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      className={`rounded-[var(--radius-carte)] border border-bordure bg-surface p-5 ${className}`}
    >
      {children}
    </div>
  )
}

export function Pastille({ couleur }: { couleur: string }) {
  return (
    <span
      className="inline-block size-2.5 shrink-0 rounded-full"
      style={{ backgroundColor: couleur }}
      aria-hidden
    />
  )
}

export function Etiquette({
  children,
  couleur,
}: {
  children: ReactNode
  couleur?: string
}) {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs"
      style={{
        borderColor: couleur ? `${couleur}55` : undefined,
        backgroundColor: couleur ? `${couleur}18` : undefined,
        color: couleur ?? undefined,
      }}
    >
      {children}
    </span>
  )
}

export function Chargement({ texte = tr('Chargement…') }: { texte?: string }) {
  return (
    <div className="flex items-center gap-3 py-8 text-texte-doux">
      <span className="size-4 animate-spin rounded-full border-2 border-bordure border-t-accent" />
      {texte}
    </div>
  )
}

export function Vide({
  titre,
  children,
}: {
  titre: string
  children?: ReactNode
}) {
  return (
    <div className="rounded-[var(--radius-carte)] border border-dashed border-bordure px-6 py-12 text-center">
      <p className="text-texte-doux">{titre}</p>
      {children && <div className="mt-4">{children}</div>}
    </div>
  )
}

export function Erreur({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p
      role="alert"
      className="animer-secousse rounded-xl border border-rate/40 bg-rate/10 px-3.5 py-2.5 text-sm text-rate"
    >
      {message}
    </p>
  )
}

export function BarreProgression({
  valeur,
  max,
}: {
  valeur: number
  max: number
}) {
  const pct = max > 0 ? Math.min(100, (valeur / max) * 100) : 0
  return (
    <div
      className="h-1 w-full overflow-hidden rounded-full bg-surface-haute"
      role="progressbar"
      aria-valuenow={valeur}
      aria-valuemin={0}
      aria-valuemax={max}
    >
      <div
        className="h-full rounded-full bg-accent transition-[width] duration-300 ease-[var(--ease-doux)]"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

/**
 * Rend les formules LaTeX ($...$ et $$...$$) avec KaTeX.
 * Le texte hors formule est inséré comme texte brut (jamais en HTML), pour
 * qu'un énoncé ne puisse pas injecter de balises.
 */
export function Texte({
  children,
  className = '',
}: {
  children: string
  className?: string
}) {
  const ref = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const noeud = ref.current
    if (!noeud) return
    noeud.replaceChildren()

    const morceaux = children.split(/(\$\$[^$]+\$\$|\$[^$\n]+\$)/g)
    for (const morceau of morceaux) {
      const bloc = morceau.startsWith('$$') && morceau.endsWith('$$')
      const inline =
        !bloc && morceau.startsWith('$') && morceau.endsWith('$') && morceau.length > 2
      if (bloc || inline) {
        const span = document.createElement('span')
        try {
          katex.render(morceau.slice(bloc ? 2 : 1, bloc ? -2 : -1), span, {
            displayMode: bloc,
            throwOnError: false,
          })
        } catch {
          span.textContent = morceau
        }
        noeud.appendChild(span)
      } else if (morceau) {
        noeud.appendChild(document.createTextNode(morceau))
      }
    }
  }, [children])

  return <span ref={ref} className={`whitespace-pre-wrap ${className}`} />
}
