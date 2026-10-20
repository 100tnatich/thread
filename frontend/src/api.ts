/** Client de l'API REST. Typage miroir des schémas Pydantic du backend. */

export type TypeQuestion =
  | 'qcm'
  | 'exact'
  | 'cloze'
  | 'probleme'
  | 'libre_llm'
  | 'redaction'

export interface Source {
  fichier: string
  page: number | null
}

interface Commun {
  id: string
  concept_id: string
  cours: string
  source: Source
  source_ref?: string | null
  confiance?: 'haute' | 'moyenne' | 'basse'
}

export type Payload =
  | (Commun & {
      type: 'qcm'
      enonce: string
      propositions: string[]
      bonnes_reponses: number[]
    })
  | (Commun & { type: 'exact'; enonce: string; reponse_exacte: string })
  | (Commun & {
      type: 'cloze'
      texte: string
      valeurs_attendues: Record<string, string>
    })
  | (Commun & {
      type: 'probleme'
      enonce: string
      solution_etapes: string[]
      reponse_finale: string
      necessite_dessin: boolean
      schema_svg: string | null
    })
  | (Commun & { type: 'libre_llm'; enonce: string; reponse_attendue: string })
  | (Commun & {
      type: 'redaction'
      consigne: string
      contraintes: string
      criteres: string[]
      exemple_reference: string
    })

export interface QuestionSession {
  card_id: string
  type: TypeQuestion
  cours: string
  couleur_cours: string
  payload: Payload
  jours_retard: number
  nouvelle: boolean
  /** Poids en points; `entretien`: carte d'un thème sans test à venir. */
  poids: number
  entretien: boolean
}

/** Correction des réponses libres par IA: réelle, simulée (tests) ou inactive. */
export type EtatJuge = 'reel' | 'simule' | 'indisponible'

/** Récap théorique d'un thème (Markdown), lu avant ses questions en mode « par cours ». */
export interface RecapTheme {
  cours: string
  couleur: string
  texte: string
}

export interface Queue {
  jour: string
  session_id: string
  total: number
  total_dues: number
  /** Plafond et totaux en POINTS. */
  plafond: number
  points: number
  points_entretien: number
  juge: EtatJuge
  /** Ordre réellement appliqué par le serveur. */
  ordre: 'melange' | 'par_cours'
  /** Vide en mode mélangé. */
  recaps: RecapTheme[]
  questions: QuestionSession[]
}

/** Résultat de `POST /api/juger`. `disponible=false`: auto-évaluation, voir `raison`. */
export interface Jugement {
  disponible: boolean
  etat: EtatJuge
  /** Note FSRS suggérée; l'élève peut la changer avant de valider. */
  note_suggeree: 1 | 2 | 3 | 4 | null
  verdict: 'juste' | 'partiel' | 'faux' | null
  commentaire: string | null
  /** Rédaction: un booléen par critère. */
  criteres: boolean[] | null
  raison: string | null
}

/** Un cours = un thème précis (ex. « Anglais conditionnel »). */
export interface Cours {
  nom: string
  couleur: string
  nb_cartes: number
  points: number
  matiere: string | null
  archive: boolean
  profil: string
  /** Rappel théorique (Markdown) lu avant les questions du thème. */
  recap: string
  /** Aucun test à venir et pas archivé. */
  en_entretien: boolean
}

export interface Matiere {
  nom: string
  couleur: string
  profil: string
  nb_cours: number
  nb_cartes: number
  nb_cours_entretien: number
}

export interface Test {
  id: number
  /** Thèmes couverts (liens directs + thèmes des matières liées). */
  cours: string[]
  matieres: string[]
  date: string
  intitule: string
}

export interface Today {
  jour: string
  total: number
  total_dues: number
  nb_nouvelles: number
  nb_en_retard: number
  par_cours: {
    cours: string
    couleur: string
    nb: number
    points: number
    entretien: boolean
  }[]
  prochain_test: Test | null
  jours_avant_test: number | null
  points: number
  points_dus: number
  plafond: number
  nb_entretien: number
  points_entretien: number
  part_entretien: number
  themes_entretien: string[]
  alertes: string[]
}

export interface Stats {
  jour: string
  total_cartes: number
  nb_dues: number
  nb_nouvelles: number
  nb_en_retard: number
  charge_14j: ChargeJour[]
  tests: {
    test: Test
    nb_cartes: number
    nb_jamais_revisees: number
    retention_estimee: number
  }[]
  points_dus: number
  points_jour: number
  plafond: number
  part_entretien: number
  points_entretien_jour: number
  nb_themes: number
  themes_entretien: string[]
  quotas: {
    theme: string
    quota: number
    fait_avant_aujourdhui: number
    planifie_fsrs: number
    manque: number
  }[]
  alertes: string[]
}

