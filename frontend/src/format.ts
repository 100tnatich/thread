/** Formats d'affichage partagés: points, et « N questions, environ P points ». */
import { localeAffichage, tr, trn } from './i18n'

/** 12 → « 12 », 12,5 → « 12,5 », 0,1 → « 0,1 » (séparateur selon la langue, 1 décimale max). */
export function pts(valeur: number): string {
  const arrondi = Math.round(valeur * 10) / 10
  return Number.isInteger(arrondi)
    ? String(arrondi)
    : arrondi.toLocaleString(localeAffichage(), { maximumFractionDigits: 1 })
}

export function libellePoints(valeur: number): string {
  return trn(valeur, '{p} point', '{p} points', { p: pts(valeur) })
}

/** « 12 pt » / « 12 pts » (forme courte). */
export function libellePtsCourt(valeur: number): string {
  return trn(valeur, '{p} pt', '{p} pts', { p: pts(valeur) })
}

/** « 32 questions, environ 20 points ». */
export function questionsEtPoints(nb: number, points: number): string {
  return tr('{q}, environ {p}', {
    q: trn(nb, '{n} question', '{n} questions'),
    p: libellePoints(points),
  })
}
