import { useEffect, useState } from 'react'

import { libellePtsCourt } from '../format'
import { tr, trn } from '../i18n'
import { type Carte as CarteType, type Cours, type Payload, api } from '../api'
import {
  Bouton,
  Carte,
  Champ,
  Chargement,
  Erreur,
  Etiquette,
  Pastille,
  Texte,
  Vide,
} from '../composants/ui'

// Clés de traduction, traduites à l'affichage.
const LIBELLE_TYPE: Record<string, string> = {
  qcm: 'QCM',
  exact: 'Exact',
  cloze: 'Texte à trous',
  probleme: 'Problème',
  libre_llm: 'Réponse libre',
  redaction: 'Rédaction',
}

function apercu(payload: Payload): string {
  if (payload.type === 'cloze') return payload.texte
  if (payload.type === 'redaction') return payload.consigne
  return payload.enonce
}

export default function Bibliotheque() {
  const [cartes, setCartes] = useState<CarteType[] | null>(null)
  const [cours, setCours] = useState<Cours[]>([])
  const [filtreCours, setFiltreCours] = useState('')
  const [filtreType, setFiltreType] = useState('')
  const [recherche, setRecherche] = useState('')
  const [erreur, setErreur] = useState<string | null>(null)
  const [ouverte, setOuverte] = useState<string | null>(null)
  const [edition, setEdition] = useState<string>('')

  useEffect(() => {
    api.cours().then(setCours).catch(() => {})
  }, [])

  const recharger = () =>
    api
      .cartes({
        cours: filtreCours || undefined,
        recherche: recherche || undefined,
      })
      .then(setCartes)
      .catch((e) => setErreur(e.message))

  useEffect(() => {
    const t = setTimeout(recharger, recherche ? 250 : 0)
    return () => clearTimeout(t)
  }, [filtreCours, recherche])

  async function basculerQualite(c: CarteType) {
    try {
      await api.modifierCarte(c.id, {
        desactivee: !c.desactivee,
        note_qualite: c.desactivee ? null : 'à corriger',
      })
      recharger()
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function supprimer(c: CarteType) {
    if (!confirm(tr('Supprimer définitivement cette question ?'))) return
    try {
      await api.supprimerCarte(c.id)
      recharger()
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function enregistrer(c: CarteType) {
    try {
      const payload = JSON.parse(edition) as Payload
      await api.modifierCarte(c.id, { payload })
      setOuverte(null)
      recharger()
    } catch (e) {
      setErreur(
        e instanceof SyntaxError
          ? tr('JSON invalide.')
          : e instanceof Error
            ? e.message
            : tr('Erreur'),
      )
    }
  }

  const couleurs = Object.fromEntries(cours.map((c) => [c.nom, c.couleur]))
  // Le filtre par type se fait ici: la liste est déjà chargée en entier.
  const affichees = cartes?.filter((c) => !filtreType || c.type === filtreType)

  return (
    <div className="animer-entree space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">
        {tr('Bibliothèque')}
      </h1>

      <Erreur message={erreur} />

      <Carte className="space-y-3">
        <Champ
          type="search"
          value={recherche}
          onChange={(e) => setRecherche(e.target.value)}
          placeholder={tr('Rechercher dans les questions…')}
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <select
            value={filtreCours}
            onChange={(e) => setFiltreCours(e.target.value)}
            className="min-h-11 rounded-xl border border-bordure bg-surface px-3.5 text-sm text-texte focus:border-accent focus:outline-none"
          >
            <option value="">{tr('Tous les cours')}</option>
            {cours.map((c) => (
              <option key={c.nom} value={c.nom}>
                {c.nom}
              </option>
            ))}
          </select>
          <select
            value={filtreType}
            onChange={(e) => setFiltreType(e.target.value)}
            aria-label={tr('Type de question')}
            className="min-h-11 rounded-xl border border-bordure bg-surface px-3.5 text-sm text-texte focus:border-accent focus:outline-none"
          >
            <option value="">{tr('Tous les types')}</option>
            {Object.entries(LIBELLE_TYPE).map(([type, libelle]) => (
              <option key={type} value={type}>
                {tr(libelle)}
              </option>
            ))}
          </select>
        </div>
      </Carte>

      {!cartes ? (
        <Chargement />
      ) : !affichees || affichees.length === 0 ? (
        <Vide titre={tr('Aucune question ne correspond.')} />
      ) : (
        <>
          <p className="text-sm text-texte-doux">
            {trn(affichees.length, '{n} question', '{n} questions')}
          </p>
          <ul className="space-y-3">
            {affichees.map((c) => (
              <li key={c.id}>
                <Carte
                  className={`space-y-3 ${c.desactivee ? 'opacity-55' : ''}`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Pastille couleur={couleurs[c.cours] ?? '#8B7CFF'} />
                    <span className="text-sm">{c.cours}</span>
                    <Etiquette>{tr(LIBELLE_TYPE[c.type] ?? c.type)}</Etiquette>
                    <Etiquette>
                      {libellePtsCourt(c.points)}
                      {c.poids !== null ? ` ${tr('(fixé)')}` : ''}
                    </Etiquette>
                    {c.desactivee && (
                      <Etiquette couleur="#FF6B7A">{tr('Désactivée')}</Etiquette>
                    )}
                    {c.nb_revisions > 0 && (
                      <span className="ml-auto text-xs text-texte-doux">
                        {trn(c.nb_revisions, '{n} révision', '{n} révisions')}
                      </span>
                    )}
                  </div>

                  <p className="text-sm">
                    <Texte>{apercu(c.payload)}</Texte>
                  </p>

                  {ouverte === c.id ? (
                    <div className="space-y-2">
                      <textarea
                        value={edition}
                        onChange={(e) => setEdition(e.target.value)}
                        rows={12}
                        spellCheck={false}
                        className="w-full rounded-xl border border-bordure bg-fond px-3 py-2.5 font-mono text-xs text-texte focus:border-accent focus:outline-none"
                      />
                      <div className="flex gap-2">
                        <Bouton
                          variante="accent"
                          onClick={() => enregistrer(c)}
                        >
                          {tr('Enregistrer')}
                        </Bouton>
                        <Bouton
                          variante="fantome"
                          onClick={() => setOuverte(null)}
                        >
                          {tr('Annuler')}
                        </Bouton>
                      </div>
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-2 border-t border-bordure pt-3">
                      <Bouton
                        onClick={() => {
                          setOuverte(c.id)
                          setEdition(JSON.stringify(c.payload, null, 2))
                        }}
                      >
                        {tr('Modifier')}
                      </Bouton>
                      <Bouton
                        variante={c.desactivee ? 'neutre' : 'danger'}
                        onClick={() => basculerQualite(c)}
                      >
                        {c.desactivee
                          ? tr('Réactiver')
                          : tr('Question mauvaise')}
                      </Bouton>
                      <Bouton
                        variante="fantome"
                        className="ml-auto"
                        onClick={() => supprimer(c)}
                      >
                        {tr('Supprimer')}
                      </Bouton>
                    </div>
                  )}
                </Carte>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
