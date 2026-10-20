import { useEffect, useState } from 'react'

import { type Cours, type Matiere, api } from '../api'
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
import { enGras } from '../composants/enGras'
import { questionsEtPoints } from '../format'
import { tr, trn } from '../i18n'

const PALETTE = [
  '#8B7CFF',
  '#5CC8FF',
  '#4ADE9F',
  '#F5B84A',
  '#FF6B7A',
  '#C084FC',
  '#38BDF8',
  '#FB923C',
]

const SELECT =
  'min-h-11 w-full rounded-xl border border-bordure bg-surface px-3.5 text-texte focus:border-accent focus:outline-none'

function Palette({
  valeur,
  onChoix,
  nom,
}: {
  valeur: string
  onChoix: (c: string) => void
  nom: string
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {PALETTE.map((c) => (
        <button
          key={c}
          type="button"
          onClick={() => onChoix(c)}
          aria-label={tr('Couleur {c} pour {nom}', { c, nom })}
          aria-pressed={valeur === c}
          className={`size-7 rounded-lg transition-transform ${
            valeur === c
              ? 'ring-2 ring-texte ring-offset-2 ring-offset-surface'
              : 'hover:scale-110'
          }`}
          style={{ backgroundColor: c }}
        />
      ))}
    </div>
  )
}

/** Texte libre au format des fichiers profiles/*.md (Cours:, Niveau:, …). */
function EditeurProfil({
  valeur,
  aide,
  onEnregistrer,
}: {
  valeur: string
  aide: string
  onEnregistrer: (texte: string) => void
}) {
  const [brouillon, setBrouillon] = useState(valeur)
  return (
    <div className="space-y-2">
      <textarea
        value={brouillon}
        onChange={(e) => setBrouillon(e.target.value)}
        rows={8}
        // Rubriques du fichier profiles/*.md: ce sont des clés de format, pas du texte d'interface.
        placeholder={
          'Cours:\nNiveau:\nLangue:\nNotation et méthode du prof:\nTypes de questions préférés:\nÀ éviter:\nParticularités:\nExemples de bonnes questions:'
        }
        className="w-full rounded-xl border border-bordure bg-surface px-3.5 py-3 font-mono text-sm text-texte placeholder:text-texte-doux/50 focus:border-accent focus:outline-none"
      />
      <p className="text-xs text-texte-doux">{aide}</p>
      <Bouton
        variante="accent"
        disabled={brouillon === valeur}
        onClick={() => onEnregistrer(brouillon)}
      >
        {tr('Enregistrer le profil')}
      </Bouton>
    </div>
  )
}

/** Récap théorique du thème: Markdown simple, lu avant ses questions en mode « par cours ». */
function EditeurRecap({
  valeur,
  onEnregistrer,
}: {
  valeur: string
  onEnregistrer: (texte: string) => void
}) {
  const [brouillon, setBrouillon] = useState(valeur)
  return (
    <div className="space-y-2">
      <textarea
        value={brouillon}
        onChange={(e) => setBrouillon(e.target.value)}
        rows={10}
        className="w-full rounded-xl border border-bordure bg-surface px-3.5 py-3 font-mono text-sm text-texte placeholder:text-texte-doux/50 focus:border-accent focus:outline-none"
      />
      <p className="text-xs text-texte-doux">
        {tr('Markdown simple: ## titre, - liste, > piège, **gras**, $formule$. Vide = pas de récap.')}
      </p>
      <Bouton
        variante="accent"
        disabled={brouillon === valeur}
        onClick={() => onEnregistrer(brouillon)}
      >
        {tr('Enregistrer le récap')}
      </Bouton>
    </div>
  )
}

function Mode({ c }: { c: Cours }) {
  if (c.archive) return <Etiquette>{tr('Archivé')}</Etiquette>
  return c.en_entretien ? (
    <Etiquette couleur="#B279A2">{tr('Entretien')}</Etiquette>
  ) : (
    <Etiquette couleur="#5CC8FF">{tr('Test à venir')}</Etiquette>
  )
}

