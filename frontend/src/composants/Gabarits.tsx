/** Un gabarit d'affichage par type de question.
 *
 * Chaque gabarit décide comment poser la question et comment la corriger,
 * puis remonte une note FSRS (1..4) via `onNote`. Toute la planification
 * reste côté serveur.
 */
import { type ReactNode, useEffect, useRef, useState } from 'react'

import { type EtatJuge, type Jugement, type Payload, api } from '../api'
import { langue, tr } from '../i18n'
import { Bouton, Champ, Texte } from './ui'

export type Note = 1 | 2 | 3 | 4
export interface Resultat {
  note: Note
  correct: boolean | null
  source: 'auto' | 'manuel' | 'juge'
}

// `libelle` est une clé de traduction, traduite à l'affichage.
const BOUTONS_NOTE: { note: Note; libelle: string; classe: string }[] = [
  { note: 1, libelle: 'Raté', classe: 'bg-rate/15 text-rate border-rate/40' },
  {
    note: 2,
    libelle: 'Difficile',
    classe: 'bg-difficile/15 text-difficile border-difficile/40',
  },
  { note: 3, libelle: 'Bien', classe: 'bg-bien/15 text-bien border-bien/40' },
  {
    note: 4,
    libelle: 'Facile',
    classe: 'bg-facile/15 text-facile border-facile/40',
  },
]

