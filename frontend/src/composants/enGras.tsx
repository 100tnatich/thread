import type { ReactNode } from 'react'

/** Met en gras les passages entre ** ** d'un texte traduit (« une **matière** »). */
export function enGras(texte: string): ReactNode[] {
  return texte
    .split(/\*\*([^*]+)\*\*/g)
    .map((morceau, i) => (i % 2 === 1 ? <strong key={i}>{morceau}</strong> : morceau))
}
