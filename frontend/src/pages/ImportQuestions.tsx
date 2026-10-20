import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  type ApercuImport,
  type ApercuQuestionImport,
  type ResultatImport,
  api,
} from '../api'
import { Bouton, Carte, Erreur, Etiquette, Pastille } from '../composants/ui'
import { tr, trn } from '../i18n'

// Clés de traduction, traduites à l'affichage.
const LIBELLE_STATUT: Record<ApercuQuestionImport['statut'], string> = {
  nouvelle: 'Nouvelle',
  doublon_fichier: 'Doublon dans le fichier',
  deja_en_base: 'Déjà en base',
}

const COULEUR_STATUT: Record<ApercuQuestionImport['statut'], string> = {
  nouvelle: '#4ADE9F',
  doublon_fichier: '#F5B84A',
  deja_en_base: '#8A91A5',
}

const APERCU_MAX = 50

/**
 * Quatrième mode de « Ajouter de la matière »: importer des questions déjà
 * générées (fichier JSON). Aucun LLM: le serveur valide, on relit le rapport,
 * puis on confirme.
 */
export default function ImportQuestions({ cours }: { cours: string }) {
  const [fichier, setFichier] = useState<File | null>(null)
  const [concepts, setConcepts] = useState<File | null>(null)
  // L'aperçu est lié au thème pour lequel il a été calculé.
  const cle = cours
  const [apercuCalcule, setApercu] = useState<{
    donnees: ApercuImport
    cle: string
  } | null>(null)
  const apercu = apercuCalcule?.cle === cle ? apercuCalcule.donnees : null
  const [resultat, setResultat] = useState<ResultatImport | null>(null)
  const [occupe, setOccupe] = useState(false)
  const [erreur, setErreur] = useState<string | null>(null)

  const inputFichier = useRef<HTMLInputElement>(null)
  const inputConcepts = useRef<HTMLInputElement>(null)

  async function verifier() {
    if (!fichier) return
    setErreur(null)
    setOccupe(true)
    try {
      const donnees = await api.importApercu(cours, fichier, concepts)
      setApercu({ donnees, cle })
    } catch (e) {
      setApercu(null)
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    } finally {
      setOccupe(false)
    }
  }

  async function importer() {
    if (!fichier) return
    setErreur(null)
    setOccupe(true)
    try {
      setResultat(
        await api.importConfirmer(cours, fichier, concepts),
      )
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    } finally {
      setOccupe(false)
    }
  }

  function recommencer() {
    setFichier(null)
    setConcepts(null)
    setApercu(null)
    setResultat(null)
    setErreur(null)
  }

  if (resultat) {
    return (
      <div className="space-y-4">
        <div>
          <p className="text-2xl font-semibold">
            {trn(resultat.importees, '{n} question importée', '{n} questions importées')}
          </p>
          <p className="mt-1 text-sm text-texte-doux">
            {resultat.cours.join(', ')}
          </p>
        </div>
        {(resultat.deja_presentes > 0 || resultat.doublons_contenu > 0) && (
          <p className="text-sm text-texte-doux">
            {tr('Ignorées: {deja} déjà présente(s), {doublons} doublon(s) de contenu.', {
              deja: resultat.deja_presentes,
              doublons: resultat.doublons_contenu,
            })}
          </p>
        )}
        <div className="flex gap-2 border-t border-bordure pt-4">
          <Bouton variante="accent" className="flex-1" onClick={recommencer}>
            {tr('Importer un autre fichier')}
          </Bouton>
          <Link to="/bibliotheque">
            <Bouton>{tr('Voir la bibliothèque')}</Bouton>
          </Link>
        </div>
      </div>
    )
  }

  const rapport = apercu?.rapport

  return (
    <div className="space-y-4">
      <p className="text-sm text-texte-doux">
        {tr('Importe un fichier de questions déjà généré (par exemple')}{' '}
        <code>sorties/…_questions.json</code>
        {tr("). Aucune IA n'est appelée: le fichier est vérifié, tu relis le rapport, puis tu confirmes.")}
      </p>

      <Erreur message={erreur} />

      <div className="space-y-3">
        <input
          ref={inputFichier}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => {
            setFichier(e.target.files?.[0] ?? null)
            setApercu(null)
          }}
        />
        <Bouton className="w-full" onClick={() => inputFichier.current?.click()}>
          {fichier
            ? tr('Questions: {nom}', { nom: fichier.name })
            : tr('Choisir le fichier de questions')}
        </Bouton>

        <input
          ref={inputConcepts}
          type="file"
          accept=".json,application/json"
          className="hidden"
          onChange={(e) => {
            setConcepts(e.target.files?.[0] ?? null)
            setApercu(null)
          }}
        />
        <Bouton
          variante="fantome"
          className="w-full"
          onClick={() => inputConcepts.current?.click()}
        >
          {concepts
            ? tr('Concepts: {nom}', { nom: concepts.name })
            : tr('Ajouter la liste de concepts (facultatif, pour la couverture)')}
        </Bouton>
      </div>

      <Bouton
        variante="accent"
        className="w-full"
        disabled={!fichier || !cours || occupe}
        onClick={verifier}
      >
        {occupe && !apercu ? tr('Vérification…') : tr('Vérifier le fichier')}
      </Bouton>

      {rapport && apercu && (
        <Carte className="space-y-4 bg-fond">
          <div className="flex flex-wrap items-center gap-2">
            <Etiquette couleur={rapport.valide ? '#4ADE9F' : '#FF6B7A'}>
              {rapport.valide ? tr('Fichier valide') : tr('Fichier invalide')}
            </Etiquette>
            <span className="text-sm text-texte-doux">
              {trn(rapport.nb_elements, '{n} question', '{n} questions')} ·{' '}
              {tr('rattaché à {cours}', { cours })}
            </span>
          </div>

          {(rapport.erreurs_fichier.length > 0 || rapport.erreurs.length > 0) && (
            <div className="space-y-2 rounded-xl border border-rate/40 bg-rate/10 px-3.5 py-3">
              <p className="text-sm font-medium text-rate">
                {tr('Rien ne sera importé tant que ces erreurs ne sont pas corrigées')}
              </p>
              <ul className="space-y-1.5 text-sm text-texte">
                {rapport.erreurs_fichier.map((m, i) => (
                  <li key={`f${i}`}>· {tr('Fichier')}: {m}</li>
                ))}
                {rapport.erreurs.flatMap((e) =>
                  e.messages.map((m, i) => (
                    <li key={`${e.position}-${i}`}>
                      · {tr('Question n°{n}', { n: e.position })} ({e.reference}): {m}
                    </li>
                  )),
                )}
              </ul>
            </div>
          )}

          {rapport.avertissements.length > 0 && (
            <div className="space-y-2 rounded-xl border border-difficile/40 bg-difficile/5 px-3.5 py-3">
              <p className="text-sm font-medium text-difficile">
                {tr('Avertissements')} ({rapport.avertissements.length})
              </p>
              <ul className="space-y-1 text-sm text-texte-doux">
                {rapport.avertissements.map((a, i) => (
                  <li key={i}>· {a}</li>
                ))}
              </ul>
            </div>
          )}

          {rapport.valide && (
            <>
              <div className="flex flex-wrap gap-2">
                <Etiquette couleur="#4ADE9F">
                  {tr('{n} à importer', { n: apercu.nouvelles })}
                </Etiquette>
                {apercu.deja_en_base > 0 && (
                  <Etiquette couleur="#8A91A5">
                    {tr('{n} déjà en base', { n: apercu.deja_en_base })}
                  </Etiquette>
                )}
                {apercu.doublons_fichier > 0 && (
                  <Etiquette couleur="#F5B84A">
                    {trn(
                      apercu.doublons_fichier,
                      '{n} doublon dans le fichier',
                      '{n} doublons dans le fichier',
                    )}
                  </Etiquette>
                )}
                {Object.entries(rapport.par_type).map(([type, n]) => (
                  <Etiquette key={type}>
                    {type} {n}
                  </Etiquette>
                ))}
              </div>

              {rapport.confiance_basse.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs uppercase tracking-wide text-difficile">
                    {tr('Confiance basse — à vérifier en priorité')}
                  </p>
                  <ul className="space-y-1 text-sm text-texte-doux">
                    {rapport.confiance_basse.map((q) => (
                      <li key={q.position}>
                        {tr('n°{n}', { n: q.position })} ({q.type}): {q.resume}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {rapport.couverture.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs uppercase tracking-wide text-texte-doux">
                    {tr('Couverture')} ({rapport.concepts_fichier})
                  </p>
                  <ul className="space-y-1.5 text-sm">
                    {rapport.couverture.map((c) => (
                      <li key={c.concept_id} className="flex items-center gap-3">
                        <Pastille
                          couleur={c.nb_questions > 0 ? '#4ADE9F' : '#F5B84A'}
                        />
                        <span className="flex-1 truncate">{c.titre}</span>
                        <span className="tabular-nums text-texte-doux">
                          {c.nb_questions}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="space-y-1.5">
                <p className="text-xs uppercase tracking-wide text-texte-doux">
                  {tr('Aperçu')}
                </p>
                <ul className="space-y-2 text-sm">
                  {rapport.apercu.slice(0, APERCU_MAX).map((q) => (
                    <li key={q.position} className="flex items-start gap-3">
                      <span className="mt-1.5">
                        <Pastille couleur={COULEUR_STATUT[q.statut]} />
                      </span>
                      <span className="flex-1">
                        <span className="text-texte-doux">{q.type} · </span>
                        {q.resume}
                        {q.statut !== 'nouvelle' && (
                          <span className="ml-2 text-xs text-texte-doux">
                            ({tr(LIBELLE_STATUT[q.statut])})
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
                {rapport.apercu.length > APERCU_MAX && (
                  <p className="text-sm text-texte-doux">
                    {trn(
                      rapport.apercu.length - APERCU_MAX,
                      '… et {n} autre.',
                      '… et {n} autres.',
                    )}
                  </p>
                )}
              </div>
            </>
          )}

          <Bouton
            variante="accent"
            className="w-full"
            disabled={!apercu.importable || apercu.nouvelles === 0 || occupe}
            onClick={importer}
          >
            {apercu.nouvelles === 0 && apercu.importable
              ? tr('Rien de nouveau à importer')
              : trn(
                  apercu.nouvelles,
                  'Importer {n} question',
                  'Importer {n} questions',
                )}
          </Bouton>
        </Carte>
      )}
    </div>
  )
}