export function BoutonsNote({
  onNote,
  titre = tr('Comment ça a été ?'),
  suggestion,
}: {
  onNote: (n: Note) => void
  titre?: string
  /** Note proposée par la correction automatique: mise en avant, jamais imposée. */
  suggestion?: Note
}) {
  return (
    <div className="animer-entree space-y-2.5">
      <p className="text-xs text-texte-doux">{titre}</p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {BOUTONS_NOTE.map((b) => (
          <button
            key={b.note}
            data-note={b.note}
            onClick={() => onNote(b.note)}
            className={`min-h-14 rounded-xl border text-sm font-medium transition-all duration-150 hover:brightness-125 ${b.classe} ${
              suggestion === b.note ? 'ring-2 ring-current ring-offset-2 ring-offset-fond' : ''
            }`}
            aria-label={
              suggestion === b.note
                ? `${tr(b.libelle)} (${tr('suggérée par la correction')})`
                : undefined
            }
          >
            {tr(b.libelle)}
            <span className="ml-1.5 opacity-50">{b.note}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

function Enonce({ children }: { children: string }) {
  return (
    <h2 className="text-xl leading-relaxed font-medium sm:text-2xl">
      <Texte>{children}</Texte>
    </h2>
  )
}

function Verdict({ juste, children }: { juste: boolean; children?: ReactNode }) {
  return (
    <div
      className={`animer-entree rounded-xl border px-4 py-3 ${
        juste
          ? 'border-bien/40 bg-bien/10 text-bien'
          : 'border-rate/40 bg-rate/10 text-rate'
      }`}
      role="status"
    >
      <p className="font-medium">{juste ? tr('Juste') : tr('Faux')}</p>
      {children && <div className="mt-1.5 text-texte">{children}</div>}
    </div>
  )
}

interface Props {
  cardId: string
  payload: Payload
  /** État de la correction par IA (voir `Queue.juge`). */
  juge: EtatJuge
  revele: boolean
  onRevele: () => void
  onResultat: (r: Resultat) => void
}

/**
 * Correction d'une réponse libre ou d'une rédaction par le juge (côté serveur).
 * Un refus attendu (juge inactif, plafond, panne) revient sans exception: `raison`
 * est alors renseignée et le gabarit retombe sur l'auto-évaluation.
 */
function useCorrection(cardId: string) {
  const [jugement, setJugement] = useState<Jugement | null>(null)
  const [raison, setRaison] = useState<string | null>(null)
  const [enCours, setEnCours] = useState(false)

  async function corriger(reponse: string): Promise<Jugement | null> {
    setEnCours(true)
    setRaison(null)
    try {
      const j = await api.juger({ card_id: cardId, reponse, langue: langue() })
      setJugement(j.disponible ? j : null)
      setRaison(j.disponible ? null : (j.raison ?? tr('Correction indisponible.')))
      return j.disponible ? j : null
    } catch (e) {
      setRaison(e instanceof Error ? e.message : tr('Erreur'))
      return null
    } finally {
      setEnCours(false)
    }
  }

  return { jugement, raison, enCours, corriger }
}

/** Une rédaction plus courte ne se corrige pas (rien à juger, rien à payer). */
const MIN_CARACTERES_REDACTION = 20

const STYLE_VERDICT = {
  juste: 'border-bien/40 bg-bien/10 text-bien',
  partiel: 'border-difficile/40 bg-difficile/10 text-difficile',
  faux: 'border-rate/40 bg-rate/10 text-rate',
} as const
const LIBELLE_VERDICT = {
  juste: 'Correct',
  partiel: 'Partiellement juste',
  faux: 'Incorrect',
} as const

/** Verdict du juge (avec son commentaire), ou la raison de son absence. */
function EncartCorrection({
  jugement,
  raison,
}: {
  jugement: Jugement | null
  raison: string | null
}) {
  if (jugement) {
    const style = jugement.verdict
      ? STYLE_VERDICT[jugement.verdict]
      : 'border-bordure bg-surface-haute text-texte'
    return (
      <div className={`animer-entree rounded-xl border px-4 py-3 ${style}`} role="status">
        <p className="font-medium">
          {jugement.verdict
            ? tr(LIBELLE_VERDICT[jugement.verdict])
            : tr('Correction par IA')}
          {jugement.etat === 'simule' && (
            <span className="ml-2 text-xs opacity-70">({tr('correction simulée')})</span>
          )}
        </p>
        {jugement.commentaire && (
          <p className="mt-1.5 text-sm text-texte">{jugement.commentaire}</p>
        )}
      </div>
    )
  }
  if (raison) {
    return (
      <p
        role="status"
        className="rounded-xl border border-difficile/40 bg-difficile/10 px-4 py-3 text-sm text-difficile"
      >
        {tr('Correction par IA indisponible : {raison}', { raison })}{' '}
        {tr('Évalue-toi toi-même.')}
      </p>
    )
  }
  return null
}

/** Suite d'une réponse jugée fausse par la comparaison automatique.
 *  « Continuer » enregistre un raté; « J'avais juste » laisse l'élève contester
 *  (faute de frappe, de syntaxe ou de formulation qui ne mérite pas un zéro) et
 *  choisir sa note: la réponse est alors comptée juste, source « manuel ». */
function SuiteErreur({ onResultat }: { onResultat: (r: Resultat) => void }) {
  const [conteste, setConteste] = useState(false)

  if (conteste) {
    return (
      <BoutonsNote
        onNote={(note) => onResultat({ note, correct: true, source: 'manuel' })}
        titre={tr('Tu avais juste: quelle note te donnes-tu ?')}
      />
    )
  }
  return (
    <div className="space-y-2.5">
      <Bouton
        variante="neutre"
        className="w-full"
        onClick={() => onResultat({ note: 1, correct: false, source: 'auto' })}
      >
        {tr('Continuer')}
      </Bouton>
      <Bouton variante="neutre" className="w-full" onClick={() => setConteste(true)}>
        {tr("J'avais juste quand même")}
      </Bouton>
    </div>
  )
}

/** QCM: propositions cliquables, correction immédiate. */
function GabaritQcm({ payload, revele, onRevele, onResultat }: Props) {
  if (payload.type !== 'qcm') return null
  const [choisis, setChoisis] = useState<number[]>([])
  const multiple = payload.bonnes_reponses.length > 1

  const basculer = (i: number) => {
    if (revele) return
    setChoisis((c) =>
      multiple
        ? c.includes(i)
          ? c.filter((x) => x !== i)
          : [...c, i]
        : [i],
    )
  }

  const juste =
    choisis.length === payload.bonnes_reponses.length &&
    choisis.every((c) => payload.bonnes_reponses.includes(c))

  return (
    <div className="space-y-6">
      <Enonce>{payload.enonce}</Enonce>
      {multiple && (
        <p className="text-xs text-texte-doux">
          {tr('Plusieurs réponses possibles.')}
        </p>
      )}

      <div className="space-y-2.5">
        {payload.propositions.map((p, i) => {
          const selectionne = choisis.includes(i)
          const bonne = payload.bonnes_reponses.includes(i)
          let style =
            'border-bordure bg-surface hover:border-accent/60 hover:bg-surface-haute'
          if (revele && bonne) style = 'border-bien/50 bg-bien/10'
          else if (revele && selectionne) style = 'border-rate/50 bg-rate/10'
          else if (selectionne) style = 'border-accent bg-accent-doux'

          return (
            <button
              key={i}
              onClick={() => basculer(i)}
              disabled={revele}
              aria-pressed={selectionne}
              className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3.5 text-left transition-all duration-150 ${style}`}
            >
              <span className="mt-0.5 font-mono text-xs text-texte-doux">
                {String.fromCharCode(65 + i)}
              </span>
              <span className="flex-1">
                <Texte>{p}</Texte>
              </span>
              {revele && bonne && <span className="text-bien">✓</span>}
            </button>
          )
        })}
      </div>

      {!revele ? (
        <Bouton
          variante="accent"
          className="w-full"
          disabled={choisis.length === 0}
          onClick={onRevele}
        >
          {tr('Valider')}
        </Bouton>
      ) : (
        <>
          <Verdict juste={juste} />
          {juste ? (
            <BoutonsNote
              onNote={(note) =>
                onResultat({ note, correct: true, source: 'auto' })
              }
              titre={tr("C'était comment ?")}
            />
          ) : (
            <SuiteErreur onResultat={onResultat} />
          )}
        </>
      )}
    </div>
  )
}

/** Exact: saisie, comparaison stricte. */
function GabaritExact({ payload, revele, onRevele, onResultat }: Props) {
  if (payload.type !== 'exact') return null
  const [saisie, setSaisie] = useState('')
  const champ = useRef<HTMLInputElement>(null)

  useEffect(() => {
    champ.current?.focus()
  }, [])

  const juste = saisie.trim() === payload.reponse_exacte.trim()

  return (
    <div className="space-y-6">
      <Enonce>{payload.enonce}</Enonce>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (!revele && saisie.trim()) onRevele()
        }}
      >
        <Champ
          ref={champ}
          value={saisie}
          onChange={(e) => setSaisie(e.target.value)}
          disabled={revele}
          placeholder={tr('Ta réponse')}
          autoComplete="off"
          autoCapitalize="off"
          className="text-lg"
        />
      </form>

      {!revele ? (
        <Bouton
          variante="accent"
          className="w-full"
          disabled={!saisie.trim()}
          onClick={onRevele}
        >
          {tr('Valider')}
        </Bouton>
      ) : (
        <>
          <Verdict juste={juste}>
            {!juste && (
              <>
                {tr('Réponse attendue :')}{' '}
                <span className="font-mono">{payload.reponse_exacte}</span>
              </>
            )}
          </Verdict>
          {juste ? (
            <BoutonsNote
              onNote={(note) =>
                onResultat({ note, correct: true, source: 'auto' })
              }
              titre={tr("C'était comment ?")}
            />
          ) : (
            <SuiteErreur onResultat={onResultat} />
          )}
        </>
      )}
    </div>
  )
}

/** Cloze: un champ par trou, correction stricte sur chaque trou. */
function GabaritCloze({ payload, revele, onRevele, onResultat }: Props) {
  if (payload.type !== 'cloze') return null
  const cles = Object.keys(payload.valeurs_attendues)
  const [valeurs, setValeurs] = useState<Record<string, string>>({})

  const juste = cles.every(
    (c) =>
      (valeurs[c] ?? '').trim() === payload.valeurs_attendues[c].trim(),
  )
  const complet = cles.every((c) => (valeurs[c] ?? '').trim())

  const masque = payload.texte.replace(
    /\{\{(c\d+)::(.*?)\}\}/g,
    (_, cle) => `[${cle}]`,
  )

  return (
    <div className="space-y-6">
      <p className="text-xl leading-relaxed sm:text-2xl">
        <Texte>{masque}</Texte>
      </p>

      <div className="space-y-3">
        {cles.map((cle) => {
          const bon =
            (valeurs[cle] ?? '').trim() ===
            payload.valeurs_attendues[cle].trim()
          return (
            <label key={cle} className="flex items-center gap-3">
              <span className="w-10 shrink-0 font-mono text-sm text-texte-doux">
                {cle}
              </span>
              <Champ
                value={valeurs[cle] ?? ''}
                onChange={(e) =>
                  setValeurs((v) => ({ ...v, [cle]: e.target.value }))
                }
                disabled={revele}
                autoComplete="off"
                className={
                  revele
                    ? bon
                      ? 'border-bien/50'
                      : 'border-rate/50'
                    : undefined
                }
              />
              {revele && !bon && (
                <span className="shrink-0 font-mono text-sm text-bien">
                  {payload.valeurs_attendues[cle]}
                </span>
              )}
            </label>
          )
        })}
      </div>

      {!revele ? (
        <Bouton
          variante="accent"
          className="w-full"
          disabled={!complet}
          onClick={onRevele}
        >
          {tr('Valider')}
        </Bouton>
      ) : (
        <>
          <Verdict juste={juste} />
          {juste ? (
            <BoutonsNote
              onNote={(note) =>
                onResultat({ note, correct: true, source: 'auto' })
              }
              titre={tr("C'était comment ?")}
            />
          ) : (
            <SuiteErreur onResultat={onResultat} />
          )}
        </>
      )}
    </div>
  )
}

/** Problème: résolution sur feuille, révélation, auto-évaluation. */
function GabaritProbleme({ payload, revele, onRevele, onResultat }: Props) {
  if (payload.type !== 'probleme') return null

  return (
    <div className="space-y-6">
      <Enonce>{payload.enonce}</Enonce>

      {payload.necessite_dessin && (
        <p className="rounded-xl border border-difficile/40 bg-difficile/10 px-4 py-3 text-sm text-difficile">
          {tr('Fais le schéma sur une feuille.')}
        </p>
      )}

      {!revele ? (
        <Bouton variante="accent" className="w-full" onClick={onRevele}>
          {tr('Voir la solution')}
        </Bouton>
      ) : (
        <div className="animer-entree space-y-5">
          <div className="space-y-3 rounded-xl border border-bordure bg-surface-haute p-4">
            <p className="text-xs uppercase tracking-wide text-texte-doux">
              {tr('Solution')}
            </p>
            <ol className="space-y-2.5">
              {payload.solution_etapes.map((etape, i) => (
                <li key={i} className="flex gap-3 text-sm">
                  <span className="font-mono text-texte-doux">{i + 1}.</span>
                  <span className="flex-1">
                    <Texte>{etape}</Texte>
                  </span>
                </li>
              ))}
            </ol>
            <p className="border-t border-bordure pt-3 text-sm">
              <span className="text-texte-doux">{tr('Réponse :')} </span>
              <span className="font-medium">
                <Texte>{payload.reponse_finale}</Texte>
              </span>
            </p>
          </div>

          {payload.schema_svg && (
            <div className="overflow-x-auto rounded-xl border border-bordure bg-white p-4">
              {/* Le SVG vient du générateur (ou d'un fichier importé): c'est
                  une donnée non fiable. Rendu comme IMAGE, jamais injecté dans
                  la page: dans un <img>, un SVG ne peut ni exécuter de script
                  ni charger de ressource externe. */}
              <img
                alt={tr('Schéma de référence')}
                className="mx-auto max-w-full"
                src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(payload.schema_svg)}`}
              />
            </div>
          )}

          <BoutonsNote
            onNote={(note) =>
              onResultat({ note, correct: null, source: 'manuel' })
            }
          />
        </div>
      )}
    </div>
  )
}

/** Réponse libre: saisie, puis correction par l'IA (si activée) ou auto-évaluation.
 *  « Voir la réponse attendue » reste toujours possible: gratuit, sans IA. */
function GabaritLibre({ cardId, payload, juge, revele, onRevele, onResultat }: Props) {
  const correction = useCorrection(cardId)
  if (payload.type !== 'libre_llm') return null
  const [saisie, setSaisie] = useState('')
  const { jugement } = correction
  const peutCorriger = juge !== 'indisponible' && saisie.trim().length > 0

  return (
    <div className="space-y-6">
      <Enonce>{payload.enonce}</Enonce>

      <textarea
        value={saisie}
        onChange={(e) => setSaisie(e.target.value)}
        disabled={revele || correction.enCours}
        rows={5}
        placeholder={tr('Ta réponse, avec tes mots')}
        className="w-full rounded-xl border border-bordure bg-surface px-3.5 py-3 text-texte placeholder:text-texte-doux/60 focus:border-accent focus:outline-none"
      />

      {!revele ? (
        <div className="space-y-2.5">
          {peutCorriger && (
            <Bouton
              variante="accent"
              className="w-full"
              disabled={correction.enCours}
              onClick={async () => {
                await correction.corriger(saisie)
                onRevele()
              }}
            >
              {correction.enCours ? tr('Correction en cours…') : tr("Corriger avec l'IA")}
            </Bouton>
          )}
          <Bouton
            variante={peutCorriger ? 'neutre' : 'accent'}
            className="w-full"
            disabled={correction.enCours}
            onClick={onRevele}
          >
            {tr('Voir la réponse attendue')}
          </Bouton>
        </div>
      ) : (
        <div className="animer-entree space-y-5">
          <EncartCorrection jugement={jugement} raison={correction.raison} />
          <div className="rounded-xl border border-bordure bg-surface-haute p-4">
            <p className="text-xs uppercase tracking-wide text-texte-doux">
              {tr('Réponse attendue')}
            </p>
            <p className="mt-2 text-sm">
              <Texte>{payload.reponse_attendue}</Texte>
            </p>
          </div>
          <BoutonsNote
            suggestion={(jugement?.note_suggeree as Note | null | undefined) ?? undefined}
            onNote={(note) =>
              onResultat(
                jugement && note === jugement.note_suggeree
                  ? {
                      note,
                      correct:
                        jugement.verdict === 'juste'
                          ? true
                          : jugement.verdict === 'faux'
                            ? false
                            : null,
                      source: 'juge',
                    }
                  : { note, correct: null, source: 'manuel' },
              )
            }
            titre={
              jugement
                ? tr('La correction te propose une note. Tu peux la changer.')
                : tr('Compare avec ta réponse et évalue-toi.')
            }
          />
        </div>
      )}
    </div>
  )
}

/** Rédaction: l'élève produit un texte, puis coche les critères remplis.
 *  Le barème (tous = Bien, la plupart = Difficile, peu = Raté) est le même
 *  que côté moteur — c'est `grading.rating_depuis_criteres`. */
function GabaritRedaction({ cardId, payload, juge, revele, onRevele, onResultat }: Props) {
  const correction = useCorrection(cardId)
  if (payload.type !== 'redaction') return null
  const [texte, setTexte] = useState('')
  const [coches, setCoches] = useState<boolean[]>(
    () => payload.criteres.map(() => false),
  )
  const { jugement } = correction
  // Trop court pour être jugé: on n'envoie rien (et on ne paie rien).
  const peutCorriger = juge !== 'indisponible' && texte.trim().length >= MIN_CARACTERES_REDACTION

  const nbCoches = coches.filter(Boolean).length
  const total = payload.criteres.length
  const tous = nbCoches === total

  const noter = (facile: boolean) => {
    const note: Note = tous ? (facile ? 4 : 3) : nbCoches * 2 >= total ? 2 : 1
    // « juge »: la note vient telle quelle de la correction. Dès que l'élève a
    // changé un critère ou la note, c'est son jugement: « manuel ».
    const intacte =
      jugement?.criteres != null &&
      jugement.criteres.length === coches.length &&
      jugement.criteres.every((v, i) => v === coches[i]) &&
      note === jugement.note_suggeree
    onResultat({ note, correct: null, source: intacte ? 'juge' : 'manuel' })
  }

  return (
    <div className="space-y-6">
      <h2 className="text-xl leading-relaxed font-medium sm:text-2xl">
        <Texte>{payload.consigne}</Texte>
      </h2>

      <p className="rounded-xl border border-bordure bg-surface-haute px-4 py-3 text-sm text-texte-doux">
        {payload.contraintes}
      </p>

      {juge !== 'indisponible' && (
        <textarea
          value={texte}
          onChange={(e) => setTexte(e.target.value)}
          disabled={revele || correction.enCours}
          rows={10}
          placeholder={tr('Ton texte (pour la correction par IA — ou rédige sur feuille)')}
          className="w-full rounded-xl border border-bordure bg-surface px-3.5 py-3 text-texte placeholder:text-texte-doux/60 focus:border-accent focus:outline-none"
        />
      )}

      {!revele ? (
        <div className="space-y-2.5">
          {peutCorriger && (
            <Bouton
              variante="accent"
              className="w-full"
              disabled={correction.enCours}
              onClick={async () => {
                const j = await correction.corriger(texte)
                // Les critères cochés par le juge sont un point de départ: l'élève ajuste.
                if (j?.criteres && j.criteres.length === payload.criteres.length) {
                  setCoches(j.criteres)
                }
                onRevele()
              }}
            >
              {correction.enCours ? tr('Correction en cours…') : tr("Corriger avec l'IA")}
            </Bouton>
          )}
          <Bouton
            variante={peutCorriger ? 'neutre' : 'accent'}
            className="w-full"
            disabled={correction.enCours}
            onClick={onRevele}
          >
            {tr("J'ai rédigé — voir l'exemple et les critères")}
          </Bouton>
        </div>
      ) : (
        <div className="animer-entree space-y-5">
          <EncartCorrection jugement={jugement} raison={correction.raison} />
          <div className="rounded-xl border border-bordure bg-surface-haute p-4">
            <p className="text-xs uppercase tracking-wide text-texte-doux">
              {tr('Exemple de référence')}
            </p>
            <p className="mt-2 text-sm">
              <Texte>{payload.exemple_reference}</Texte>
            </p>
          </div>

          <fieldset className="space-y-2.5">
            <legend className="mb-1 text-xs text-texte-doux">
              {tr('Coche les critères que ta copie remplit ({a}/{b})', {
                a: nbCoches,
                b: total,
              })}
            </legend>
            {payload.criteres.map((critere, i) => (
              <label
                key={i}
                className={`flex min-h-12 cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 text-sm transition-colors ${
                  coches[i]
                    ? 'border-bien/50 bg-bien/10'
                    : 'border-bordure bg-surface hover:border-accent/60'
                }`}
              >
                <input
                  type="checkbox"
                  checked={coches[i]}
                  onChange={(e) =>
                    setCoches((c) =>
                      c.map((v, j) => (j === i ? e.target.checked : v)),
                    )
                  }
                  className="size-5 shrink-0 accent-[var(--color-accent)]"
                />
                <span className="flex-1">
                  <Texte>{critere}</Texte>
                </span>
              </label>
            ))}
          </fieldset>

          {tous ? (
            <div className="grid grid-cols-2 gap-2">
              <button
                data-note={3}
                onClick={() => noter(false)}
                className="min-h-14 rounded-xl border border-bien/40 bg-bien/15 text-sm font-medium text-bien transition-all hover:brightness-125"
              >
                {tr('Tous les critères — Bien')}
              </button>
              <button
                data-note={4}
                onClick={() => noter(true)}
                className="min-h-14 rounded-xl border border-facile/40 bg-facile/15 text-sm font-medium text-facile transition-all hover:brightness-125"
              >
                {tr("… et c'était facile")}
              </button>
            </div>
          ) : (
            <Bouton variante="neutre" className="w-full" onClick={() => noter(false)}>
              {tr('Valider mon auto-évaluation ({a}/{b})', { a: nbCoches, b: total })}
            </Bouton>
          )}
        </div>
      )}
    </div>
  )
}

const GABARITS = {
  qcm: GabaritQcm,
  exact: GabaritExact,
  cloze: GabaritCloze,
  probleme: GabaritProbleme,
  libre_llm: GabaritLibre,
  redaction: GabaritRedaction,
}

export function Gabarit(props: Props) {
  const Composant = GABARITS[props.payload.type]
  return <Composant {...props} />
}
