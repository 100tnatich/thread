/** Rôle du compte connecté. Sert à n'afficher que ce que le serveur autorise:
 *  la vraie protection est côté serveur, ici on évite seulement les culs-de-sac. */
import { createContext, useContext } from 'react'

import type { Role } from './api'

export const RoleContext = createContext<Role>('standard')
export const useRole = () => useContext(RoleContext)

export const estAdmin = (r: Role) => r === 'admin'
/** Page Dépôt: comptes étendus, et l'admin qui a tous les droits. */
export const peutDeposer = (r: Role) => r === 'admin' || r === 'etendu'

export const LIBELLE_ROLE: Record<Role, string> = {
  admin: 'Administrateur',
  standard: 'Standard',
  etendu: 'Étendu',
}
