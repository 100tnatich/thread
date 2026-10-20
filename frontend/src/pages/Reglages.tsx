import { useEffect, useState } from 'react'

import { type Budget, type Reglages as TypeReglages, api } from '../api'
import {
  Bouton,
  Carte,
  Champ,
  Chargement,
  Erreur,
  Etiquette,
} from '../composants/ui'
import SelecteurLangue from '../composants/SelecteurLangue'
import { tr } from '../i18n'
import { estAdmin, useRole } from '../role'

export default function Reglages() {
  const admin = estAdmin(useRole())
  const [reglages, setReglages] = useState<TypeReglages | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  const [ancien, setAncien] = useState('')
  const [nouveau, setNouveau] = useState('')
  const [budget, setBudget] = useState<Budget | null>(null)

  useEffect(() => {
    api
      .reglages()
      .then(setReglages)
      .catch((e) => setErreur(e.message))
    api
      .budget()
      .then(setBudget)
      .catch((e) => setErreur(e.message))
  }, [])

  async function basculerArret(coupe: boolean) {
    setErreur(null)
    try {
      await api.arretLLM(coupe)
      setBudget(await api.budget())
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function enregistrer(champs: Partial<TypeReglages>) {
    setErreur(null)
    try {
      await api.modifierReglages(champs)
      setReglages(await api.reglages())
      setMessage(tr('Enregistré.'))
      setTimeout(() => setMessage(null), 2000)
    } catch (e) {
      setErreur(e instanceof Error ? e.message : tr('Erreur'))
    }
  }

  async function changerMdp(e: React.FormEvent) {
    e.preventDefault()
    setErreur(null)
    try {
      await api.changerMdp(ancien, nouveau)
      setAncien('')
      setNouveau('')
      setMessage(tr('Mot de passe modifié.'))
    } catch (err) {
      setErreur(err instanceof Error ? err.message : tr('Erreur'))
    }
  }

  async function exporter() {
    const data = await api.export()
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `flashcards-export-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (!reglages) return <Chargement />

  // `libelle` et `aide` sont des clés de traduction, traduites à l'affichage.
  const NOMBRES: {
    cle: keyof TypeReglages
    libelle: string
    aide: string
    min: number
    max: number
    pas: number
  }[] = [
    {
      cle: 'plafond_quotidien',
      libelle: 'Plafond quotidien (en points)',
      aide: 'Points maximum par jour: une question pèse entre 0,1 et 4 points selon son type (voir ci-dessous). Le surplus est reporté, jamais perdu.',
      min: 1,
      max: 500,
      pas: 1,
    },
    {
      cle: 'retention_normale',
      libelle: 'Rétention cible normale',
      aide: 'Hors période de test. Plus haut = révisions plus fréquentes.',
      min: 0.7,
      max: 0.99,
      pas: 0.01,
    },
    {
      cle: 'retention_test',
      libelle: 'Rétention cible avant test',
      aide: 'Atteinte le jour du test, par montée progressive.',
      min: 0.8,
      max: 0.99,
      pas: 0.01,
    },
    {
      cle: 'jours_phase_finale',
      libelle: 'Jours de phase finale',
      aide: 'Durée de la montée en intensité avant un test.',
      min: 1,
      max: 60,
      pas: 1,
    },
    {
      cle: 'buffer_jours_avant_test',
      libelle: 'Marge avant le test',
      aide: 'Dernière révision au plus tard ce nombre de jours avant le test.',
      min: 0,
      max: 14,
      pas: 1,
    },
  ]

  const ENTRETIEN: typeof NOMBRES = [
    {
      cle: 'entretien_quota_hebdo',
      libelle: 'Quota hebdomadaire par thème (points)',
      aide: 'Plancher: chaque thème sans test à venir reçoit au moins ces points de révision par semaine (lundi à dimanche). Si FSRS en a déjà planifié assez, rien n’est ajouté.',
      min: 0,
      max: 100,
      pas: 0.5,
    },
    {
      cle: 'entretien_plafond_jour',
      libelle: 'Plafond d’entretien par jour (points)',
      aide: 'Tous thèmes confondus, avec rotation (le thème révisé le moins récemment passe en premier). Il limite l’étalement, pas le quota hebdomadaire.',
      min: 0,
      max: 100,
      pas: 0.5,
    },
    {
      cle: 'entretien_poids_max',
      libelle: 'Poids maximum préféré en entretien',
      aide: 'On complète le quota de préférence avec des questions de ce poids ou moins.',
      min: 0.1,
      max: 20,
      pas: 0.1,
    },
  ]

  const POIDS_TYPE: [string, string][] = [
    ['exact', 'Réponse exacte'],
    ['cloze', 'Texte à trous'],
    ['qcm', 'QCM'],
    ['libre_llm', 'Réponse libre'],
    ['probleme', 'Problème (calcul)'],
    ['redaction', 'Rédaction'],
  ]

  const champPoids = (
    libelle: string,
    valeur: number,
    enregistrerPoids: (v: number) => void,
  ) => (
    <label key={libelle} className="block space-y-1.5">
      <span className="text-sm">{tr(libelle)}</span>
      <Champ
        type="number"
        min={0.05}
        max={20}
        step={0.05}
        defaultValue={valeur}
        onBlur={(e) => {
          const v = Number(e.target.value)
          if (v > 0 && v !== valeur) enregistrerPoids(v)
        }}
        aria-label={tr('Poids {nom}', { nom: libelle })}
      />
    </label>
  )

  return (
    <div className="animer-entree space-y-6">
      <h1 className="text-3xl font-semibold tracking-tight">
        {tr('Réglages')}
      </h1>

      <Erreur message={erreur} />
      {message && (
        <p className="rounded-xl border border-bien/40 bg-bien/10 px-3.5 py-2.5 text-sm text-bien">
          {message}
        </p>
      )}

      <Carte className="flex items-center justify-between gap-3">
        <p className="font-medium">{tr('Langue')}</p>
        <SelecteurLangue />
      </Carte>

      <Carte className="space-y-5">
        <p className="font-medium">{tr('Planification')}</p>
        {NOMBRES.map((n) => (
          <label key={n.cle} className="block space-y-1.5">
            <span className="text-sm">{tr(n.libelle)}</span>
            <Champ
              type="number"
              min={n.min}
              max={n.max}
              step={n.pas}
              defaultValue={reglages[n.cle] as number}
              onBlur={(e) => {
                const valeur = Number(e.target.value)
                if (valeur !== reglages[n.cle])
                  enregistrer({ [n.cle]: valeur } as Partial<TypeReglages>)
              }}
            />
            <span className="block text-xs text-texte-doux">{tr(n.aide)}</span>
          </label>
        ))}
      </Carte>

      <Carte className="space-y-5">
        <div>
          <p className="font-medium">{tr('Poids des questions (points)')}</p>
          <p className="mt-1 text-xs text-texte-doux">
            {tr(
              'Le plafond et la dette se comptent en points. Le poids d’une carte : son poids explicite s’il existe, sinon celui de sa catégorie (vocabulaire), sinon celui de son type.',
            )}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          {POIDS_TYPE.map(([type, libelle]) =>
            champPoids(libelle, reglages.poids_type[type] ?? 1, (v) =>
              enregistrer({ poids_type: { [type]: v } }),
            ),
          )}
          {champPoids(
            'Vocabulaire (prime sur le type)',
            reglages.poids_categorie.vocabulaire ?? 0.1,
            (v) => enregistrer({ poids_categorie: { vocabulaire: v } }),
          )}
        </div>
      </Carte>

      <Carte className="space-y-5">
        <div>
          <p className="font-medium">{tr('Mode entretien')}</p>
          <p className="mt-1 text-xs text-texte-doux">
            {tr(
              'Un thème dont tous les tests sont passés (ou sans test) passe en entretien : plus de date à viser, un quota hebdomadaire en points. Dès qu’un test futur lui est lié, il repasse en mode test.',
            )}
          </p>
        </div>
        {ENTRETIEN.map((n) => (
          <label key={n.cle} className="block space-y-1.5">
            <span className="text-sm">{tr(n.libelle)}</span>
            <Champ
              type="number"
              min={n.min}
              max={n.max}
              step={n.pas}
              defaultValue={reglages[n.cle] as number}
              onBlur={(e) => {
                const valeur = Number(e.target.value)
                if (valeur !== reglages[n.cle])
                  enregistrer({ [n.cle]: valeur } as Partial<TypeReglages>)
              }}
            />
            <span className="block text-xs text-texte-doux">{tr(n.aide)}</span>
          </label>
        ))}
      </Carte>

      <Carte className="space-y-4">
        <p className="font-medium">{tr('Session')}</p>
        <fieldset>
          <legend className="mb-2 text-sm">
            {tr('Ordre des questions par défaut')}
          </legend>
          <div className="flex gap-2">
            {(
              [
                ['melange', tr('Mélangé')],
                ['par_cours', tr('Par cours')],
              ] as const
            ).map(([valeur, libelle]) => (
              <Bouton
                key={valeur}
                selectionne={reglages.ordre_session === valeur}
                onClick={() => enregistrer({ ordre_session: valeur })}
              >
                {libelle}
              </Bouton>
            ))}
          </div>
        </fieldset>
      </Carte>

      {admin && (
        <Carte className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <p className="font-medium">{tr('Modèle LLM')}</p>
            {reglages.mock_llm && (
              <Etiquette couleur="#F5B84A">{tr('Mode simulé')}</Etiquette>
            )}
          </div>
          <Champ
            defaultValue={reglages.modele}
            onBlur={(e) => {
              if (e.target.value !== reglages.modele)
                enregistrer({ modele: e.target.value })
            }}
          />
          <p className="text-xs text-texte-doux">
            {reglages.mock_llm
              ? tr(
                  "Mode simulé : l'extraction et la génération renvoient des données factices, sans aucune dépense. Le mode réel demande FLASHCARDS_LLM_REEL=1 et ANTHROPIC_API_KEY côté serveur.",
                )
              : tr('Utilisé pour extraire les concepts et générer les questions.')}
          </p>
        </Carte>
      )}

      <Carte className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <p className="font-medium">{tr('Correction par IA')}</p>
          {reglages.juge_etat === 'reel' ? (
            <Etiquette couleur="#5BD49A">{tr('Active')}</Etiquette>
          ) : reglages.juge_etat === 'simule' ? (
            <Etiquette couleur="#F5B84A">{tr('Simulée')}</Etiquette>
          ) : (
            <Etiquette>{tr('Inactive')}</Etiquette>
          )}
        </div>
        <p className="text-xs text-texte-doux">
          {reglages.juge_etat === 'reel'
            ? tr(
                'Les réponses libres et les rédactions sont corrigées par {modele} (OpenRouter), à ta demande. Environ 0,0002 USD par correction, compté dans les plafonds du crédit API.',
                { modele: reglages.juge_modele },
              )
            : reglages.juge_etat === 'simule'
              ? tr('Mode simulé : les corrections sont factices et ne coûtent rien.')
              : tr(
                  "Non activée : les réponses libres et les rédactions restent en auto-évaluation. Pour l'activer, mets OPENROUTER_API_KEY et FLASHCARDS_JUGE_REEL=1 dans .env, puis relance.",
                )}
        </p>
      </Carte>

      {budget && (
        <Carte className="space-y-4">
          <div className="flex items-center justify-between gap-3">
            <p className="font-medium">{tr('Crédit API')}</p>
            {budget.llm_desactive ? (
              <Etiquette couleur="#F26D6D">{tr('Appels coupés')}</Etiquette>
            ) : budget.mode_reel ? (
              <Etiquette couleur="#5BD49A">{tr('Mode réel')}</Etiquette>
            ) : (
              <Etiquette couleur="#F5B84A">{tr('Mode simulé')}</Etiquette>
            )}
          </div>
          <dl className="space-y-2 text-sm">
            {(
              [
                [tr("Aujourd'hui"), budget.cout_jour_usd, budget.plafond_jour_usd, budget.appels_jour],
                [tr('Ce mois'), budget.cout_mois_usd, budget.plafond_mois_usd, budget.appels_mois],
              ] as const
            ).map(([libelle, depense, plafond, appels]) => (
              <div key={libelle}>
                <div className="flex justify-between gap-3">
                  <dt className="text-texte-doux">{libelle}</dt>
                  <dd>
                    {depense.toFixed(4)} / {plafond.toFixed(2)} USD · {tr('{n} appel(s)', { n: appels })}
                  </dd>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-bordure">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${plafond > 0 ? Math.min(100, (depense / plafond) * 100) : 100}%`,
                      background: plafond > 0 && depense < 0.8 * plafond ? '#8B7CFF' : '#F26D6D',
                    }}
                  />
                </div>
              </div>
            ))}
          </dl>
          <p className="text-xs text-texte-doux">
            {tr(
              "Au plus {max} USD par opération. {evites} appel(s) évité(s) par l'anti-doublon, {refus} refus enregistré(s). Les plafonds se règlent côté serveur (config.toml ou variables d'environnement), pas ici.",
              {
                max: budget.plafond_operation_usd.toFixed(2),
                evites: budget.appels_evites,
                refus: budget.refus,
              },
            )}
          </p>
          {budget.alertes.map((a) => (
            <p key={a} role="alert" className="text-sm text-[#F26D6D]">
              ⚠ {a}
            </p>
          ))}
          {budget.disjoncteur_ouvert && (
            <p role="alert" className="text-sm text-[#F26D6D]">
              {tr('⚠ Disjoncteur ouvert : appels bloqués après des échecs répétés.')}
            </p>
          )}
          {!admin ? null : budget.arret_par_env ? (
            <p className="text-xs text-texte-doux">
              {tr('Coupé par LLM_DISABLED=1 sur le serveur : à lever là-bas.')}
            </p>
          ) : (
            <Bouton
              className="w-full"
              onClick={() => basculerArret(!budget.llm_desactive)}
            >
              {budget.llm_desactive
                ? tr('Rétablir les appels au modèle')
                : tr("Arrêt d'urgence : couper tous les appels au modèle")}
            </Bouton>
          )}
        </Carte>
      )}

      <Carte className="space-y-4">
        <p className="font-medium">{tr('Mot de passe')}</p>
        <form onSubmit={changerMdp} className="space-y-3">
          <Champ
            type="password"
            value={ancien}
            onChange={(e) => setAncien(e.target.value)}
            placeholder={tr('Mot de passe actuel')}
            autoComplete="current-password"
            required
          />
          <Champ
            type="password"
            value={nouveau}
            onChange={(e) => setNouveau(e.target.value)}
            placeholder={tr('Nouveau mot de passe (12 caractères minimum)')}
            autoComplete="new-password"
            minLength={12}
            required
          />
          <Bouton type="submit" className="w-full">
            {tr('Changer le mot de passe')}
          </Bouton>
        </form>
      </Carte>

      <Carte className="space-y-3">
        <p className="font-medium">{tr('Données')}</p>
        <p className="text-sm text-texte-doux">
          {tr('Export complet : matières, cours, questions, tests et historique de révision.')}
        </p>
        <Bouton className="w-full" onClick={exporter}>
          {tr('Exporter en JSON')}
        </Bouton>
      </Carte>
    </div>
  )
}
