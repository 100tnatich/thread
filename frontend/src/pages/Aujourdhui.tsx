import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { type Reglages, type Today, api } from '../api'
import { libellePoints, libellePtsCourt, questionsEtPoints } from '../format'
import { localeAffichage, tr, trn } from '../i18n'
import {
  Bouton,
  Carte,
  Chargement,
  Erreur,
  Etiquette,
  Pastille,
  Vide,
} from '../composants/ui'

export default function Aujourdhui() {
  const [today, setToday] = useState<Today | null>(null)
  const [reglages, setReglages] = useState<Reglages | null>(null)
  const [ordre, setOrdre] = useState<'melange' | 'par_cours'>('melange')
  // Mode « par cours »: '' = tous les cours du jour, sinon un seul thème.
  const [coursChoisi, setCoursChoisi] = useState('')
  const [erreur, setErreur] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.today(), api.reglages()])
      .then(([t, r]) => {
        setToday(t)
        setReglages(r)
        setOrdre(r.ordre_session)
      })
      .catch((e) => setErreur(e.message))
  }, [])

  if (erreur) return <Erreur message={erreur} />
  if (!today || !reglages) return <Chargement />

  const reportees = today.total_dues - today.total
  const unSeulCours = ordre === 'par_cours' && today.par_cours.some((c) => c.cours === coursChoisi)
  const lienSession = new URLSearchParams({ ordre })
  if (unSeulCours) lienSession.set('cours', coursChoisi)

  return (
    <div className="animer-entree space-y-6">
      <header>
        <p className="text-sm text-texte-doux">
          {new Date(today.jour).toLocaleDateString(localeAffichage(), {
            weekday: 'long',
            day: 'numeric',
            month: 'long',
          })}
        </p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">
          {tr("Aujourd'hui")}
        </h1>
      </header>

      {today.total === 0 ? (
        <Vide titre={tr("Rien à réviser aujourd'hui.")}>
          <Link to="/ajouter">
            <Bouton variante="accent">{tr('Ajouter de la matière')}</Bouton>
          </Link>
        </Vide>
      ) : (
        <>
          <Carte className="space-y-5">
            <div>
              <div className="flex items-baseline gap-3">
                <span className="text-5xl font-semibold tabular-nums">
                  {today.total}
                </span>
                <span className="text-texte-doux">
                  {trn(today.total, 'question à réviser', 'questions à réviser')}
                </span>
              </div>
              <p className="mt-1.5 text-sm text-texte-doux">
                {questionsEtPoints(today.total, today.points)} — {tr('plafond')}{' '}
                {libellePoints(today.plafond)}
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              {today.nb_nouvelles > 0 && (
                <Etiquette couleur="#5CC8FF">
                  {trn(today.nb_nouvelles, '{n} nouvelle', '{n} nouvelles')}
                </Etiquette>
              )}
              {today.nb_en_retard > 0 && (
                <Etiquette couleur="#F5B84A">
                  {tr('{n} en retard', { n: today.nb_en_retard })}
                </Etiquette>
              )}
              {reportees > 0 && (
                <Etiquette>
                  {trn(reportees, '{n} reportée à demain', '{n} reportées à demain')}
                </Etiquette>
              )}
              {today.nb_entretien > 0 && (
                <Etiquette couleur="#B279A2">
                  {tr('Entretien : {q}, {p} ({pct} %)', {
                    q: trn(today.nb_entretien, '{n} question', '{n} questions'),
                    p: libellePtsCourt(today.points_entretien),
                    pct: Math.round(today.part_entretien * 100),
                  })}
                </Etiquette>
              )}
            </div>

            {today.alertes.map((a) => (
              <p
                key={a}
                role="alert"
                className="rounded-xl border border-difficile/40 bg-difficile/10 px-3.5 py-2.5 text-xs text-difficile"
              >
                {a}
              </p>
            ))}

            <ul className="space-y-2.5">
              {today.par_cours.map((c) => (
                <li key={c.cours} className="flex items-center gap-3 text-sm">
                  <Pastille couleur={c.couleur} />
                  <span className="flex-1">
                    {c.cours}
                    {c.entretien && (
                      <span className="ml-2 text-xs text-[#B279A2]">{tr('entretien')}</span>
                    )}
                  </span>
                  <span className="tabular-nums text-texte-doux">
                    {c.nb} q · {libellePtsCourt(c.points)}
                  </span>
                </li>
              ))}
            </ul>

            <div className="space-y-3 border-t border-bordure pt-5">
              <fieldset>
                <legend className="mb-2 text-xs text-texte-doux">
                  {tr('Ordre des questions')}
                </legend>
                <div className="flex gap-2">
                  {(
                    [
                      ['melange', tr('Mélangé')],
                      ['par_cours', tr('Par cours')],
                    ] as const
                  ).map(([valeur, libelle]) => (
                    <Bouton
                      key={valeur}
                      onClick={() => setOrdre(valeur)}
                      selectionne={ordre === valeur}
                    >
                      {libelle}
                    </Bouton>
                  ))}
                </div>
              </fieldset>

              {ordre === 'par_cours' && (
                <label className="block space-y-2">
                  <span className="text-xs text-texte-doux">{tr('Cours à réviser')}</span>
                  <select
                    value={unSeulCours ? coursChoisi : ''}
                    onChange={(e) => setCoursChoisi(e.target.value)}
                    className="min-h-11 w-full rounded-xl border border-bordure bg-surface-haute px-3.5 text-sm text-texte focus:border-accent focus:outline-none"
                  >
                    <option value="">{tr('Tous les cours')}</option>
                    {today.par_cours.map((c) => (
                      <option key={c.cours} value={c.cours}>
                        {c.cours} · {c.nb} q
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <Link to={`/session?${lienSession}`} className="block">
                <Bouton variante="accent" className="w-full text-base">
                  {tr('Commencer')}
                </Bouton>
              </Link>
            </div>
          </Carte>
        </>
      )}

      {today.prochain_test ? (
        <Carte>
          <p className="text-xs uppercase tracking-wide text-texte-doux">
            {tr('Prochain test')}
          </p>
          <p className="mt-2 font-medium">{today.prochain_test.intitule}</p>
          <p className="mt-1 text-sm text-texte-doux">
            {today.prochain_test.matieres.length > 0
              ? tr('Toute la matière {noms}', {
                  noms: today.prochain_test.matieres.join(', '),
                })
              : today.prochain_test.cours.join(', ')}{' '}
            ·{' '}
            {new Date(today.prochain_test.date).toLocaleDateString(
              localeAffichage(),
            )}{' '}
            ·{' '}
            <span
              className={
                (today.jours_avant_test ?? 99) <= 7
                  ? 'text-difficile'
                  : undefined
              }
            >
              {trn(today.jours_avant_test ?? 0, 'dans {n} jour', 'dans {n} jours')}
            </span>
          </p>
        </Carte>
      ) : (
        <Carte>
          <p className="text-sm text-texte-doux">
            {tr('Aucun test à venir.')}{' '}
            <Link to="/tests" className="text-accent hover:underline">
              {tr('En ajouter un')}
            </Link>{' '}
            {tr("pour que le planning s'adapte.")}
          </p>
        </Carte>
      )}
    </div>
  )
}
