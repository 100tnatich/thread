/**
 * Traduction minimale (FR ⇄ EN), sans bibliothèque.
 *
 * La clé d'une traduction est la phrase française telle qu'elle s'écrit dans
 * le code: `tr("Se connecter")`. En français, `tr` la renvoie telle quelle; en
 * anglais, elle cherche dans `EN` (et retombe sur le français si la phrase
 * n'est pas encore traduite). Les valeurs variables s'écrivent `{nom}`:
 * `tr("{n} questions", { n: 3 })`.
 *
 * Le contenu des cartes (énoncés, réponses) et les messages renvoyés par le
 * serveur ne sont pas traduits: ce sont des données.
 */
import { useSyncExternalStore } from 'react'

import { EN } from './i18n-en'

export type Langue = 'fr' | 'en'

const CLE = 'flashcards.langue'

function langueInitiale(): Langue {
  try {
    const stockee = localStorage.getItem(CLE)
    if (stockee === 'fr' || stockee === 'en') return stockee
  } catch {
    // localStorage indisponible (navigation privée stricte): français.
  }
  return 'fr'
}

let courante: Langue = langueInitiale()
const abonnes = new Set<() => void>()

if (typeof document !== 'undefined') document.documentElement.lang = courante

export function langue(): Langue {
  return courante
}

export function changerLangue(nouvelle: Langue): void {
  if (nouvelle === courante) return
  courante = nouvelle
  try {
    localStorage.setItem(CLE, nouvelle)
  } catch {
    // Pas grave: la langue reste valable jusqu'au rechargement.
  }
  document.documentElement.lang = nouvelle
  abonnes.forEach((f) => f())
}

/** Re-rend le composant qui l'appelle quand la langue change. */
export function useLangue(): Langue {
  return useSyncExternalStore(
    (f) => {
      abonnes.add(f)
      return () => abonnes.delete(f)
    },
    langue,
  )
}

export function tr(
  fr: string,
  valeurs?: Record<string, string | number>,
): string {
  const modele = courante === 'en' ? (EN[fr] ?? fr) : fr
  if (!valeurs) return modele
  return modele.replace(/\{(\w+)\}/g, (m, nom: string) =>
    nom in valeurs ? String(valeurs[nom]) : m,
  )
}

/** Pluriel: en français 0 et 1 sont au singulier (1,5 aussi), en anglais seul 1 l'est. */
export function pluriel(n: number): boolean {
  return courante === 'en' ? n !== 1 : n >= 2
}

/** `trn(3, "{n} question", "{n} questions")` — `{n}` est ajouté aux valeurs. */
export function trn(
  n: number,
  singulier: string,
  plur: string,
  valeurs?: Record<string, string | number>,
): string {
  return tr(pluriel(n) ? plur : singulier, { ...valeurs, n })
}

/** Locale des dates et nombres affichés. */
export function localeAffichage(): string {
  return courante === 'en' ? 'en-GB' : 'fr-CH'
}
