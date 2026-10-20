import { useEffect, useState } from 'react'

import { type Cours, type Stats, api } from '../api'
import { pts, questionsEtPoints } from '../format'
import { localeAffichage, tr, trn } from '../i18n'
import {
  Carte,
  Chargement,
  Erreur,
  Etiquette,
  Pastille,
  Vide,
} from '../composants/ui'

/** Charge prévue en POINTS par jour: mode test en accent, entretien en violet clair. */
function Graphique({
  charge,
  plafond,
}: {
  charge: Stats['charge_14j']
  plafond: number
}) {
  const max = Math.max(1, plafond, ...charge.map((c) => c.points))

  return (
    <div className="relative">
      <div
        className="flex h-44 items-end gap-1.5"
        role="img"
        aria-label={tr('Charge prévue sur 14 jours, en points')}
      >
        {charge.map((c) => {
          const test = Math.max(0, c.points - c.points_entretien)
          const jour = new Date(c.jour)
          return (
            <div
              key={c.jour}
              className="group flex h-full flex-1 flex-col items-center gap-2"
              title={tr('{q}, {p} pts (dont entretien {e})', {
                q: trn(c.nb, '{n} question', '{n} questions'),
                p: pts(c.points),
                e: pts(c.points_entretien),
              })}
            >
              <div className="relative flex w-full flex-1 flex-col justify-end">
                <div
                  className="w-full bg-[#B279A2]/80 transition-all duration-300 ease-[var(--ease-doux)] group-hover:bg-[#B279A2]"
                  style={{ height: `${(c.points_entretien / max) * 100}%` }}
                />
                <div
                  className="w-full rounded-t bg-accent/70 transition-all duration-300 ease-[var(--ease-doux)] group-hover:bg-accent"
                  style={{ height: `${(test / max) * 100}%` }}
                />
                <span className="absolute -top-5 left-1/2 -translate-x-1/2 whitespace-nowrap text-[10px] tabular-nums text-texte-doux opacity-0 transition-opacity group-hover:opacity-100">
                  {pts(c.points)}
                </span>
              </div>
              <span className="text-[10px] text-texte-doux">
                {jour.toLocaleDateString(localeAffichage(), { weekday: 'narrow' })}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function TableauDeBord() {
  const [stats, setStats] = useState<Stats | null>(null)
  const [cours, setCours] = useState<Cours[]>([])
  const [filtre, setFiltre] = useState('')
  const [erreur, setErreur] = useState<string | null>(null)

  useEffect(() => {
    api.cours().then(setCours).catch(() => {})
  }, [])

  useEffect(() => {
    api
      .stats(undefined, filtre || undefined)
      .then(setStats)
      .catch((e) => setErreur(e.message))
  }, [filtre])

  if (erreur) return <Erreur message={erreur} />
  if (!stats) return <Chargement />

  const couleurs = Object.fromEntries(cours.map((c) => [c.nom, c.couleur]))

  return (
    <div className="animer-entree space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-3xl font-semibold tracking-tight">
          {tr('Tableau de bord')}
        </h1>
        <select
          value={filtre}
          onChange={(e) => setFiltre(e.target.value)}
          className="min-h-11 rounded-xl border border-bordure bg-surface px-3.5 text-sm text-texte focus:border-accent focus:outline-none"
        >
          <option value="">{tr('Tous les cours')}</option>
          {cours.map((c) => (
            <option key={c.nom} value={c.nom}>
              {c.nom}
            </option>
          ))}
        </select>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[
          [tr('Cartes'), String(stats.total_cartes), undefined],
          [
            tr('Dues aujourd’hui'),
            String(stats.nb_dues),
            tr('environ {p} points', { p: pts(stats.points_dus) }),
          ],
          [
            tr('En retard'),
            String(stats.nb_en_retard),
            undefined,
            stats.nb_en_retard > 0 ? '#F5B84A' : undefined,
          ],
          [
            tr('Thèmes en entretien'),
            `${stats.themes_entretien.length}/${stats.nb_themes}`,
            tr('{pct} % de la charge du jour', {
              pct: Math.round(stats.part_entretien * 100),
            }),
            '#B279A2',
          ],
        ].map(([libelle, valeur, detail, couleur]) => (
          <Carte key={String(libelle)}>
            <p
              className="text-3xl font-semibold tabular-nums"
              style={{ color: couleur as string | undefined }}
            >
              {valeur as string}
            </p>
            <p className="mt-1 text-sm text-texte-doux">{libelle as string}</p>
            {detail && (
              <p className="mt-0.5 text-xs text-texte-doux">{detail as string}</p>
            )}
          </Carte>
        ))}
      </div>

      {stats.alertes.map((a) => (
        <p
          key={a}
          role="alert"
          className="rounded-xl border border-difficile/40 bg-difficile/10 px-3.5 py-2.5 text-sm text-difficile"
        >
          {a}
        </p>
      ))}

      <Carte className="space-y-4">
        <div>
          <p className="font-medium">{tr('Charge prévue sur 14 jours')}</p>
          <p className="mt-1 text-sm text-texte-doux">
            {tr('Estimation si tu réponds juste à chaque fois.')}
          </p>
        </div>
        <Graphique charge={stats.charge_14j} plafond={stats.plafond} />
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-texte-doux">
          <span>
            <span className="mr-1.5 inline-block size-2.5 rounded-sm bg-accent/70" />
            {tr('mode test et nouvelles cartes')}
          </span>
          <span>
            <span className="mr-1.5 inline-block size-2.5 rounded-sm bg-[#B279A2]/80" />
            {tr('entretien')}
          </span>
          <span>
            {tr('plafond quotidien : {p} points (échelle du graphique)', {
              p: pts(stats.plafond),
            })}
          </span>
        </div>
      </Carte>

      <Carte className="space-y-4">
        <div>
          <p className="font-medium">{tr('Entretien à venir')}</p>
          <p className="mt-1 text-sm text-texte-doux">
            {tr("Charge d'entretien prévue sur 14 jours :")}{' '}
            {questionsEtPoints(
              stats.charge_14j.reduce((n, c) => n + c.nb_entretien, 0),
              stats.charge_14j.reduce((n, c) => n + c.points_entretien, 0),
            )}
            .
          </p>
        </div>
        {stats.quotas.length === 0 ? (
          <p className="text-sm text-texte-doux">
            {tr('Aucun thème en entretien : tous ont un test à venir.')}
          </p>
        ) : (
          <ul className="space-y-2.5">
            {stats.quotas.map((q) => {
              const fait = Math.min(q.quota, q.fait_avant_aujourdhui)
              return (
                <li key={q.theme} className="space-y-1.5">
                  <div className="flex items-center gap-3 text-sm">
                    <Pastille couleur={couleurs[q.theme] ?? '#B279A2'} />
                    <span className="flex-1 truncate">{q.theme}</span>
                    <span className="tabular-nums text-texte-doux">
                      {tr('{fait}/{quota} pts cette semaine', {
                        fait: pts(fait),
                        quota: pts(q.quota),
                      })}
                    </span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-surface-haute">
                    <div
                      className="h-full rounded-full bg-[#B279A2]"
                      style={{ width: `${(fait / q.quota) * 100}%` }}
                    />
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </Carte>

      <section className="space-y-3">
        <h2 className="font-medium">{tr('Tests à venir')}</h2>
        {stats.tests.length === 0 ? (
          <Vide titre={tr('Aucun test à venir.')} />
        ) : (
          stats.tests.map((t) => {
            const jours = Math.round(
              (new Date(t.test.date).getTime() - new Date(stats.jour).getTime()) /
                86400000,
            )
            return (
              <Carte key={t.test.id} className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      <Pastille couleur={couleurs[t.test.cours[0]] ?? '#8B7CFF'} />
                      <span className="truncate">{t.test.intitule}</span>
                    </p>
                    <p className="mt-1 text-sm text-texte-doux">
                      {t.test.matieres.length > 0
                        ? `${tr('Toute la matière {noms}', { noms: t.test.matieres.join(', ') })} · `
                        : `${t.test.cours.join(', ')} · `}
                      {new Date(t.test.date).toLocaleDateString(localeAffichage())}
                    </p>
                  </div>
                  <Etiquette couleur={jours <= 7 ? '#F5B84A' : undefined}>
                    {tr('dans {n} j', { n: jours })}
                  </Etiquette>
                </div>

                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
                  <span className="text-texte-doux">
                    {trn(t.nb_cartes, '{n} carte', '{n} cartes')}
                  </span>
                  {t.nb_jamais_revisees > 0 && (
                    <span className="text-difficile">
                      {trn(
                        t.nb_jamais_revisees,
                        '{n} jamais révisée',
                        '{n} jamais révisées',
                      )}
                    </span>
                  )}
                  <span className="ml-auto">
                    <span className="text-texte-doux">
                      {tr('Rétention estimée')}{' '}
                    </span>
                    <span
                      className="font-medium tabular-nums"
                      style={{
                        color:
                          t.retention_estimee >= 0.9
                            ? '#4ADE9F'
                            : t.retention_estimee >= 0.75
                              ? '#F5B84A'
                              : '#FF6B7A',
                      }}
                    >
                      {(t.retention_estimee * 100).toFixed(0)} %
                    </span>
                  </span>
                </div>
              </Carte>
            )
          })
        )}
      </section>
    </div>
  )
}
