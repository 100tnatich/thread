import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  type ConceptExtrait,
  type Cours,
  type Job,
  type Matiere,
  api,
  suivreJob,
} from '../api'
import { tr, trn } from '../i18n'
import ImportQuestions from './ImportQuestions'
import {
  BarreProgression,
  Bouton,
  Carte,
  Champ,
  Erreur,
  Pastille,
} from '../composants/ui'

type Categorie = ConceptExtrait['categorie']
type Importance = ConceptExtrait['importance']

const CATEGORIES: Categorie[] = [
  'definition',
  'propriete',
  'formule',
  'methode',
  'fait',
  'vocabulaire',
  'exemple_type',
]
const IMPORTANCES: Importance[] = ['essentiel', 'important', 'secondaire']

type Voie = 'texte' | 'pdf' | 'image' | 'import'
const SANS_MATIERE = '__sans_matiere'
type Etape = 'saisie' | 'extraction' | 'validation' | 'generation' | 'fini'

interface ConceptEditable extends ConceptExtrait {
  garde: boolean
}

export default function Ajouter() {
  const [cours, setCours] = useState<Cours[]>([])
  const [matieres, setMatieres] = useState<Matiere[]>([])
  // '' = toutes les matières, SANS_MATIERE = thèmes sans matière.
  const [matiereChoisie, setMatiereChoisie] = useState('')
  const [creation, setCreation] = useState(false)
  const [nomNouveauTheme, setNomNouveauTheme] = useState('')
  const [coursChoisi, setCoursChoisi] = useState('')

  const [voie, setVoie] = useState<Voie>('texte')
  const [texte, setTexte] = useState('')
  const [fichiers, setFichiers] = useState<File[]>([])

  const [etape, setEtape] = useState<Etape>('saisie')
  const [job, setJob] = useState<Job | null>(null)
  const [estimation, setEstimation] = useState<string | null>(null)
  const [concepts, setConcepts] = useState<ConceptEditable[]>([])
  const [avertissements, setAvertissements] = useState<string[]>([])
  const [source, setSource] = useState('')
  const [jobExtraction, setJobExtraction] = useState<string | null>(null)
  const [resultat, setResultat] = useState<Record<string, unknown> | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)

  const inputFichier = useRef<HTMLInputElement>(null)

  useEffect(() => {
    Promise.all([api.cours(), api.matieres()]).then(([c, m]) => {
      setCours(c)
      setMatieres(m)
      const actifs = c.filter((t) => !t.archive)
      if (actifs.length && !coursChoisi) setCoursChoisi(actifs[0].nom)
    })
  }, [])

  const dansLaMatiere = (m: string) => (c: Cours) =>
    !c.archive &&
    (m === '' || (m === SANS_MATIERE ? c.matiere === null : c.matiere === m))
  const themes = cours.filter(dansLaMatiere(matiereChoisie))

  function choisirMatiere(m: string) {
    setMatiereChoisie(m)
    const dispo = cours.filter(dansLaMatiere(m))
    if (!dispo.some((c) => c.nom === coursChoisi)) setCoursChoisi(dispo[0]?.nom ?? '')
  }

  async function creerThemeRapide() {
    const nom = nomNouveauTheme.trim()
    if (!nom) return
    setErreur(null)
    try {
      const parent =
        matiereChoisie && matiereChoisie !== SANS_MATIERE ? matiereChoisie : null
      const cree = await api.creerCours(nom, undefined, parent)
      setCours(await api.cours())
      setCoursChoisi(cree.nom)
      setNomNouveauTheme('')
      setCreation(false)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  const pretAExtraire =
    coursChoisi &&
    (voie === 'texte' ? texte.trim().length > 10 : fichiers.length > 0)

  async function estimer() {
    setErreur(null)
    setEstimation(null)
    try {
      const e = await api.estimerTexte(coursChoisi, texte)
      setEstimation(
        tr(
          "Coût estimé : {cout} USD au plus (~{tokens} tokens en entrée, {modele}). Rien n'a été appelé.",
          {
            cout: e.cout_estime_usd.toFixed(4),
            tokens: e.tokens_entree,
            modele: e.modele,
          },
        ),
      )
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function extraire() {
    if (voie === 'import') return // l'import a son propre parcours
    setErreur(null)
    setEtape('extraction')
    try {
      const { job_id } =
        voie === 'texte'
          ? await api.ingestTexte(coursChoisi, texte)
          : await api.ingestFichiers(coursChoisi, voie, fichiers)

      setJobExtraction(job_id)
      const fini = await suivreJob(job_id, setJob)
      // « interrompu » (redémarrage du serveur) n'est jamais relancé tout
      // seul: on revient à la saisie, c'est à l'utilisateur de décider.
      if (fini.statut !== 'termine') {
        setErreur(fini.message ?? tr("L'extraction a échoué."))
        setEtape('saisie')
        return
      }

      const r = fini.resultat as {
        concepts: ConceptExtrait[]
        avertissements: string[]
        source: string
      }
      setConcepts(r.concepts.map((c) => ({ ...c, garde: true })))
      setAvertissements(r.avertissements)
      setSource(r.source)
      setEtape('validation')
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
      setEtape('saisie')
    }
  }

  async function generer() {
    const gardes = concepts.filter((c) => c.garde)
    if (!gardes.length) {
      setErreur(tr('Garde au moins un concept.'))
      return
    }
    setErreur(null)
    setEtape('generation')
    try {
      const { job_id } = await api.genererQuestions({
        cours: coursChoisi,
        source,
        concepts: gardes.map(({ garde, ...c }) => c),
        // Permet au serveur de rejoindre la matière à la génération.
        extraction_job_id: jobExtraction ?? undefined,
      })
      const fini = await suivreJob(job_id, setJob)
      if (fini.statut !== 'termine') {
        setErreur(fini.message ?? tr('La génération a échoué.'))
        setEtape('validation')
        return
      }
      setResultat(fini.resultat)
      setEtape('fini')
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
      setEtape('validation')
    }
  }

  function recommencer() {
    setEtape('saisie')
    setTexte('')
    setFichiers([])
    setConcepts([])
    setAvertissements([])
    setResultat(null)
    setJob(null)
    setJobExtraction(null)
    setErreur(null)
  }

  return (
    <div className="animer-entree space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">
        {tr('Ajouter de la matière')}
      </h1>

      <Erreur message={erreur} />

      {/* --- Étape 1: saisie --- */}
      {etape === 'saisie' && (
        <Carte className="space-y-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-3">
              <label className="block space-y-1.5">
                <span className="text-sm text-texte-doux">{tr('Matière')}</span>
                <select
                  aria-label={tr('Matière')}
                  value={matiereChoisie}
                  onChange={(e) => choisirMatiere(e.target.value)}
                  className="min-h-11 w-full rounded-xl border border-bordure bg-surface px-3.5 text-texte focus:border-accent focus:outline-none"
                >
                  <option value="">{tr('Toutes les matières')}</option>
                  {matieres.map((m) => (
                    <option key={m.nom} value={m.nom}>
                      {m.nom}
                    </option>
                  ))}
                  <option value={SANS_MATIERE}>{tr('Sans matière')}</option>
                </select>
              </label>

              <label className="block space-y-1.5">
                <span className="text-sm text-texte-doux">{tr('Thème')}</span>
                <select
                  aria-label={tr('Thème')}
                  value={coursChoisi}
                  onChange={(e) => setCoursChoisi(e.target.value)}
                  className="min-h-11 w-full rounded-xl border border-bordure bg-surface px-3.5 text-texte focus:border-accent focus:outline-none"
                >
                  {themes.length === 0 && (
                    <option value="">{tr('Aucun thème')}</option>
                  )}
                  {themes.map((c) => (
                    <option key={c.nom} value={c.nom}>
                      {c.nom}
                    </option>
                  ))}
                </select>
              </label>

              {creation ? (
                <div className="flex gap-2">
                  <Champ
                    value={nomNouveauTheme}
                    onChange={(e) => setNomNouveauTheme(e.target.value)}
                    placeholder={tr('Nom du nouveau thème')}
                    aria-label={tr('Nom du nouveau thème')}
                    autoFocus
                  />
                  <Bouton
                    variante="accent"
                    onClick={creerThemeRapide}
                    disabled={!nomNouveauTheme.trim()}
                  >
                    {tr('Créer')}
                  </Bouton>
                  <Bouton variante="fantome" onClick={() => setCreation(false)}>
                    ✕
                  </Bouton>
                </div>
              ) : (
                <Bouton variante="fantome" onClick={() => setCreation(true)}>
                  {matiereChoisie && matiereChoisie !== SANS_MATIERE
                    ? tr('+ Nouveau thème dans {matiere}', { matiere: matiereChoisie })
                    : tr('+ Nouveau thème')}
                </Bouton>
              )}
            </div>
          </div>

          <div className="space-y-3 border-t border-bordure pt-5">
            <div className="flex gap-2">
              {(
                [
                  ['texte', tr('Texte')],
                  ['pdf', 'PDF'],
                  ['image', tr('Photo')],
                  ['import', tr('Importer')],
                ] as const
              ).map(([v, libelle]) => (
                <Bouton
                  key={v}
                  onClick={() => {
                    setVoie(v)
                    setFichiers([])
                  }}
                  selectionne={voie === v}
                >
                  {libelle}
                </Bouton>
              ))}
            </div>

            {voie === 'import' ? (
              <ImportQuestions cours={coursChoisi} />
            ) : voie === 'texte' ? (
              <textarea
                value={texte}
                onChange={(e) => setTexte(e.target.value)}
                rows={10}
                placeholder={tr('Colle ou écris ici ce sur quoi tu veux être interrogé.')}
                className="w-full rounded-xl border border-bordure bg-surface px-3.5 py-3 text-texte placeholder:text-texte-doux/60 focus:border-accent focus:outline-none"
              />
            ) : (
              <div className="space-y-3">
                <input
                  ref={inputFichier}
                  type="file"
                  accept={voie === 'pdf' ? 'application/pdf' : 'image/*'}
                  // capture: ouvre directement l'appareil photo sur téléphone.
                  {...(voie === 'image' ? { capture: 'environment' } : {})}
                  multiple={voie === 'image'}
                  onChange={(e) =>
                    setFichiers(Array.from(e.target.files ?? []))
                  }
                  className="hidden"
                />
                <Bouton
                  className="w-full"
                  onClick={() => inputFichier.current?.click()}
                >
                  {voie === 'pdf'
                    ? tr('Choisir un PDF')
                    : tr('Prendre une photo ou en choisir')}
                </Bouton>
                {fichiers.length > 0 && (
                  <ul className="space-y-1.5 text-sm text-texte-doux">
                    {fichiers.map((f) => (
                      <li key={f.name} className="truncate">
                        {f.name} · {(f.size / 1024).toFixed(0)} {tr('Ko')}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>

          {voie !== 'import' && (
            <Bouton
              variante="accent"
              className="w-full"
              disabled={!pretAExtraire}
              onClick={extraire}
            >
              {tr('Extraire les concepts')}
            </Bouton>
          )}
          {voie === 'texte' && (
            <Bouton className="w-full" disabled={!pretAExtraire} onClick={estimer}>
              {tr('Estimer le coût (sans rien appeler)')}
            </Bouton>
          )}
          {estimation && (
            <p className="text-xs text-texte-doux" role="status">
              {estimation}
            </p>
          )}
        </Carte>
      )}

      {/* --- Étapes longues --- */}
      {(etape === 'extraction' || etape === 'generation') && (
        <Carte className="space-y-4">
          <p className="font-medium">
            {etape === 'extraction'
              ? tr('Lecture et extraction des concepts…')
              : tr('Génération des questions…')}
          </p>
          <BarreProgression valeur={job?.progression ?? 0} max={100} />
          <p className="text-sm text-texte-doux">
            {tr('Tu peux laisser la page ouverte, le travail continue côté serveur.')}
          </p>
        </Carte>
      )}

      {/* --- Étape 2: validation des concepts --- */}
      {etape === 'validation' && (
        <>
          {avertissements.length > 0 && (
            <Carte className="border-difficile/40 bg-difficile/5">
              <p className="text-sm font-medium text-difficile">
                {tr('Avertissements')}
              </p>
              <ul className="mt-2 space-y-1.5 text-sm text-texte-doux">
                {avertissements.map((a, i) => (
                  <li key={i}>· {a}</li>
                ))}
              </ul>
              {voie === 'image' && (
                <p className="mt-3 text-sm text-texte-doux">
                  {tr(
                    'Si une photo est illisible, reprends-la et relance plutôt que de valider des concepts incertains.',
                  )}
                </p>
              )}
            </Carte>
          )}

          <Carte className="space-y-4">
            <div>
              <p className="font-medium">{tr('Relis les concepts')}</p>
              <p className="mt-1 text-sm text-texte-doux">
                {tr(
                  'Décoche, renomme ou complète. Les questions seront générées uniquement à partir de ce que tu valides ici.',
                )}
              </p>
            </div>

            {concepts.length === 0 ? (
              <p className="text-sm text-texte-doux">
                {tr(
                  "Aucun concept n'a pu être extrait. Reprends avec une source plus lisible.",
                )}
              </p>
            ) : (
              <ul className="space-y-3">
                {concepts.map((c, i) => (
                  <li
                    key={i}
                    className={`rounded-xl border p-3.5 transition-colors ${
                      c.garde
                        ? 'border-bordure bg-surface-haute'
                        : 'border-bordure/50 opacity-50'
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        checked={c.garde}
                        onChange={(e) =>
                          setConcepts((cs) =>
                            cs.map((x, j) =>
                              j === i ? { ...x, garde: e.target.checked } : x,
                            ),
                          )
                        }
                        aria-label={tr('Garder {nom}', { nom: c.titre })}
                        className="mt-2.5 size-5 shrink-0 accent-[var(--color-accent)]"
                      />
                      <div className="flex-1 space-y-2">
                        <Champ
                          value={c.titre}
                          onChange={(e) =>
                            setConcepts((cs) =>
                              cs.map((x, j) =>
                                j === i ? { ...x, titre: e.target.value } : x,
                              ),
                            )
                          }
                        />
                        <textarea
                          value={c.resume}
                          onChange={(e) =>
                            setConcepts((cs) =>
                              cs.map((x, j) =>
                                j === i ? { ...x, resume: e.target.value } : x,
                              ),
                            )
                          }
                          rows={2}
                          placeholder={tr('Résumé fidèle au document (notation du prof, formule exacte…)')}
                          className="w-full rounded-xl border border-bordure bg-surface px-3 py-2 text-sm text-texte placeholder:text-texte-doux/60 focus:border-accent focus:outline-none"
                        />
                        <div className="flex flex-wrap items-center gap-2 text-xs">
                          <select
                            value={c.categorie}
                            onChange={(e) =>
                              setConcepts((cs) =>
                                cs.map((x, j) =>
                                  j === i
                                    ? { ...x, categorie: e.target.value as Categorie }
                                    : x,
                                ),
                              )
                            }
                            className="rounded-lg border border-bordure bg-surface px-2 py-1 text-texte-doux"
                          >
                            {CATEGORIES.map((v) => (
                              <option key={v} value={v}>
                                {v}
                              </option>
                            ))}
                          </select>
                          <select
                            value={c.importance}
                            onChange={(e) =>
                              setConcepts((cs) =>
                                cs.map((x, j) =>
                                  j === i
                                    ? { ...x, importance: e.target.value as Importance }
                                    : x,
                                ),
                              )
                            }
                            className="rounded-lg border border-bordure bg-surface px-2 py-1 text-texte-doux"
                          >
                            {IMPORTANCES.map((v) => (
                              <option key={v} value={v}>
                                {v}
                              </option>
                            ))}
                          </select>
                          <label className="flex items-center gap-1.5 text-texte-doux">
                            <input
                              type="checkbox"
                              checked={c.adapte_calcul}
                              onChange={(e) =>
                                setConcepts((cs) =>
                                  cs.map((x, j) =>
                                    j === i
                                      ? { ...x, adapte_calcul: e.target.checked }
                                      : x,
                                  ),
                                )
                              }
                              className="size-4 accent-[var(--color-accent)]"
                            />
                            {tr('se teste par un exercice')}
                          </label>
                          {c.source && (
                            <span className="text-texte-doux/70">{c.source}</span>
                          )}
                        </div>
                      </div>
                      <button
                        onClick={() =>
                          setConcepts((cs) => cs.filter((_, j) => j !== i))
                        }
                        aria-label={tr('Supprimer {nom}', { nom: c.titre })}
                        className="mt-2 text-texte-doux transition-colors hover:text-rate"
                      >
                        ✕
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}

            <Bouton
              className="w-full"
              onClick={() =>
                setConcepts((cs) => [
                  ...cs,
                  {
                    id: `concept-${cs.length + 1}-${Date.now()}`,
                    titre: '',
                    categorie: 'definition',
                    resume: '',
                    source: null,
                    importance: 'important',
                    adapte_calcul: false,
                    garde: true,
                  },
                ])
              }
            >
              {tr('+ Ajouter un concept')}
            </Bouton>

            <div className="flex gap-2 border-t border-bordure pt-4">
              <Bouton variante="fantome" onClick={recommencer}>
                {tr('Annuler')}
              </Bouton>
              <Bouton
                variante="accent"
                className="flex-1"
                disabled={!concepts.some((c) => c.garde && c.titre.trim())}
                onClick={generer}
              >
                {tr('Valider et générer ({n})', {
                  n: concepts.filter((c) => c.garde).length,
                })}
              </Bouton>
            </div>
          </Carte>
        </>
      )}

      {/* --- Étape 3: rapport de couverture --- */}
      {etape === 'fini' && resultat && (
        <Carte className="space-y-4">
          <div>
            <p className="text-2xl font-semibold">
              {trn(
                Number(resultat.questions_creees),
                '{n} question créée',
                '{n} questions créées',
              )}
            </p>
            <p className="mt-1 text-sm text-texte-doux">
              {coursChoisi}
            </p>
          </div>

          <div className="space-y-2">
            <p className="text-xs uppercase tracking-wide text-texte-doux">
              {tr('Couverture')}
            </p>
            <ul className="space-y-1.5 text-sm">
              {(
                resultat.details as {
                  concept_id: string
                  titre: string
                  nb_questions: number
                }[]
              ).map((d) => (
                <li key={d.concept_id} className="flex items-center gap-3">
                  <Pastille
                    couleur={d.nb_questions > 0 ? '#4ADE9F' : '#F5B84A'}
                  />
                  <span className="flex-1 truncate">{d.titre}</span>
                  <span className="tabular-nums text-texte-doux">
                    {d.nb_questions}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {(resultat.concepts_sans_question as string[]).length > 0 && (
            <p className="rounded-xl border border-difficile/40 bg-difficile/10 px-3.5 py-2.5 text-sm text-difficile">
              {tr('{n} concept(s) sans question. Précise leur description et relance.', {
                n: (resultat.concepts_sans_question as string[]).length,
              })}
            </p>
          )}

          {(resultat.avertissements as string[]).length > 0 && (
            <ul className="space-y-1.5 text-sm text-texte-doux">
              {(resultat.avertissements as string[]).map((a, i) => (
                <li key={i}>· {a}</li>
              ))}
            </ul>
          )}

          <div className="flex gap-2 border-t border-bordure pt-4">
            <Bouton variante="accent" className="flex-1" onClick={recommencer}>
              {tr('Ajouter autre chose')}
            </Bouton>
            <Link to="/bibliotheque">
              <Bouton>{tr('Voir la bibliothèque')}</Bouton>
            </Link>
          </div>
        </Carte>
      )}
    </div>
  )
}
