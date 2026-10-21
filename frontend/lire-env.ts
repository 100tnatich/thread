// Lit les ports dans le .env de la racine du projet (conventions §3.2, §6).
// Partagé par vite.config.ts et playwright.config.ts: AUCUN port en dur ailleurs.
// Une variable d'environnement du même nom l'emporte (c'est ainsi que les tests
// E2E visent leur API jetable).
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function lireFichier(): Record<string, string> {
  const chemin = resolve(import.meta.dirname, '..', '.env')
  if (!existsSync(chemin)) return {}
  const valeurs: Record<string, string> = {}
  for (const ligne of readFileSync(chemin, 'utf8').split('\n')) {
    const m = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(ligne.trim())
    if (m) valeurs[m[1]] = m[2].replace(/^["']|["']$/g, '')
  }
  return valeurs
}

export function lirePort(nom: string, obligatoire = true): number | undefined {
  const brut = process.env[nom] ?? lireFichier()[nom]
  if (!brut) {
    if (!obligatoire) return undefined
    throw new Error(`${nom} manquant dans .env (voir ~/infra/CONVENTIONS.md §3.2)`)
  }
  return Number(brut)
}
