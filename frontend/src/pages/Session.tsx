import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { type QuestionSession, type Queue, api } from '../api'
import { Gabarit, type Resultat } from '../composants/Gabarits'
import { Recap } from '../composants/Recap'
import { libellePtsCourt, pts } from '../format'
import { tr, trn } from '../i18n'
import {
  BarreProgression,
  Bouton,
  Chargement,
  Erreur,
  Etiquette,
  Pastille,
} from '../composants/ui'

type Bilan = { repondues: number; passees: number; justes: number; points: number }

// `libelle` est une clé de traduction, traduite à l'affichage.
const RAISONS = [
  { valeur: 'feuille', libelle: 'Il me faut une feuille' },
  { valeur: 'redaction', libelle: 'Rédaction trop longue' },
  { valeur: 'pas_le_temps', libelle: "Pas le temps là" },
] as const

export default function Session() {
  const [params] = useSearchParams()
  const navigate = useNavigate()

  const [queue, setQueue] = useState<Queue | null>(null)
  const [file, setFile] = useState<QuestionSession[]>([])
  const [index, setIndex] = useState(0)
  const [revele, setRevele] = useState(false)
  const [menuSkip, setMenuSkip] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)
  const [fini, setFini] = useState(false)
  // Thèmes dont le récap a déjà été lu (ou passé) dans cette session.
  const [recapsVus, setRecapsVus] = useState<string[]>([])
  const [bilan, setBilan] = useState<Bilan>({
    repondues: 0,
    passees: 0,
    justes: 0,
    points: 0,
  })
  const debut = useRef<number>(Date.now())

  useEffect(() => {
    api
      .session({
        ordre: params.get('ordre') ?? undefined,
        cours: params.get('cours') ?? undefined,
      })
      .then((q) => {
        setQueue(q)
        setFile(q.questions)
        if (q.questions.length === 0) setFini(true)
      })
      .catch((e) => setErreur(e.message))
  }, [params])

  const courante = file[index]
  const total = file.length

  // Mode « par cours »: à l'entrée dans un thème, son récap passe avant la
  // première question. Un thème déjà vu (ou une question passée qui revient)
  // ne le réaffiche pas.
  const recaps = queue?.recaps ?? []
  const recapAVoir =
    courante && !recapsVus.includes(courante.cours)
      ? recaps.find((r) => r.cours === courante.cours)
      : undefined

  const passerRecap = useCallback(() => {
    if (!recapAVoir) return
    setRecapsVus((v) => [...v, recapAVoir.cours])
    debut.current = Date.now()
  }, [recapAVoir])

  const avancer = useCallback(() => {
    setRevele(false)
    setMenuSkip(false)
    debut.current = Date.now()
    setIndex((i) => {
      if (i + 1 >= file.length) {
        setFini(true)
        return i
      }
      return i + 1
    })
  }, [file.length])

  async function noter(r: Resultat) {
    if (!courante) return
    try {
      await api.repondre({
        card_id: courante.card_id,
        rating: r.note,
        correct: r.correct,
        source: r.source,
        duration_ms: Date.now() - debut.current,
      })
      setBilan((b) => ({
        ...b,
        repondues: b.repondues + 1,
        justes: b.justes + (r.correct === true || r.note >= 3 ? 1 : 0),
        points: b.points + courante.poids,
      }))
      avancer()
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function passer(raison?: string) {
    if (!courante || !queue) return
    try {
      const res = await api.passer({
        card_id: courante.card_id,
        session_id: queue.session_id,
        raison: raison ?? null,
      })
      setBilan((b) => ({ ...b, passees: b.passees + 1 }))

      // Un skip n'est pas une réponse: la question retourne en fin de file
      // tant qu'elle n'a pas atteint la limite de repasses.
      setFile((f) => {
        const restante = f.filter((_, i) => i !== index)
        return res.reviendra ? [...restante, courante] : restante
      })
      setRevele(false)
      setMenuSkip(false)
      debut.current = Date.now()
      setIndex((i) => {
        const nouvelleTaille = res.reviendra ? file.length : file.length - 1
        if (i >= nouvelleTaille) {
          setFini(true)
          return Math.max(0, nouvelleTaille - 1)
        }
        return i
      })
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  // Raccourcis clavier: espace révèle, 1-4 notent, s passe.
  useEffect(() => {
    function onTouche(e: KeyboardEvent) {
      if (fini || !courante) return
      const cible = e.target as HTMLElement
      const saisie =
        cible.tagName === 'INPUT' || cible.tagName === 'TEXTAREA'

      // Page de récap: seule la touche espace / entrée la passe.
      if (recapAVoir) {
        if ((e.key === ' ' || e.key === 'Enter') && !saisie && cible.tagName !== 'BUTTON') {
          e.preventDefault()
          passerRecap()
        }
        return
      }

      if (e.key === 's' && !saisie) {
        e.preventDefault()
        setMenuSkip((m) => !m)
        return
      }
      if (e.key === ' ' && !revele && !saisie) {
        e.preventDefault()
        setRevele(true)
        return
      }
      if (revele && ['1', '2', '3', '4'].includes(e.key)) {
        const boutons = document.querySelectorAll<HTMLButtonElement>(
          '[data-note]',
        )
        boutons[Number(e.key) - 1]?.click()
      }
    }
    window.addEventListener('keydown', onTouche)
    return () => window.removeEventListener('keydown', onTouche)
  }, [revele, fini, courante, recapAVoir, passerRecap])

  if (erreur) {
    return (
      <div className="mx-auto max-w-lg p-6">
        <Erreur message={erreur} />
        <Bouton className="mt-4" onClick={() => navigate('/')}>
          {tr('Retour')}
        </Bouton>
      </div>
    )
  }

  if (!queue) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Chargement texte={tr('Préparation de la session…')} />
      </div>
    )
  }

  if (fini) {
    const reste = file.length - bilan.repondues
    return (
      <main className="flex min-h-dvh items-center justify-center p-6">
        <div className="animer-entree w-full max-w-md space-y-6 text-center">
          <div>
            <p className="text-5xl">✓</p>
            <h1 className="mt-4 text-2xl font-semibold tracking-tight">
              {tr('Session terminée')}
            </h1>
          </div>

          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              [tr('Répondues'), bilan.repondues],
              [tr('Points'), pts(bilan.points)],
              [tr('Justes'), bilan.justes],
              [tr('Passées'), bilan.passees],
            ].map(([libelle, valeur]) => (
              <div
                key={libelle}
                className="rounded-xl border border-bordure bg-surface p-4"
              >
                <dd className="text-2xl font-semibold tabular-nums">
                  {valeur}
                </dd>
                <dt className="mt-1 text-xs text-texte-doux">{libelle}</dt>
              </div>
            ))}
          </dl>

          {bilan.passees > 0 && (
            <p className="text-sm text-texte-doux">
              {tr(
                'Les questions passées restent dues : tu les retrouveras à la prochaine session.',
              )}
            </p>
          )}
          {reste > 0 && bilan.passees === 0 && (
            <p className="text-sm text-texte-doux">
              {trn(
                reste,
                '{n} question non traitée, toujours due.',
                '{n} questions non traitées, toujours dues.',
              )}
            </p>
          )}

          <Link to="/" className="block">
            <Bouton variante="accent" className="w-full">
              {tr("Retour à l'accueil")}
            </Bouton>
          </Link>
        </div>
      </main>
    )
  }

  if (!courante) return <Chargement />

  if (recapAVoir) {
    const rang = recaps.findIndex((r) => r.cours === recapAVoir.cours) + 1
    return (
      <main className="flex min-h-dvh flex-col">
        <header className="sticky top-0 z-10 border-b border-bordure bg-fond/95 backdrop-blur">
          <div className="flex items-center gap-3 px-4 py-3">
            <Link
              to="/"
              className="text-texte-doux transition-colors hover:text-texte"
              aria-label={tr('Quitter la session')}
            >
              ✕
            </Link>
            <div className="flex flex-1 items-center gap-2 overflow-hidden">
              <Pastille couleur={recapAVoir.couleur} />
              <span className="truncate text-sm">{recapAVoir.cours}</span>
            </div>
            <span className="shrink-0 font-mono text-xs text-texte-doux tabular-nums">
              {tr('Récap')} {rang}/{recaps.length}
            </span>
          </div>
        </header>

        <div className="animer-entree mx-auto w-full max-w-2xl flex-1 px-4 py-8">
          <p className="mb-4 text-xs uppercase tracking-wide text-texte-doux">
            {tr('Avant de commencer: rappel théorique')}
          </p>
          <Recap texte={recapAVoir.texte} />
        </div>

        <footer className="zone-sure sticky bottom-0 border-t border-bordure bg-fond/95 px-4 py-3 backdrop-blur">
          <div className="mx-auto flex max-w-2xl items-center gap-4">
            <p className="hidden text-xs text-texte-doux sm:block">
              <kbd className="font-mono">{tr('espace')}</kbd> {tr('passer')}
            </p>
            <Bouton variante="accent" className="flex-1" onClick={passerRecap}>
              {tr('Passer')}
            </Bouton>
          </div>
        </footer>
      </main>
    )
  }

  return (
    <main className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-10 border-b border-bordure bg-fond/95 backdrop-blur">
        <BarreProgression valeur={bilan.repondues} max={bilan.repondues + total} />
        <div className="flex items-center gap-3 px-4 py-3">
          <Link
            to="/"
            className="text-texte-doux transition-colors hover:text-texte"
            aria-label={tr('Quitter la session')}
          >
            ✕
          </Link>
          <div className="flex flex-1 items-center gap-2 overflow-hidden">
            <Pastille couleur={courante.couleur_cours} />
            <span className="truncate text-sm">{courante.cours}</span>
          </div>
          <span className="shrink-0 text-right font-mono text-xs text-texte-doux tabular-nums">
            <span>
              {index + 1}/{total}
            </span>
            {queue && (
              <span className="block">
                {pts(bilan.points)}/{pts(queue.points)} pts
              </span>
            )}
          </span>
        </div>
      </header>

      <div className="mx-auto w-full max-w-2xl flex-1 px-4 py-8">
        <div className="mb-5 flex flex-wrap gap-2">
          {courante.nouvelle && <Etiquette couleur="#5CC8FF">
              {tr('Nouvelle')}
            </Etiquette>}
          {courante.entretien && (
            <Etiquette couleur="#B279A2">{tr('Entretien')}</Etiquette>
          )}
          <Etiquette>{libellePtsCourt(courante.poids)}</Etiquette>
          {courante.jours_retard > 0 && (
            <Etiquette couleur="#F5B84A">
              {tr('En retard de {n} j', { n: courante.jours_retard })}
            </Etiquette>
          )}
        </div>

        <div key={courante.card_id} className="animer-entree">
          <Gabarit
            cardId={courante.card_id}
            juge={queue.juge}
            payload={courante.payload}
            revele={revele}
            onRevele={() => setRevele(true)}
            onResultat={noter}
          />
        </div>
      </div>

      <footer className="zone-sure sticky bottom-0 border-t border-bordure bg-fond/95 px-4 py-3 backdrop-blur">
        {menuSkip ? (
          <div className="animer-entree mx-auto max-w-2xl space-y-2">
            <p className="text-xs text-texte-doux">
              {tr('Pourquoi passer ? (optionnel — aucune pénalité)')}
            </p>
            <div className="flex flex-wrap gap-2">
              {RAISONS.map((r) => (
                <Bouton key={r.valeur} onClick={() => passer(r.valeur)}>
                  {tr(r.libelle)}
                </Bouton>
              ))}
              <Bouton onClick={() => passer()}>{tr('Sans raison')}</Bouton>
              <Bouton variante="fantome" onClick={() => setMenuSkip(false)}>
                {tr('Annuler')}
              </Bouton>
            </div>
          </div>
        ) : (
          <div className="mx-auto flex max-w-2xl items-center justify-between gap-4">
            <p className="hidden text-xs text-texte-doux sm:block">
              <kbd className="font-mono">{tr('espace')}</kbd> {tr('révéler')} ·{' '}
              <kbd className="font-mono">1-4</kbd> {tr('noter')} ·{' '}
              <kbd className="font-mono">s</kbd> {tr('passer')}
            </p>
            <Bouton variante="fantome" onClick={() => setMenuSkip(true)}>
              {tr('Passer')}
            </Bouton>
          </div>
        )}
      </footer>
    </main>
  )
}