export default function PageCours() {
  const [matieres, setMatieres] = useState<Matiere[] | null>(null)
  const [cours, setCours] = useState<Cours[]>([])
  const [erreur, setErreur] = useState<string | null>(null)

  const [nomMatiere, setNomMatiere] = useState('')
  const [couleurMatiere, setCouleurMatiere] = useState(PALETTE[1])
  const [nomTheme, setNomTheme] = useState('')
  const [matiereDuTheme, setMatiereDuTheme] = useState('')

  const [renomme, setRenomme] = useState<{
    type: 'theme' | 'matiere'
    nom: string
    valeur: string
  } | null>(null)
  const [panneau, setPanneau] = useState<string | null>(null)
  const [voirArchives, setVoirArchives] = useState(false)
  const [aSupprimer, setASupprimer] = useState<Cours | null>(null)
  const [reaffecter, setReaffecter] = useState('')
  const [matiereASupprimer, setMatiereASupprimer] = useState<Matiere | null>(null)

  const recharger = () =>
    Promise.all([api.matieres(), api.cours()])
      .then(([m, c]) => {
        setMatieres(m)
        setCours(c)
      })
      .catch((e) => setErreur(e.message))

  useEffect(() => {
    recharger()
  }, [])

  async function agir(action: () => Promise<unknown>) {
    setErreur(null)
    try {
      await action()
      await recharger()
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  async function creerMatiere(e: React.FormEvent) {
    e.preventDefault()
    await agir(async () => {
      await api.creerMatiere(nomMatiere.trim(), couleurMatiere)
      setNomMatiere('')
      setCouleurMatiere(PALETTE[((matieres?.length ?? 0) + 2) % PALETTE.length])
    })
  }

  async function creerTheme(e: React.FormEvent) {
    e.preventDefault()
    await agir(async () => {
      await api.creerCours(nomTheme.trim(), undefined, matiereDuTheme || null)
      setNomTheme('')
    })
  }

  async function validerRenommage() {
    if (!renomme) return
    await agir(async () => {
      if (renomme.type === 'theme')
        await api.modifierCours(renomme.nom, { nouveau_nom: renomme.valeur.trim() })
      else
        await api.modifierMatiere(renomme.nom, { nouveau_nom: renomme.valeur.trim() })
      setRenomme(null)
    })
  }

  if (!matieres) return <Chargement />

  const archives = cours.filter((c) => c.archive)
  const visibles = (liste: Cours[]) =>
    liste.filter((c) => voirArchives || !c.archive)
  const sansMatiere = visibles(cours.filter((c) => c.matiere === null))

  // Fonction de rendu (et non composant): un composant défini ici changerait
  // d'identité à chaque frappe et ferait perdre le focus aux champs.
  const ligne = (c: Cours) => {
    const ouvert = panneau === `t:${c.nom}`
    return (
      <li key={c.nom} className={`space-y-3 py-3 ${c.archive ? 'opacity-60' : ''}`}>
        {renomme?.type === 'theme' && renomme.nom === c.nom ? (
          <div className="flex gap-2">
            <Champ
              value={renomme.valeur}
              onChange={(e) =>
                setRenomme({ ...renomme, valeur: e.target.value })
              }
              autoFocus
              aria-label={tr('Nouveau nom de {nom}', { nom: c.nom })}
            />
            <Bouton variante="accent" onClick={validerRenommage}>
              OK
            </Bouton>
            <Bouton variante="fantome" onClick={() => setRenomme(null)}>
              ✕
            </Bouton>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <Pastille couleur={c.couleur} />
            <span className="font-medium">{c.nom}</span>
            <Mode c={c} />
            <span className="ml-auto text-sm text-texte-doux">
              {questionsEtPoints(c.nb_cartes, c.points)}
            </span>
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Bouton
            variante="fantome"
            onClick={() => setPanneau(ouvert ? null : `t:${c.nom}`)}
            aria-expanded={ouvert}
          >
            {tr('Réglages')}
          </Bouton>
          <Bouton
            variante="fantome"
            onClick={() => setRenomme({ type: 'theme', nom: c.nom, valeur: c.nom })}
          >
            {tr('Renommer')}
          </Bouton>
          <Bouton
            variante="fantome"
            onClick={() => agir(() => api.modifierCours(c.nom, { archive: !c.archive }))}
          >
            {c.archive ? tr('Désarchiver') : tr('Archiver')}
          </Bouton>
          <Bouton variante="danger" onClick={() => setASupprimer(c)}>
            {tr('Supprimer')}
          </Bouton>
        </div>

        {ouvert && (
          <div className="space-y-4 rounded-xl border border-bordure p-4">
            <label className="block space-y-1.5">
              <span className="text-sm text-texte-doux">{tr('Matière')}</span>
              <select
                value={c.matiere ?? ''}
                onChange={(e) =>
                  agir(() =>
                    api.modifierCours(c.nom, { matiere: e.target.value || null }),
                  )
                }
                className={SELECT}
              >
                <option value="">{tr('Sans matière')}</option>
                {matieres!.map((m) => (
                  <option key={m.nom} value={m.nom}>
                    {m.nom}
                  </option>
                ))}
              </select>
            </label>
            <div className="space-y-2">
              <span className="text-sm text-texte-doux">{tr('Couleur')}</span>
              <Palette
                valeur={c.couleur}
                nom={c.nom}
                onChoix={(couleur) => agir(() => api.modifierCours(c.nom, { couleur }))}
              />
            </div>
            <div className="space-y-2">
              <span className="text-sm text-texte-doux">
                {tr('Profil du thème (surcharge celui de la matière)')}
              </span>
              <EditeurProfil
                valeur={c.profil}
                aide={
                  c.matiere
                    ? tr(
                        'Fusionné avec le profil de « {matiere} » rubrique par rubrique: ce que tu écris ici prime. Une rubrique laissée vide reprend celle de la matière.',
                        { matiere: c.matiere },
                      )
                    : tr('Ce thème n’a pas de matière: ce profil est utilisé tel quel.')
                }
                onEnregistrer={(profil) => agir(() => api.modifierCours(c.nom, { profil }))}
              />
            </div>
            <div className="space-y-2">
              <span className="text-sm text-texte-doux">
                {tr('Récap théorique (lu avant les questions du thème, en mode « par cours »)')}
              </span>
              <EditeurRecap
                valeur={c.recap}
                onEnregistrer={(recap) => agir(() => api.modifierCours(c.nom, { recap }))}
              />
            </div>
          </div>
        )}
      </li>
    )
  }

  return (
    <div className="animer-entree space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">
        {tr('Matières et thèmes')}
      </h1>

      <Erreur message={erreur} />

      <p className="text-sm text-texte-doux">
        {enGras(
          tr(
            'Une **matière** (Anglais, Maths…) regroupe des **thèmes** précis (Anglais conditionnel, English present perfect…). Le profil se règle sur la matière; chaque thème en hérite et peut le surcharger. Un thème sans test à venir passe en **entretien**.',
          ),
        )}
      </p>

      <div className="grid gap-4 lg:grid-cols-2">
        <Carte>
          <form onSubmit={creerMatiere} className="space-y-4">
            <label className="block space-y-1.5">
              <span className="text-sm text-texte-doux">
                {tr('Nouvelle matière')}
              </span>
              <Champ
                value={nomMatiere}
                onChange={(e) => setNomMatiere(e.target.value)}
                placeholder={tr('Ex. Anglais')}
                required
              />
            </label>
            <Palette valeur={couleurMatiere} onChoix={setCouleurMatiere} nom={tr('la matière')} />
            <Bouton
              variante="accent"
              type="submit"
              className="w-full"
              disabled={!nomMatiere.trim()}
            >
              {tr('Créer la matière')}
            </Bouton>
          </form>
        </Carte>

        <Carte>
          <form onSubmit={creerTheme} className="space-y-4">
            <label className="block space-y-1.5">
              <span className="text-sm text-texte-doux">{tr('Nouveau thème')}</span>
              <Champ
                value={nomTheme}
                onChange={(e) => setNomTheme(e.target.value)}
                placeholder={tr('Ex. Anglais conditionnel')}
                required
              />
            </label>
            <label className="block space-y-1.5">
              <span className="text-sm text-texte-doux">{tr('Matière')}</span>
              <select
                value={matiereDuTheme}
                onChange={(e) => setMatiereDuTheme(e.target.value)}
                className={SELECT}
                aria-label={tr('Matière du nouveau thème')}
              >
                <option value="">{tr('Sans matière')}</option>
                {matieres.map((m) => (
                  <option key={m.nom} value={m.nom}>
                    {m.nom}
                  </option>
                ))}
              </select>
            </label>
            <Bouton
              variante="accent"
              type="submit"
              className="w-full"
              disabled={!nomTheme.trim()}
            >
              {tr('Créer le thème')}
            </Bouton>
          </form>
        </Carte>
      </div>

      {matieres.length === 0 && cours.length === 0 && (
        <Vide titre={tr("Aucune matière ni thème pour l'instant.")} />
      )}

      {matieres.map((m) => {
        const themes = visibles(cours.filter((c) => c.matiere === m.nom))
        const ouvert = panneau === `m:${m.nom}`
        const actifs = cours.filter((c) => c.matiere === m.nom && !c.archive)
        const enEntretien = actifs.filter((c) => c.en_entretien).length
        return (
          <Carte key={m.nom} className="space-y-3">
            {renomme?.type === 'matiere' && renomme.nom === m.nom ? (
              <div className="flex gap-2">
                <Champ
                  value={renomme.valeur}
                  onChange={(e) => setRenomme({ ...renomme, valeur: e.target.value })}
                  autoFocus
                  aria-label={tr('Nouveau nom de {nom}', { nom: m.nom })}
                />
                <Bouton variante="accent" onClick={validerRenommage}>
                  OK
                </Bouton>
                <Bouton variante="fantome" onClick={() => setRenomme(null)}>
                  ✕
                </Bouton>
              </div>
            ) : (
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                <Pastille couleur={m.couleur} />
                <h2 className="text-lg font-semibold">{m.nom}</h2>
                {actifs.length > 0 && (
                  <Etiquette couleur={enEntretien > 0 ? '#B279A2' : undefined}>
                    {tr('{a}/{b} en entretien', { a: enEntretien, b: actifs.length })}
                  </Etiquette>
                )}
                <span className="ml-auto text-sm text-texte-doux">
                  {trn(m.nb_cours, '{n} thème', '{n} thèmes')} ·{' '}
                  {trn(m.nb_cartes, '{n} question', '{n} questions')}
                </span>
              </div>
            )}

            <div className="flex flex-wrap gap-2">
              <Bouton
                variante="fantome"
                onClick={() => setPanneau(ouvert ? null : `m:${m.nom}`)}
                aria-expanded={ouvert}
              >
                {tr('Profil et couleur')}
              </Bouton>
              <Bouton
                variante="fantome"
                onClick={() => setRenomme({ type: 'matiere', nom: m.nom, valeur: m.nom })}
              >
                {tr('Renommer')}
              </Bouton>
              <Bouton variante="danger" onClick={() => setMatiereASupprimer(m)}>
                {tr('Supprimer')}
              </Bouton>
            </div>

            {ouvert && (
              <div className="space-y-4 rounded-xl border border-bordure p-4">
                <div className="space-y-2">
                  <span className="text-sm text-texte-doux">{tr('Couleur')}</span>
                  <Palette
                    valeur={m.couleur}
                    nom={m.nom}
                    onChoix={(couleur) => agir(() => api.modifierMatiere(m.nom, { couleur }))}
                  />
                </div>
                <div className="space-y-2">
                  <span className="text-sm text-texte-doux">
                    {tr('Profil de la matière (hérité par tous ses thèmes)')}
                  </span>
                  <EditeurProfil
                    valeur={m.profil}
                    aide={tr(
                      'Notation et méthode du prof, types préférés, exemples… Les thèmes peuvent surcharger chaque rubrique.',
                    )}
                    onEnregistrer={(profil) => agir(() => api.modifierMatiere(m.nom, { profil }))}
                  />
                </div>
              </div>
            )}

            {themes.length === 0 ? (
              <p className="border-t border-bordure pt-3 text-sm text-texte-doux">
                {tr('Aucun thème dans cette matière.')}
              </p>
            ) : (
              <ul className="divide-y divide-bordure border-t border-bordure">
                {themes.map((c) => ligne(c))}
              </ul>
            )}
          </Carte>
        )
      })}

      {sansMatiere.length > 0 && (
        <Carte className="space-y-3">
          <h2 className="text-lg font-semibold">{tr('Sans matière')}</h2>
          <ul className="divide-y divide-bordure border-t border-bordure">
            {sansMatiere.map((c) => ligne(c))}
          </ul>
        </Carte>
      )}

      {archives.length > 0 && (
        <Bouton variante="fantome" onClick={() => setVoirArchives((v) => !v)}>
          {voirArchives
            ? tr('Masquer les thèmes archivés ({n})', { n: archives.length })
            : tr('Afficher les thèmes archivés ({n})', { n: archives.length })}
        </Bouton>
      )}

      {aSupprimer && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-fond/80 p-4 backdrop-blur sm:items-center">
          <Carte className="animer-entree w-full max-w-md space-y-4">
            <div>
              <p className="font-medium">
                {tr('Supprimer « {nom} » ?', { nom: aSupprimer.nom })}
              </p>
              <p className="mt-1.5 text-sm text-texte-doux">
                {aSupprimer.nb_cartes > 0
                  ? tr(
                      'Ce thème contient {n} question(s). Sans réaffectation, elles seront supprimées définitivement. Pour les garder sans les réviser, archive le thème plutôt.',
                      { n: aSupprimer.nb_cartes },
                    )
                  : tr('Ce thème ne contient aucune question.')}
              </p>
            </div>

            {aSupprimer.nb_cartes > 0 && (
              <label className="block space-y-1.5">
                <span className="text-sm text-texte-doux">
                  {tr('Réaffecter les questions vers')}
                </span>
                <select
                  value={reaffecter}
                  onChange={(e) => setReaffecter(e.target.value)}
                  className={SELECT}
                >
                  <option value="">
                    {tr('Ne pas réaffecter (supprimer les questions)')}
                  </option>
                  {cours
                    .filter((c) => c.nom !== aSupprimer.nom)
                    .map((c) => (
                      <option key={c.nom} value={c.nom}>
                        {c.nom}
                      </option>
                    ))}
                </select>
              </label>
            )}

            <div className="flex gap-2">
              <Bouton
                variante="fantome"
                className="flex-1"
                onClick={() => {
                  setASupprimer(null)
                  setReaffecter('')
                }}
              >
                {tr('Annuler')}
              </Bouton>
              <Bouton
                variante="danger"
                className="flex-1"
                onClick={() =>
                  agir(async () => {
                    await api.supprimerCours(aSupprimer.nom, reaffecter || undefined)
                    setASupprimer(null)
                    setReaffecter('')
                  })
                }
              >
                {tr('Supprimer')}
              </Bouton>
            </div>
          </Carte>
        </div>
      )}

      {matiereASupprimer && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-fond/80 p-4 backdrop-blur sm:items-center">
          <Carte className="animer-entree w-full max-w-md space-y-4">
            <div>
              <p className="font-medium">
                {tr('Supprimer la matière « {nom} » ?', { nom: matiereASupprimer.nom })}
              </p>
              <p className="mt-1.5 text-sm text-texte-doux">
                {tr(
                  'Ses {n} thème(s) et leurs questions sont conservés, sans matière. Les tests liés à toute la matière perdent ce lien.',
                  { n: matiereASupprimer.nb_cours },
                )}
              </p>
            </div>
            <div className="flex gap-2">
              <Bouton
                variante="fantome"
                className="flex-1"
                onClick={() => setMatiereASupprimer(null)}
              >
                {tr('Annuler')}
              </Bouton>
              <Bouton
                variante="danger"
                className="flex-1"
                onClick={() =>
                  agir(async () => {
                    await api.supprimerMatiere(matiereASupprimer.nom)
                    setMatiereASupprimer(null)
                  })
                }
              >
                {tr('Supprimer la matière')}
              </Bouton>
            </div>
          </Carte>
        </div>
      )}
    </div>
  )
}
