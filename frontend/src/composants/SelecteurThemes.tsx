import type { Cours, Matiere } from '../api'
import { tr } from '../i18n'
import { Pastille } from './ui'

export interface LiensTest {
  cours: string[]
  matieres: string[]
}

/**
 * Choix des thèmes d'un test, rangés par matière. Le bouton « toute la
 * matière » lie la matière entière: ses thèmes actuels ET ceux qu'on y
 * ajoutera plus tard sont couverts.
 */
export default function SelecteurThemes({
  cours,
  matieres,
  valeur,
  onChange,
}: {
  cours: Cours[]
  matieres: Matiere[]
  valeur: LiensTest
  onChange: (v: LiensTest) => void
}) {
  const actifs = cours.filter((c) => !c.archive)
  const groupes: { nom: string | null; couleur: string; themes: Cours[] }[] = [
    ...matieres.map((m) => ({
      nom: m.nom as string | null,
      couleur: m.couleur,
      themes: actifs.filter((c) => c.matiere === m.nom),
    })),
    {
      nom: null,
      couleur: '#8A91A5',
      themes: actifs.filter((c) => c.matiere === null),
    },
  ].filter((g) => g.themes.length > 0 || g.nom !== null)

  const basculerTheme = (nom: string) =>
    onChange({
      ...valeur,
      cours: valeur.cours.includes(nom)
        ? valeur.cours.filter((c) => c !== nom)
        : [...valeur.cours, nom],
    })

  const basculerMatiere = (nom: string) =>
    onChange({
      ...valeur,
      matieres: valeur.matieres.includes(nom)
        ? valeur.matieres.filter((m) => m !== nom)
        : [...valeur.matieres, nom],
    })

  if (groupes.length === 0) {
    return (
      <p className="text-sm text-texte-doux">
        {tr("Crée d'abord une matière ou un thème.")}
      </p>
    )
  }

  return (
    <div className="space-y-4" role="group" aria-label={tr('Thèmes du test')}>
      {groupes.map((g) => {
        const entiere = g.nom !== null && valeur.matieres.includes(g.nom)
        return (
          <fieldset key={g.nom ?? '__sans'} className="space-y-2">
            <legend className="flex w-full items-center gap-2 text-sm">
              <Pastille couleur={g.couleur} />
              <span className="font-medium">{g.nom ?? tr('Sans matière')}</span>
              {g.nom !== null && (
                <button
                  type="button"
                  onClick={() => basculerMatiere(g.nom as string)}
                  aria-pressed={entiere}
                  className={`ml-auto min-h-8 rounded-lg border px-2.5 text-xs transition-colors ${
                    entiere
                      ? 'border-accent bg-accent-doux text-accent'
                      : 'border-bordure text-texte-doux hover:border-accent/60'
                  }`}
                >
                  {entiere ? `✓ ${tr('Toute la matière')}` : tr('Toute la matière')}
                </button>
              )}
            </legend>
            <ul className="grid gap-1.5 sm:grid-cols-2">
              {g.themes.map((t) => {
                const coche = entiere || valeur.cours.includes(t.nom)
                return (
                  <li key={t.nom}>
                    <label
                      className={`flex min-h-10 cursor-pointer items-center gap-2.5 rounded-lg border px-3 text-sm transition-colors ${
                        coche
                          ? 'border-accent/50 bg-accent-doux'
                          : 'border-bordure hover:border-accent/40'
                      } ${entiere ? 'opacity-70' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={coche}
                        disabled={entiere}
                        onChange={() => basculerTheme(t.nom)}
                        aria-label={tr('Thème {nom}', { nom: t.nom })}
                      />
                      <span className="truncate">{t.nom}</span>
                    </label>
                  </li>
                )
              })}
              {g.themes.length === 0 && (
                <li className="text-xs text-texte-doux">
                  {tr("Aucun thème pour l'instant.")}
                </li>
              )}
            </ul>
          </fieldset>
        )
      })}
    </div>
  )
}