export interface ChargeJour {
  jour: string
  nb: number
  points: number
  nb_entretien: number
  points_entretien: number
}

export interface Carte {
  id: string
  concept_id: string
  cours: string
  type: TypeQuestion
  payload: Payload
  desactivee: boolean
  note_qualite: string | null
  nb_revisions: number
  stabilite: number | null
  source_ref: string | null
  confiance: 'haute' | 'moyenne' | 'basse'
  poids: number | null
  categorie: string | null
  points: number
}

export interface ConceptExtrait {
  id: string
  titre: string
  categorie:
    | 'definition'
    | 'propriete'
    | 'formule'
    | 'methode'
    | 'fait'
    | 'vocabulaire'
    | 'exemple_type'
  resume: string
  source: string | null
  importance: 'essentiel' | 'important' | 'secondaire'
  adapte_calcul: boolean
}

/** Import de questions déjà générées (sans LLM): miroir de `manuel.Rapport`. */
export interface ApercuQuestionImport {
  position: number
  concept_id: string
  type: TypeQuestion
  resume: string
  confiance: 'haute' | 'moyenne' | 'basse'
  statut: 'nouvelle' | 'doublon_fichier' | 'deja_en_base'
}

export interface LigneCouverture {
  concept_id: string
  titre: string
  nb_questions: number
}

export interface RapportImport {
  fichier: string
  valide: boolean
  cours: string | null
  source: string | null
  erreurs_fichier: string[]
  erreurs: { position: number; reference: string; messages: string[] }[]
  avertissements: string[]
  nb_elements: number
  par_type: Record<string, number>
  confiance_basse: ApercuQuestionImport[]
  apercu: ApercuQuestionImport[]
  concepts_fichier: string | null
  couverture: LigneCouverture[]
  concepts_sans_question: LigneCouverture[]
}

export interface ApercuImport {
  rapport: RapportImport
  importable: boolean
  nouvelles: number
  deja_en_base: number
  doublons_fichier: number
}

export interface ResultatImport {
  importees: number
  deja_presentes: number
  doublons_contenu: number
  cours: string[]
}

export interface Job {
  id: string
  type: string
  statut: 'en_cours' | 'termine' | 'echoue'
  progression: number
  message: string | null
  resultat: Record<string, unknown> | null
}

export interface Reglages {
  /** En POINTS. */
  plafond_quotidien: number
  retention_normale: number
  retention_test: number
  jours_phase_finale: number
  buffer_jours_avant_test: number
  entretien_quota_hebdo: number
  entretien_plafond_jour: number
  entretien_poids_max: number
  poids_type: Record<string, number>
  poids_categorie: Record<string, number>
  modele: string
  ordre_session: 'melange' | 'par_cours'
  mock_llm: boolean
  juge_etat: EtatJuge
  juge_modele: string
  profils: string[]
}

/** Consommation du crédit API, plafonds et état de l'interrupteur d'urgence. */
export interface Budget {
  cout_jour_usd: number
  cout_mois_usd: number
  appels_jour: number
  appels_mois: number
  plafond_jour_usd: number
  plafond_mois_usd: number
  plafond_operation_usd: number
  llm_desactive: boolean
  /** Coupé par LLM_DISABLED=1 côté serveur: l'interface ne peut pas le lever. */
  arret_par_env: boolean
  /** Faux = mode simulé, aucune dépense possible. */
  mode_reel: boolean
  disjoncteur_ouvert: boolean
  alertes: string[]
  appels_evites: number
  refus: number
}

export interface Estimation {
  operation: string
  modele: string
  tokens_entree: number
  max_tokens_sortie: number
  cout_estime_usd: number
}

export class ErreurApi extends Error {
  statut: number

  constructor(message: string, statut: number) {
    super(message)
    this.statut = statut
  }
}

