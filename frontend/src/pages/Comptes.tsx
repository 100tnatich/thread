import { useEffect, useState } from 'react'

import { type Compte, type Role, api } from '../api'
import { Bouton, Carte, Champ, Chargement, Erreur, Etiquette } from '../composants/ui'
import { localeAffichage, tr } from '../i18n'
import { LIBELLE_ROLE } from '../role'

const ROLES: Role[] = ['standard', 'etendu', 'admin']
const AIDE_ROLE: Record<Role, string> = {
  admin: 'Tous les droits : comptes, réglages système, dépôt.',
  standard: "L'appli de révision.",
  etendu: "L'appli de révision + la page Dépôt (cours et fichiers).",
}
const SELECT =
  'min-h-11 rounded-xl border border-bordure bg-surface px-3.5 text-sm text-texte focus:border-accent focus:outline-none'

/** Gestion des comptes (administrateur). Les données restent partagées: le rôle
 *  décide de ce qu'un compte peut faire, pas de ce qu'il voit. */
export default function Comptes({ moi }: { moi: string }) {
  const [comptes, setComptes] = useState<Compte[] | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const [identifiant, setIdentifiant] = useState('')
  const [mdp, setMdp] = useState('')
  const [role, setRole] = useState<Role>('standard')
  const [mdpDe, setMdpDe] = useState<string | null>(null)
  const [nouveauMdp, setNouveauMdp] = useState('')

  const charger = () =>
    api
      .comptes()
      .then(setComptes)
      .catch((e) => setErreur(e.message))

  useEffect(() => {
    charger()
  }, [])

  async function agir(action: () => Promise<unknown>, ok: string) {
    setErreur(null)
    setMessage(null)
    try {
      await action()
      setMessage(ok)
      await charger()
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function creer(e: React.FormEvent) {
    e.preventDefault()
    await agir(() => api.creerCompte(identifiant.trim(), mdp, role), tr('Compte créé.'))
    setIdentifiant('')
    setMdp('')
  }

  if (!comptes) return erreur ? <Erreur message={erreur} /> : <Chargement />

  return (
    <div className="animer-entree space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">{tr('Comptes')}</h1>

      <Erreur message={erreur} />
      {message && (
        <p className="rounded-xl border border-bien/40 bg-bien/10 px-3.5 py-2.5 text-sm text-bien">
          {message}
        </p>
      )}

      <Carte className="space-y-1 text-sm text-texte-doux">
        {ROLES.map((r) => (
          <p key={r}>
            <span className="font-medium text-texte">{tr(LIBELLE_ROLE[r])}</span> —{' '}
            {tr(AIDE_ROLE[r])}
          </p>
        ))}
      </Carte>

      <ul className="space-y-3">
        {comptes.map((c) => (
          <li key={c.identifiant}>
            <Carte className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <p className="min-w-0 flex-1 truncate font-medium">
                  {c.identifiant}
                  {c.identifiant === moi && (
                    <span className="ml-2 text-xs text-texte-doux">{tr('(toi)')}</span>
                  )}
                </p>
                <select
                  value={c.role}
                  onChange={(e) =>
                    agir(
                      () => api.modifierCompte(c.identifiant, { role: e.target.value as Role }),
                      tr('Rôle modifié.'),
                    )
                  }
                  className={SELECT}
                  aria-label={tr('Rôle de {nom}', { nom: c.identifiant })}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {tr(LIBELLE_ROLE[r])}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Etiquette>
                  {tr('Créé le {date}', {
                    date: new Date(c.cree_le).toLocaleDateString(localeAffichage()),
                  })}
                </Etiquette>
                <span className="flex-1" />
                <Bouton
                  variante="fantome"
                  onClick={() => {
                    setMdpDe(mdpDe === c.identifiant ? null : c.identifiant)
                    setNouveauMdp('')
                  }}
                >
                  {tr('Nouveau mot de passe')}
                </Bouton>
                {c.identifiant !== moi && (
                  <Bouton
                    variante="danger"
                    onClick={() => {
                      if (!confirm(tr('Supprimer « {nom} » ?', { nom: c.identifiant }))) return
                      agir(() => api.supprimerCompte(c.identifiant), tr('Compte supprimé.'))
                    }}
                  >
                    {tr('Supprimer')}
                  </Bouton>
                )}
              </div>
              {mdpDe === c.identifiant && (
                <form
                  className="flex gap-2"
                  onSubmit={async (e) => {
                    e.preventDefault()
                    await agir(
                      () => api.modifierCompte(c.identifiant, { mot_de_passe: nouveauMdp }),
                      tr('Mot de passe modifié.'),
                    )
                    setMdpDe(null)
                  }}
                >
                  <Champ
                    type="password"
                    value={nouveauMdp}
                    onChange={(e) => setNouveauMdp(e.target.value)}
                    placeholder={tr('Nouveau mot de passe (12 caractères minimum)')}
                    autoComplete="new-password"
                    required
                  />
                  <Bouton variante="accent" type="submit">
                    {tr('Enregistrer')}
                  </Bouton>
                </form>
              )}
            </Carte>
          </li>
        ))}
      </ul>

      <Carte>
        <form onSubmit={creer} className="space-y-3">
          <p className="font-medium">{tr('Nouveau compte')}</p>
          <label className="block space-y-1.5">
            <span className="text-sm text-texte-doux">{tr('Identifiant')}</span>
            <Champ
              value={identifiant}
              onChange={(e) => setIdentifiant(e.target.value)}
              autoComplete="off"
              maxLength={40}
              required
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm text-texte-doux">{tr('Mot de passe')}</span>
            <Champ
              type="password"
              value={mdp}
              onChange={(e) => setMdp(e.target.value)}
              placeholder={tr('12 caractères minimum')}
              autoComplete="new-password"
              required
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm text-texte-doux">{tr('Rôle')}</span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as Role)}
              aria-label={tr('Rôle du nouveau compte')}
              className={`${SELECT} w-full`}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {tr(LIBELLE_ROLE[r])}
                </option>
              ))}
            </select>
          </label>
          <Bouton variante="accent" type="submit" className="w-full" disabled={!identifiant || !mdp}>
            {tr('Créer le compte')}
          </Bouton>
        </form>
      </Carte>
    </div>
  )
}
