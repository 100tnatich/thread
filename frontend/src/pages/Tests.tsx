import { useEffect, useState } from 'react'

import { type Cours, type Matiere, type Test, api } from '../api'
import {
  Bouton,
  Carte,
  Champ,
  Chargement,
  Erreur,
  Etiquette,
  Pastille,
  Vide,
} from '../composants/ui'
import SelecteurThemes, { type LiensTest } from '../composants/SelecteurThemes'
import { localeAffichage, tr } from '../i18n'

const aujourdhui = () => new Date().toISOString().slice(0, 10)

const AUCUN_LIEN: LiensTest = { cours: [], matieres: [] }
const sansLien = (l: LiensTest) => l.cours.length + l.matieres.length === 0

export default function Tests() {
  const [tests, setTests] = useState<Test[] | null>(null)
  const [cours, setCours] = useState<Cours[]>([])
  const [matieres, setMatieres] = useState<Matiere[]>([])
  const [erreur, setErreur] = useState<string | null>(null)
  const [formulaire, setFormulaire] = useState(false)

  const [nouveau, setNouveau] = useState({
    date: '',
    intitule: '',
  })
  const [liens, setLiens] = useState<LiensTest>(AUCUN_LIEN)
  const [enEdition, setEnEdition] = useState<{ id: number; liens: LiensTest } | null>(
    null,
  )

  const recharger = () =>
    Promise.all([api.tests(), api.cours(), api.matieres()])
      .then(([t, c, m]) => {
        setTests(t)
        setCours(c)
        setMatieres(m)
      })
      .catch((e) => setErreur(e.message))

  useEffect(() => {
    recharger()
  }, [])

  async function creer(e: React.FormEvent) {
    e.preventDefault()
    setErreur(null)
    if (sansLien(liens)) {
      setErreur(tr('Choisis au moins un thème, ou « toute la matière ».'))
      return
    }
    try {
      await api.creerTest({
        cours: liens.cours,
        matieres: liens.matieres,
        date: nouveau.date,
        intitule: nouveau.intitule,
      })
      setNouveau({ date: '', intitule: '' })
      setLiens(AUCUN_LIEN)
      setFormulaire(false)
      recharger()
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  async function deplacer(id: number, date: string) {
    if (!date) return
    try {
      await api.deplacerTest(id, date)
      recharger()
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  async function enregistrerLiens() {
    if (!enEdition) return
    if (sansLien(enEdition.liens)) {
      setErreur(tr('Un test doit rester lié à au moins un thème ou une matière.'))
      return
    }
    try {
      await api.modifierLiensTest(
        enEdition.id,
        enEdition.liens.cours,
        enEdition.liens.matieres,
      )
      setEnEdition(null)
      setErreur(null)
      recharger()
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  async function supprimer(test: Test) {
    if (!confirm(tr('Supprimer « {nom} » ?', { nom: test.intitule }))) return
    try {
      await api.supprimerTest(test.id)
      recharger()
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  if (!tests) return <Chargement />

  const couleurs = Object.fromEntries(cours.map((c) => [c.nom, c.couleur]))
  const now = aujourdhui()
  const avenir = tests.filter((t) => t.date >= now)
  const passes = tests.filter((t) => t.date < now)

  /** Résumé lisible des liens: matières entières puis thèmes isolés. */
  const resume = (t: Test) => {
    const parMatiere = new Set(
      cours.filter((c) => c.matiere && t.matieres.includes(c.matiere)).map((c) => c.nom),
    )
    const isoles = t.cours.filter((c) => !parMatiere.has(c))
    return [
      ...t.matieres.map((m) => tr('Toute la matière {noms}', { noms: m })),
      ...isoles,
    ].join(' · ')
  }

  return (
    <div className="animer-entree space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-3xl font-semibold tracking-tight">
          {tr('Tests')}
        </h1>
        <Bouton variante="accent" onClick={() => setFormulaire((f) => !f)}>
          {formulaire ? tr('Annuler') : tr('+ Ajouter')}
        </Bouton>
      </div>

      <Erreur message={erreur} />

      <p className="text-sm text-texte-doux">
        {tr(
          "Un test se lie à plusieurs thèmes, ou à toute une matière. Le planning se recalcule automatiquement : un thème sans test à venir passe en mode entretien, et dès qu'un test futur lui est lié il repasse en mode test.",
        )}
      </p>

      {formulaire && (
        <Carte>
          <form onSubmit={creer} className="space-y-4">
            <div className="space-y-2">
              <span className="text-sm text-texte-doux">
                {tr('Thèmes concernés')}
              </span>
              <SelecteurThemes
                cours={cours}
                matieres={matieres}
                valeur={liens}
                onChange={setLiens}
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="space-y-1.5">
                <span className="text-sm text-texte-doux">{tr('Date')}</span>
                <Champ
                  type="date"
                  value={nouveau.date}
                  min={now}
                  onChange={(e) =>
                    setNouveau((n) => ({ ...n, date: e.target.value }))
                  }
                  required
                />
              </label>
              <label className="space-y-1.5">
                <span className="text-sm text-texte-doux">{tr('Intitulé')}</span>
                <Champ
                  value={nouveau.intitule}
                  onChange={(e) =>
                    setNouveau((n) => ({ ...n, intitule: e.target.value }))
                  }
                  placeholder={tr('Ex. TE conditionnel')}
                  required
                />
              </label>
            </div>
            <Bouton variante="accent" type="submit" className="w-full">
              {tr('Ajouter le test')}
            </Bouton>
          </form>
        </Carte>
      )}

      <section className="space-y-3">
        <h2 className="font-medium">{tr('À venir')}</h2>
        {avenir.length === 0 ? (
          <Vide titre={tr('Aucun test à venir.')} />
        ) : (
          avenir.map((t) => {
            const jours = Math.round(
              (new Date(t.date).getTime() - new Date(now).getTime()) / 86400000,
            )
            const edition = enEdition?.id === t.id ? enEdition : null
            return (
              <Carte key={t.id} className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      <Pastille couleur={couleurs[t.cours[0]] ?? '#8B7CFF'} />
                      <span className="truncate">{t.intitule}</span>
                    </p>
                    <p className="mt-1 text-sm text-texte-doux">
                      {resume(t)} ·{' '}
                      {new Date(t.date).toLocaleDateString(localeAffichage())}
                    </p>
                  </div>
                  <Etiquette couleur={jours <= 7 ? '#F5B84A' : undefined}>
                    {tr('dans {n} j', { n: jours })}
                  </Etiquette>
                </div>

                {edition && (
                  <div className="space-y-3 rounded-xl border border-bordure p-3">
                    <SelecteurThemes
                      cours={cours}
                      matieres={matieres}
                      valeur={edition.liens}
                      onChange={(l) => setEnEdition({ id: t.id, liens: l })}
                    />
                    <div className="flex gap-2">
                      <Bouton variante="accent" onClick={enregistrerLiens}>
                        {tr('Enregistrer les thèmes')}
                      </Bouton>
                      <Bouton variante="fantome" onClick={() => setEnEdition(null)}>
                        {tr('Annuler')}
                      </Bouton>
                    </div>
                  </div>
                )}

                <div className="flex flex-wrap items-center gap-2 border-t border-bordure pt-3">
                  <Champ
                    type="date"
                    defaultValue={t.date}
                    min={now}
                    onChange={(e) => deplacer(t.id, e.target.value)}
                    className="max-w-44 text-sm"
                    aria-label={tr('Déplacer {nom}', { nom: t.intitule })}
                  />
                  {!edition && (
                    <Bouton
                      variante="fantome"
                      onClick={() =>
                        setEnEdition({
                          id: t.id,
                          // Liens directs seulement: ceux d'une matière liée
                          // passent par la matière, pas par chaque thème.
                          liens: {
                            cours: t.cours.filter(
                              (c) =>
                                !cours.find(
                                  (x) =>
                                    x.nom === c &&
                                    x.matiere &&
                                    t.matieres.includes(x.matiere),
                                ),
                            ),
                            matieres: t.matieres,
                          },
                        })
                      }
                    >
                      {tr('Modifier les thèmes')}
                    </Bouton>
                  )}
                  <Bouton
                    variante="danger"
                    className="ml-auto"
                    onClick={() => supprimer(t)}
                  >
                    {tr('Supprimer')}
                  </Bouton>
                </div>
              </Carte>
            )
          })
        )}
      </section>

      {passes.length > 0 && (
        <section className="space-y-3">
          <h2 className="font-medium text-texte-doux">{tr('Passés')}</h2>
          {passes.map((t) => (
            <Carte key={t.id} className="opacity-60">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate font-medium">{t.intitule}</p>
                  <p className="mt-1 text-sm text-texte-doux">
                    {resume(t)} ·{' '}
                    {new Date(t.date).toLocaleDateString(localeAffichage())}
                  </p>
                </div>
                <Bouton variante="fantome" onClick={() => supprimer(t)}>
                  {tr('Supprimer')}
                </Bouton>
              </div>
            </Carte>
          ))}
        </section>
      )}
    </div>
  )
}
