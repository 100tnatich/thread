import { useEffect, useRef, useState } from 'react'

import { type Cours, type Depot as TypeDepot, api } from '../api'
import { Bouton, Carte, Champ, Chargement, Erreur, Etiquette, Pastille, Vide } from '../composants/ui'
import { localeAffichage, tr, trn } from '../i18n'

const TAILLE_MAX = 20 * 1024 * 1024
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.gif,.txt,.md,.docx,image/*,application/pdf'
const SELECT =
  'min-h-11 w-full rounded-xl border border-bordure bg-surface px-3.5 text-texte focus:border-accent focus:outline-none'

function taille(octets: number): string {
  if (octets < 1024 * 1024) return `${Math.max(1, Math.round(octets / 1024))} Ko`
  return `${(octets / 1024 / 1024).toFixed(1)} Mo`
}

type Envoi = { nom: string; etat: 'attente' | 'ok' | 'erreur'; message?: string }

/** Dépôt de fichiers par cours (comptes étendus). Rien n'est envoyé à une IA:
 *  les fichiers attendent, et se traitent plus tard avec `/generer-questions depot`. */
export default function Depot() {
  const [cours, setCours] = useState<Cours[] | null>(null)
  const [depots, setDepots] = useState<TypeDepot[]>([])
  const [erreur, setErreur] = useState<string | null>(null)

  const [nouveauCours, setNouveauCours] = useState('')
  const [choisi, setChoisi] = useState('')
  const [fichiers, setFichiers] = useState<File[]>([])
  const [envois, setEnvois] = useState<Envoi[]>([])
  const [enCours, setEnCours] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  async function charger() {
    try {
      const [c, d] = await Promise.all([api.cours(), api.depots()])
      setCours(c)
      setDepots(d)
      setChoisi((actuel) => (c.some((x) => x.nom === actuel) ? actuel : (c[0]?.nom ?? '')))
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  useEffect(() => {
    charger()
  }, [])

  async function creer(e: React.FormEvent) {
    e.preventDefault()
    setErreur(null)
    try {
      const c = await api.creerCours(nouveauCours.trim())
      setNouveauCours('')
      await charger()
      setChoisi(c.nom)
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  async function deposer() {
    if (!choisi || fichiers.length === 0) return
    setErreur(null)
    setEnCours(true)
    const suivi: Envoi[] = fichiers.map((f) => ({ nom: f.name, etat: 'attente' }))
    setEnvois([...suivi])
    // Un fichier par requête: la limite de taille d'une requête n'est jamais atteinte
    // et un fichier refusé n'empêche pas les autres.
    for (const [i, f] of fichiers.entries()) {
      try {
        if (f.size > TAILLE_MAX) throw new Error(tr('Fichier trop lourd (20 Mo maximum).'))
        await api.deposer(choisi, f)
        suivi[i] = { nom: f.name, etat: 'ok' }
      } catch (err) {
        suivi[i] = {
          nom: f.name,
          etat: 'erreur',
          message: err instanceof Error ? err.message : tr('Erreur'),
        }
      }
      setEnvois([...suivi])
    }
    setFichiers([])
    if (input.current) input.current.value = ''
    setEnCours(false)
    await charger()
  }

  async function supprimer(d: TypeDepot) {
    if (!confirm(tr('Supprimer « {nom} » ?', { nom: d.nom }))) return
    setErreur(null)
    try {
      await api.supprimerDepot(d.id)
      await charger()
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  if (!cours) return erreur ? <Erreur message={erreur} /> : <Chargement />

  const couleurs = Object.fromEntries(cours.map((c) => [c.nom, c.couleur]))
  const parCours = [...new Set(depots.map((d) => d.cours))].sort()
  const nbAttente = depots.filter((d) => !d.traite_le).length

  return (
    <div className="animer-entree space-y-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">{tr('Dépôt')}</h1>
        <p className="mt-2 max-w-prose text-sm text-texte-doux">
          {tr(
            "Dépose tes fichiers de cours maintenant, génère les questions plus tard. Rien n'est envoyé à une IA ici : les fichiers attendent que tu les traites depuis Claude Code (/generer-questions depot).",
          )}
        </p>
      </header>

      <Erreur message={erreur} />

      <Carte>
        <form onSubmit={creer} className="space-y-3">
          <p className="font-medium">{tr('Créer un cours')}</p>
          <div className="flex gap-2">
            <Champ
              value={nouveauCours}
              onChange={(e) => setNouveauCours(e.target.value)}
              placeholder={tr('Ex. Anglais conditionnel')}
              aria-label={tr('Nom du cours')}
              maxLength={100}
            />
            <Bouton variante="accent" type="submit" disabled={!nouveauCours.trim()}>
              {tr('Créer')}
            </Bouton>
          </div>
        </form>
      </Carte>

      <Carte className="space-y-4">
        <p className="font-medium">{tr('Déposer des fichiers')}</p>
        {cours.length === 0 ? (
          <p className="text-sm text-texte-doux">{tr("Crée d'abord un cours ci-dessus.")}</p>
        ) : (
          <>
            <label className="block space-y-1.5">
              <span className="text-sm text-texte-doux">{tr('Cours')}</span>
              <select
                value={choisi}
                onChange={(e) => setChoisi(e.target.value)}
                className={SELECT}
                aria-label={tr('Cours du dépôt')}
              >
                {cours.map((c) => (
                  <option key={c.nom} value={c.nom}>
                    {c.nom}
                  </option>
                ))}
              </select>
            </label>

            <input
              ref={input}
              type="file"
              multiple
              accept={ACCEPT}
              onChange={(e) => setFichiers(Array.from(e.target.files ?? []))}
              className="hidden"
              aria-label={tr('Fichiers à déposer')}
            />
            <Bouton className="w-full" onClick={() => input.current?.click()} disabled={enCours}>
              {fichiers.length > 0
                ? trn(fichiers.length, '{n} fichier choisi', '{n} fichiers choisis')
                : tr('Choisir des fichiers (PDF, photos, texte, Word)')}
            </Bouton>
            {fichiers.length > 0 && (
              <ul className="space-y-1 text-sm text-texte-doux">
                {fichiers.map((f) => (
                  <li key={f.name + f.size} className="truncate">
                    {f.name} · {taille(f.size)}
                  </li>
                ))}
              </ul>
            )}
            <Bouton
              variante="accent"
              className="w-full"
              disabled={enCours || !choisi || fichiers.length === 0}
              onClick={deposer}
            >
              {enCours ? tr('Dépôt en cours…') : tr('Déposer')}
            </Bouton>
          </>
        )}

        {envois.length > 0 && (
          <ul className="space-y-1.5 text-sm" aria-live="polite">
            {envois.map((e, i) => (
              <li
                key={i}
                className={
                  e.etat === 'erreur'
                    ? 'text-rate'
                    : e.etat === 'ok'
                      ? 'text-bien'
                      : 'text-texte-doux'
                }
              >
                {e.etat === 'ok' ? '✓' : e.etat === 'erreur' ? '✕' : '…'} {e.nom}
                {e.message && <span className="block pl-4 text-xs">{e.message}</span>}
              </li>
            ))}
          </ul>
        )}
      </Carte>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">{tr('Fichiers déposés')}</h2>
          {nbAttente > 0 && (
            <Etiquette couleur="#F5B84A">
              {trn(nbAttente, '{n} à traiter', '{n} à traiter')}
            </Etiquette>
          )}
        </div>
        {depots.length === 0 ? (
          <Vide titre={tr('Aucun fichier déposé.')} />
        ) : (
          parCours.map((nom) => (
            <Carte key={nom} className="space-y-3">
              <div className="flex items-center gap-2.5">
                <Pastille couleur={couleurs[nom] ?? '#8B7CFF'} />
                <p className="font-medium">{nom}</p>
              </div>
              <ul className="divide-y divide-bordure">
                {depots
                  .filter((d) => d.cours === nom)
                  .map((d) => (
                    <li key={d.id} className="flex items-center gap-3 py-2.5 text-sm">
                      <div className="min-w-0 flex-1">
                        <a
                          href={api.urlDepot(d.id)}
                          className="block truncate text-texte hover:text-accent"
                          title={tr('Télécharger')}
                        >
                          {d.nom}
                        </a>
                        <p className="text-xs text-texte-doux">
                          {taille(d.taille)} · {d.depose_par} ·{' '}
                          {new Date(d.depose_le).toLocaleDateString(localeAffichage())}
                        </p>
                      </div>
                      {d.traite_le ? (
                        <Etiquette couleur="#4ADE9F">{tr('Traité')}</Etiquette>
                      ) : (
                        <Etiquette>{tr('À traiter')}</Etiquette>
                      )}
                      <Bouton
                        variante="fantome"
                        className="min-h-9 px-2.5"
                        onClick={() => supprimer(d)}
                        aria-label={tr('Supprimer « {nom} » ?', { nom: d.nom })}
                      >
                        ✕
                      </Bouton>
                    </li>
                  ))}
              </ul>
            </Carte>
          ))
        )}
      </section>
    </div>
  )
}
