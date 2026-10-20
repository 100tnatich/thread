import { useEffect, useState } from 'react'
import {
  NavLink,
  Navigate,
  Route,
  BrowserRouter as Router,
  Routes,
  useLocation,
} from 'react-router-dom'

import { ErreurApi, type Role, api } from './api'
import Avatar from './composants/Avatar'
import Logo, { FilDeFond } from './composants/Logo'
import SelecteurLangue from './composants/SelecteurLangue'
import { Bouton, Champ, Chargement, Erreur } from './composants/ui'
import { tr, useLangue } from './i18n'
import Aujourdhui from './pages/Aujourdhui'
import Session from './pages/Session'
import Ajouter from './pages/Ajouter'
import TableauDeBord from './pages/TableauDeBord'
import Tests from './pages/Tests'
import Bibliotheque from './pages/Bibliotheque'
import PageCours from './pages/Cours'
import Comptes from './pages/Comptes'
import Depot from './pages/Depot'
import Reglages from './pages/Reglages'
import { RoleContext, estAdmin, peutDeposer, useRole } from './role'

// `libelle` est une clé de traduction (voir i18n.ts), traduite à l'affichage.
// `visible`: qui voit le lien (absent = tout le monde). Le serveur refuse de toute
// façon l'accès aux routes qu'un rôle n'a pas.
const LIENS: {
  to: string
  libelle: string
  icone: string
  visible?: (r: Role) => boolean
}[] = [
  { to: '/', libelle: "Aujourd'hui", icone: '◉' },
  { to: '/ajouter', libelle: 'Ajouter', icone: '＋' },
  { to: '/tableau', libelle: 'Tableau', icone: '▦' },
  { to: '/tests', libelle: 'Tests', icone: '◷' },
  { to: '/bibliotheque', libelle: 'Bibliothèque', icone: '≣' },
  { to: '/cours', libelle: 'Matières', icone: '◆' },
  { to: '/depot', libelle: 'Dépôt', icone: '↑', visible: peutDeposer },
  { to: '/comptes', libelle: 'Comptes', icone: '◐', visible: estAdmin },
  { to: '/reglages', libelle: 'Réglages', icone: '⚙' },
]