async function requete<T>(
  chemin: string,
  options: RequestInit = {},
): Promise<T> {
  const reponse = await fetch(chemin, {
    credentials: 'include',
    headers:
      options.body instanceof FormData
        ? undefined
        : { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!reponse.ok) {
    let detail = reponse.statusText
    try {
      const corps = await reponse.json()
      detail = corps.detail ?? detail
      if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join(', ')
    } catch {
      /* corps non JSON: on garde statusText */
    }
    throw new ErreurApi(String(detail), reponse.status)
  }

  if (reponse.status === 204) return undefined as T
  return reponse.json()
}

const parametres = (p: Record<string, string | undefined>) => {
  const q = new URLSafeParams(p)
  return q.toString() ? `?${q}` : ''
}

class URLSafeParams extends URLSearchParams {
  constructor(p: Record<string, string | undefined>) {
    super()
    for (const [cle, valeur] of Object.entries(p)) {
      if (valeur !== undefined && valeur !== '') this.append(cle, valeur)
    }
  }
}

export type Role = 'admin' | 'standard' | 'etendu'

export interface Compte {
  identifiant: string
  role: Role
  cree_le: string
}

export interface Depot {
  id: number
  cours: string
  nom: string
  taille: number
  depose_par: string
  depose_le: string
  traite_le: string | null
}

export const api = {
  // Auth
  connexion: (identifiant: string, mot_de_passe: string) =>
    requete<{ identifiant: string; role: Role }>('/api/login', {
      method: 'POST',
      body: JSON.stringify({ identifiant, mot_de_passe }),
    }),
  deconnexion: () => requete<void>('/api/logout', { method: 'POST' }),
  moi: () => requete<{ identifiant: string; role: Role }>('/api/me'),

  // Comptes (administrateur)
  comptes: () => requete<Compte[]>('/api/comptes'),
  creerCompte: (identifiant: string, mot_de_passe: string, role: Role) =>
    requete<Compte>('/api/comptes', {
      method: 'POST',
      body: JSON.stringify({ identifiant, mot_de_passe, role }),
    }),
  modifierCompte: (identifiant: string, data: { role?: Role; mot_de_passe?: string }) =>
    requete<void>(`/api/comptes/${encodeURIComponent(identifiant)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  supprimerCompte: (identifiant: string) =>
    requete<void>(`/api/comptes/${encodeURIComponent(identifiant)}`, { method: 'DELETE' }),

  // Dépôt de fichiers (compte étendu, administrateur)
  depots: () => requete<Depot[]>('/api/depot'),
  deposer: (cours: string, fichier: File) => {
    const form = new FormData()
    form.append('cours', cours)
    form.append('fichier', fichier)
    return requete<Depot>('/api/depot', { method: 'POST', body: form })
  },
  supprimerDepot: (id: number) => requete<void>(`/api/depot/${id}`, { method: 'DELETE' }),
  urlDepot: (id: number) => `/api/depot/${id}/fichier`,
  changerMdp: (ancien: string, nouveau: string) =>
    requete<void>('/api/mot-de-passe', {
      method: 'POST',
      body: JSON.stringify({ ancien, nouveau }),
    }),

  // Matières
  matieres: () => requete<Matiere[]>('/api/matieres'),
  creerMatiere: (nom: string, couleur: string) =>
    requete<Matiere>('/api/matieres', {
      method: 'POST',
      body: JSON.stringify({ nom, couleur }),
    }),
  modifierMatiere: (
    nom: string,
    data: { nouveau_nom?: string; couleur?: string; profil?: string },
  ) =>
    requete<void>(`/api/matieres/${encodeURIComponent(nom)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  supprimerMatiere: (nom: string) =>
    requete<{ cours_detaches: number }>(
      `/api/matieres/${encodeURIComponent(nom)}`,
      { method: 'DELETE' },
    ),

  // Cours (thèmes)
  cours: () => requete<Cours[]>('/api/cours'),
  creerCours: (nom: string, couleur?: string, matiere?: string | null) =>
    requete<Cours>('/api/cours', {
      method: 'POST',
      body: JSON.stringify({ nom, couleur, matiere: matiere || null }),
    }),
  modifierCours: (
    nom: string,
    data: {
      nouveau_nom?: string
      couleur?: string
      /** `null` détache le thème de sa matière. */
      matiere?: string | null
      profil?: string
      recap?: string
      archive?: boolean
    },
  ) =>
    requete<void>(`/api/cours/${encodeURIComponent(nom)}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  supprimerCours: (nom: string, reaffecter_vers?: string) =>
    requete<{ questions_concernees: number }>(
      `/api/cours/${encodeURIComponent(nom)}`,
      { method: 'DELETE', body: JSON.stringify({ reaffecter_vers }) },
    ),

  // Tests
  tests: () => requete<Test[]>('/api/tests'),
  creerTest: (data: Omit<Test, 'id'>) =>
    requete<Test>('/api/tests', { method: 'POST', body: JSON.stringify(data) }),
  deplacerTest: (id: number, date: string) =>
    requete<void>(`/api/tests/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ date }),
    }),
  /** Remplace les thèmes et matières liés à un test. */
  modifierLiensTest: (id: number, cours: string[], matieres: string[]) =>
    requete<void>(`/api/tests/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ cours, matieres }),
    }),
  supprimerTest: (id: number) =>
    requete<void>(`/api/tests/${id}`, { method: 'DELETE' }),

  // Aujourd'hui / session
  today: (jour?: string, cours?: string) =>
    requete<Today>(`/api/today${parametres({ jour, cours })}`),
  session: (p: {
    jour?: string
    cours?: string
    ordre?: string
    session_id?: string
  }) => requete<Queue>(`/api/session${parametres(p)}`),
  repondre: (data: {
    card_id: string
    rating: 1 | 2 | 3 | 4
    correct?: boolean | null
    source?: 'auto' | 'manuel' | 'juge'
    duration_ms?: number
    jour?: string
  }) =>
    requete<void>('/api/reponse', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  juger: (data: { card_id: string; reponse: string; langue: 'fr' | 'en' }) =>
    requete<Jugement>('/api/juger', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  passer: (data: {
    card_id: string
    session_id: string
    raison?: string | null
    jour?: string
  }) =>
    requete<{ nb_skips: number; reviendra: boolean }>('/api/skip', {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  // Stats / bibliothèque
  stats: (jour?: string, cours?: string) =>
    requete<Stats>(`/api/stats${parametres({ jour, cours })}`),
  cartes: (p: {
    cours?: string
    recherche?: string
    desactivees?: string
  }) => requete<Carte[]>(`/api/cartes${parametres(p)}`),
  modifierCarte: (
    id: string,
    data: Partial<{
      payload: Payload
      desactivee: boolean
      note_qualite: string | null
      cours: string
    }>,
  ) =>
    requete<void>(`/api/cartes/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  supprimerCarte: (id: string) =>
    requete<void>(`/api/cartes/${id}`, { method: 'DELETE' }),

  // Ingestion
  ingestTexte: (cours: string, texte: string) =>
    requete<{ job_id: string }>('/api/ingest/texte', {
      method: 'POST',
      body: JSON.stringify({ cours, texte }),
    }),
  ingestFichiers: (
    cours: string,
    voie: 'pdf' | 'image',
    fichiers: File[],
  ) => {
    const form = new FormData()
    form.append('cours', cours)
    form.append('voie', voie)
    fichiers.forEach((f) => form.append('fichiers', f))
    return requete<{ job_id: string }>('/api/ingest/fichiers', {
      method: 'POST',
      body: form,
    })
  },
  genererQuestions: (data: {
    cours: string
    source: string
    concepts: ConceptExtrait[]
    extraction_job_id?: string
  }) =>
    requete<{ job_id: string }>('/api/ingest/generer', {
      method: 'POST',
      body: JSON.stringify(data),
    }),
  job: (id: string) => requete<Job>(`/api/jobs/${id}`),

  // Import de questions déjà générées (aucun LLM). Le serveur ne garde rien
  // entre l'aperçu et l'import: les mêmes fichiers sont renvoyés.
  importApercu: (
    cours: string,
    fichier: File,
    concepts: File | null,
  ) =>
    requete<ApercuImport>('/api/import/apercu', {
      method: 'POST',
      body: formulaireImport(cours, fichier, concepts),
    }),
  importConfirmer: (
    cours: string,
    fichier: File,
    concepts: File | null,
  ) =>
    requete<ResultatImport>('/api/import/confirmer', {
      method: 'POST',
      body: formulaireImport(cours, fichier, concepts),
    }),

  // Réglages
  reglages: () => requete<Reglages>('/api/reglages'),
  modifierReglages: (data: Partial<Reglages>) =>
    requete<void>('/api/reglages', {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),
  export: () => requete<Record<string, unknown>>('/api/export'),

  // Crédit API
  budget: () => requete<Budget>('/api/budget'),
  arretLLM: (coupe: boolean) =>
    requete<{ llm_desactive: boolean; arret_par_env: boolean }>(
      '/api/budget/arret',
      { method: 'POST', body: JSON.stringify({ coupe }) },
    ),
  /** Coût estimé d'une extraction par texte. N'appelle pas le modèle. */
  estimerTexte: (cours: string, texte: string) =>
    requete<Estimation>('/api/ingest/estimer', {
      method: 'POST',
      body: JSON.stringify({ cours, texte }),
    }),
}

function formulaireImport(
  cours: string,
  fichier: File,
  concepts: File | null,
): FormData {
  const form = new FormData()
  form.append('cours', cours)
  form.append('fichier', fichier)
  if (concepts) form.append('concepts', concepts)
  return form
}

/** Interroge une tâche de fond jusqu'à ce qu'elle finisse. */
export async function suivreJob(
  id: string,
  onProgression?: (job: Job) => void,
  intervalleMs = 400,
): Promise<Job> {
  for (;;) {
    const job = await api.job(id)
    onProgression?.(job)
    if (job.statut !== 'en_cours') return job
    await new Promise((r) => setTimeout(r, intervalleMs))
  }
}