function Connexion({ onConnecte }: { onConnecte: () => void }) {
  useLangue() // re-rend à chaque changement de langue
  const [identifiant, setIdentifiant] = useState('')
  const [motDePasse, setMotDePasse] = useState('')
  const [erreur, setErreur] = useState<string | null>(null)
  const [envoi, setEnvoi] = useState(false)

  async function soumettre(e: React.FormEvent) {
    e.preventDefault()
    setErreur(null)
    setEnvoi(true)
    try {
      await api.connexion(identifiant, motDePasse)
      onConnecte()
    } catch (err) {
      setErreur(
        err instanceof ErreurApi ? err.message : tr('Connexion impossible.'),
      )
    } finally {
      setEnvoi(false)
    }
  }

  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden p-6">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-x-0 top-0 h-[60vh] bg-[radial-gradient(ellipse_at_50%_0%,var(--color-accent-doux),transparent_70%)]"
      />
      <FilDeFond />
      <SelecteurLangue className="fixed top-4 right-4 z-10" />
      <form
        onSubmit={soumettre}
        className="animer-entree relative w-full max-w-sm space-y-6 rounded-[var(--radius-carte)] border border-bordure bg-surface/85 p-7 shadow-2xl shadow-black/40 backdrop-blur-md"
      >
        <div className="space-y-2">
          <h1>
            <Logo grand />
          </h1>
          <p className="text-sm text-texte-doux">
            {tr('Révisions pilotées par tes dates de test.')}
          </p>
        </div>

        <div className="space-y-3">
          <label className="block space-y-1.5">
            <span className="text-sm text-texte-doux">{tr('Identifiant')}</span>
            <Champ
              value={identifiant}
              onChange={(e) => setIdentifiant(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm text-texte-doux">{tr('Mot de passe')}</span>
            <Champ
              type="password"
              value={motDePasse}
              onChange={(e) => setMotDePasse(e.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
        </div>

        <Erreur message={erreur} />

        <Bouton
          type="submit"
          variante="accent"
          className="w-full"
          disabled={envoi}
        >
          {envoi ? tr('Connexion…') : tr('Se connecter')}
        </Bouton>
      </form>
    </main>
  )
}

function Navigation({ identifiant }: { identifiant: string }) {
  const role = useRole()
  const liens = LIENS.filter((l) => !l.visible || l.visible(role))
  // Au-delà de 7 liens la barre basse ne tient plus sur un téléphone: elle défile.
  const serre = liens.length > 7
  return (
    <>
      {/* Mobile: la barre basse est pleine, le sélecteur de langue flotte en haut. */}
      <SelecteurLangue className="fixed top-3 right-3 z-40 md:hidden" />

      {/* Mobile: barre basse, pouce accessible. */}
      <nav className="zone-sure fixed inset-x-0 bottom-0 z-40 border-t border-bordure bg-surface/95 backdrop-blur md:hidden">
        <ul className={serre ? 'flex overflow-x-auto' : 'flex'}>
          {liens.map((l) => (
            <li key={l.to} className={serre ? 'min-w-[4.75rem] shrink-0 flex-1' : 'flex-1'}>
              <NavLink
                to={l.to}
                end={l.to === '/'}
                className={({ isActive }) =>
                  `flex min-h-14 flex-col items-center justify-center gap-0.5 text-[10px] transition-colors ${
                    isActive ? 'text-accent' : 'text-texte-doux'
                  }`
                }
              >
                <span aria-hidden className="text-base">
                  {l.icone}
                </span>
                {tr(l.libelle)}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      {/* Desktop: rail latéral. */}
      <nav className="fixed inset-y-0 left-0 hidden w-56 flex-col border-r border-bordure bg-surface p-4 md:flex">
        <div className="px-3 py-4">
          <Logo />
        </div>
        <ul className="flex flex-1 flex-col gap-1">
          {liens.map((l) => (
            <li key={l.to}>
              <NavLink
                to={l.to}
                end={l.to === '/'}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors ${
                    isActive
                      ? 'bg-accent-doux text-accent'
                      : 'text-texte-doux hover:bg-surface-haute hover:text-texte'
                  }`
                }
              >
                <span aria-hidden className="w-4 text-center">
                  {l.icone}
                </span>
                {tr(l.libelle)}
              </NavLink>
            </li>
          ))}
        </ul>
        <SelecteurLangue className="mb-2 self-start" />
        <div className="flex items-center gap-2">
          <Avatar identifiant={identifiant} role={role} />
          <Bouton
            variante="fantome"
            className="flex-1 text-left"
            onClick={async () => {
              await api.deconnexion()
              location.reload()
            }}
          >
            {tr('Se déconnecter')}
          </Bouton>
        </div>
      </nav>
    </>
  )
}

function Layout({ children, identifiant }: { children: React.ReactNode; identifiant: string }) {
  const { pathname } = useLocation()
  // La session est en plein écran: aucune navigation ne doit distraire.
  const pleinEcran = pathname.startsWith('/session')

  if (pleinEcran) return <>{children}</>

  return (
    <div className="min-h-dvh md:pl-56">
      <Navigation identifiant={identifiant} />
      <main className="mx-auto max-w-4xl px-4 pt-6 pb-24 md:px-8 md:pb-10">
        {children}
      </main>
    </div>
  )
}

export default function App() {
  const lang = useLangue()
  const [etat, setEtat] = useState<'charge' | 'connecte' | 'deconnecte'>(
    'charge',
  )
  const [moi, setMoi] = useState<{ identifiant: string; role: Role }>({
    identifiant: '',
    role: 'standard',
  })

  const verifier = () =>
    api
      .moi()
      .then((m) => {
        setMoi(m)
        setEtat('connecte')
      })
      .catch(() => setEtat('deconnecte'))

  useEffect(() => {
    verifier()
  }, [])

  if (etat === 'charge')
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Chargement />
      </div>
    )

  if (etat === 'deconnecte')
    return <Connexion onConnecte={verifier} />

  return (
    // `key`: à chaque changement de langue, les pages sont remontées pour se
    // réafficher dans la nouvelle langue (l'URL, elle, est conservée).
    <RoleContext.Provider value={moi.role}>
    <Router key={lang}>
      <Layout identifiant={moi.identifiant}>
        <Routes>
          <Route path="/" element={<Aujourdhui />} />
          <Route path="/session" element={<Session />} />
          <Route path="/ajouter" element={<Ajouter />} />
          <Route path="/tableau" element={<TableauDeBord />} />
          <Route path="/tests" element={<Tests />} />
          <Route path="/bibliotheque" element={<Bibliotheque />} />
          <Route path="/cours" element={<PageCours />} />
          <Route path="/reglages" element={<Reglages />} />
          {peutDeposer(moi.role) && <Route path="/depot" element={<Depot />} />}
          {estAdmin(moi.role) && (
            <Route path="/comptes" element={<Comptes moi={moi.identifiant} />} />
          )}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </Router>
    </RoleContext.Provider>
  )
}
