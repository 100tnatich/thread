import { type Browser, expect, test, type Page } from '@playwright/test'

/**
 * Parcours complet en mode simulé: connexion → ajout de matière par texte →
 * validation des concepts → session avec un skip et une notation.
 */

const IDENTIFIANT = 'e2e'
const MOT_DE_PASSE = 'motdepasse-e2e'

async function connexion(page: Page) {
  await page.goto('/')
  await page.getByLabel('Identifiant').fill(IDENTIFIANT)
  await page.getByLabel('Mot de passe').fill(MOT_DE_PASSE)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page.getByRole('heading', { name: "Aujourd'hui" })).toBeVisible()
}

async function creerCours(page: Page, nom: string) {
  await page.goto('/cours')
  await page.getByPlaceholder('Ex. Anglais conditionnel').fill(nom)
  await page.getByRole('button', { name: 'Créer le thème' }).click()
  await expect(page.getByText(nom, { exact: true })).toBeVisible()
}

async function ajouterMatiere(page: Page, cours: string) {
  await page.goto('/ajouter')
  await page.getByLabel('Thème', { exact: true }).selectOption(cours)
  await page
    .getByPlaceholder('Colle ou écris ici ce sur quoi tu veux être interrogé.')
    .fill(
      'La tension electrique se mesure en volts. Le courant electrique se ' +
        'mesure en amperes. La resistance se mesure en ohms.',
    )
  await page.getByRole('button', { name: 'Extraire les concepts' }).click()

  // Étape de validation humaine: elle doit apparaître AVANT toute génération.
  await expect(page.getByText('Relis les concepts')).toBeVisible({ timeout: 20_000 })
}

test('le login est exigé avant toute page', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Se connecter' })).toBeVisible()
  await expect(page.getByRole('heading', { name: "Aujourd'hui" })).toBeHidden()
})

test('parcours complet: ajout par texte, validation, session avec skip et notation', async ({
  page,
}) => {
  await connexion(page)
  await creerCours(page, 'physique')
  await ajouterMatiere(page, 'physique')

  // --- Validation des concepts: on en décoche un, il ne doit pas produire
  // de question (la génération part uniquement de la liste validée).
  // Uniquement les cases « Garder » — chaque concept en a d'autres
  // (catégorie, importance, « se teste par un exercice »).
  const cases = page.locator('input[aria-label^="Garder"]')
  const nbConcepts = await cases.count()
  expect(nbConcepts).toBeGreaterThan(1)
  await cases.first().uncheck()

  const bouton = page.getByRole('button', { name: /Valider et générer/ })
  await expect(bouton).toContainText(String(nbConcepts - 1))
  await bouton.click()

  await expect(page.getByText(/questions? créée/)).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('COUVERTURE')).toBeVisible()

  // --- Récap théorique du thème (posé ici par l'API, comme le fait l'éditeur de la page Cours)
  const patch = await page.request.patch('/api/cours/physique', {
    data: { recap: '## Rappel\n- **U = R · I**\n> Les unités font partie de la réponse.' },
  })
  expect(patch.ok()).toBeTruthy()

  // --- Session
  await page.goto('/')
  await expect(page.getByText(/questions? à réviser/)).toBeVisible()
  await page.getByRole('button', { name: 'Par cours' }).click()
  await page.getByRole('button', { name: 'Commencer' }).click()

  // En mode « par cours », le récap du thème passe avant la première question.
  await expect(page.getByText('Avant de commencer: rappel théorique')).toBeVisible()
  await expect(page.getByText('Les unités font partie de la réponse.')).toBeVisible()
  await page.getByRole('button', { name: 'Passer' }).click()
  await expect(page.getByText('Avant de commencer: rappel théorique')).toBeHidden()

  const compteur = page.locator('header').getByText(/^\d+\/\d+$/)
  await expect(compteur).toBeVisible()
  const total = Number((await compteur.innerText()).split('/')[1])
  expect(total).toBeGreaterThan(1)

  // --- Skip: la question passée repart en fin de file, sans pénalité.
  await page.getByRole('button', { name: 'Passer' }).click()
  await expect(page.getByText(/Pourquoi passer/)).toBeVisible()
  await page.getByRole('button', { name: 'Il me faut une feuille' }).click()

  // Le total ne change pas: la question n'est pas perdue, elle est déplacée
  // en fin de file. On reste donc sur la position 1.
  await expect(compteur).toHaveText(`1/${total}`)

  // --- Notation: la file avance d'un cran (le total, lui, ne bouge pas).
  await revelerEtNoter(page)
  await expect(compteur).toHaveText(`2/${total}`)

  // Et la réponse a bien été enregistrée côté moteur.
  const cartes = await page.request.get('/api/cartes')
  const revisions = (await cartes.json()).reduce(
    (n: number, c: { nb_revisions: number }) => n + c.nb_revisions,
    0,
  )
  expect(revisions).toBe(1)

  // Le skip n'a pas fait revenir le récap (déjà lu dans cette session), et en
  // mode mélangé il n'apparaît pas du tout.
  await expect(page.getByText('Avant de commencer: rappel théorique')).toBeHidden()
  await page.goto('/')
  await page.getByRole('button', { name: 'Mélangé' }).click()
  await page.getByRole('button', { name: 'Commencer' }).click()
  await expect(compteur).toBeVisible()
  await expect(page.getByText('Avant de commencer: rappel théorique')).toBeHidden()

  // La base E2E est partagée avec les tests suivants: on retire le récap.
  expect((await page.request.patch('/api/cours/physique', { data: { recap: '' } })).ok()).toBeTruthy()
})

/**
 * Révèle la question courante puis met une note, quel que soit son type.
 *
 * On attend d'abord le bouton de révélation: sa présence garantit que la
 * question est rendue. Compter les champs avant cette attente était instable
 * — le compte pouvait être pris entre deux rendus de React.
 */
async function revelerEtNoter(page: Page) {
  const reveler = page.getByRole('button', {
    name: /^(Valider$|Voir la solution|Voir la réponse attendue|J'ai rédigé)/,
  })
  await reveler.waitFor({ state: 'visible' })

  // exact / cloze / libre_llm: remplir les champs de saisie visibles.
  const champs = await page
    .locator('main input[type="text"], main input:not([type]), main textarea')
    .all()
  for (const champ of champs) {
    if (await champ.isVisible()) await champ.fill('x')
  }

  // qcm: aucun champ, il faut sélectionner une proposition.
  if (champs.length === 0) {
    const propositions = page.locator('main button[aria-pressed]')
    if ((await propositions.count()) > 0) await propositions.first().click()
  }

  await reveler.click()

  // Note: boutons Raté/Difficile/Bien/Facile (data-note), "Continuer" si la
  // réponse était fausse, ou la validation de l'auto-évaluation d'une
  // rédaction dont tous les critères ne sont pas cochés.
  const notes = page.locator('[data-note]')
  if ((await notes.count()) > 0) {
    await notes.last().click()
    return
  }
  await page
    .getByRole('button', { name: /^(Continuer|Valider mon auto-évaluation)/ })
    .first()
    .click()
}

test('les réglages modifient la file du jour', async ({ page }) => {
  await connexion(page)
  await creerCours(page, 'maths')
  await ajouterMatiere(page, 'maths')
  await page.getByRole('button', { name: /Valider et générer/ }).click()
  await expect(page.getByText(/questions? créée/)).toBeVisible({ timeout: 20_000 })

  await page.goto('/reglages')
  const plafond = page.getByLabel('Plafond quotidien')
  await plafond.fill('1')
  await plafond.blur()
  await expect(page.getByText('Enregistré.')).toBeVisible()

  await page.goto('/')
  // Plafond en points: 1 point pèse au plus une ou deux petites questions.
  await expect(page.getByText(/plafond 1 point\b/)).toBeVisible()
  await expect(page.getByText(/environ (0,5|1) point/)).toBeVisible()

  // Remet le plafond par défaut: la base E2E est partagée entre les tests.
  await page.goto('/reglages')
  await plafond.fill('40')
  await plafond.blur()
  await expect(page.getByText('Enregistré.')).toBeVisible()
})

test('import de questions déjà générées: aperçu, erreurs, puis import (sans LLM)', async ({
  page,
}) => {
  await connexion(page)
  await creerCours(page, 'informatique')

  const question = (concept: string, enonce: string) => ({
    concept_id: concept,
    type: 'exact',
    enonce,
    reponse_exacte: 'chmod',
    source_ref: 'page 2',
    confiance: 'haute',
  })
  const fichier = (questions: unknown[]) => ({
    name: 'ch1_questions.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      JSON.stringify({
        source: 'cours.pdf',
        cours: 'informatique',
        questions,
        avertissements: [],
      }),
    ),
  })

  await page.goto('/ajouter')
  await page.getByLabel('Thème', { exact: true }).selectOption('informatique')
  await page.getByRole('button', { name: 'Importer', exact: true }).click()
  const champFichier = page.locator('input[type="file"]').first()

  // 1. Fichier invalide: le rapport nomme la question fautive, rien à importer.
  await champFichier.setInputFiles(
    fichier([{ ...question('c1', 'Quelle commande change les droits ?'), type: 'qcm' }]),
  )
  await page.getByRole('button', { name: 'Vérifier le fichier' }).click()
  await expect(page.getByText('Fichier invalide')).toBeVisible()
  await expect(page.getByText(/Question n°1 \(c1\): champ manquant: propositions/)).toBeVisible()
  await expect(page.getByRole('button', { name: /^Importer \d+ question/ })).toBeDisabled()

  // 2. Fichier valide: aperçu, puis import.
  await champFichier.setInputFiles(
    fichier([
      question('c1', 'Quelle commande change les droits ?'),
      question('c2', 'Quelle commande change le propriétaire ?'),
    ]),
  )
  await page.getByRole('button', { name: 'Vérifier le fichier' }).click()
  await expect(page.getByText('Fichier valide')).toBeVisible()
  await expect(page.getByText('2 à importer')).toBeVisible()
  await page.getByRole('button', { name: 'Importer 2 questions' }).click()
  await expect(page.getByText('2 questions importées')).toBeVisible()

  // 3. Le même fichier une seconde fois: tout est déjà en base.
  await page.getByRole('button', { name: 'Importer un autre fichier' }).click()
  await champFichier.setInputFiles(
    fichier([question('c1', 'Quelle commande change les droits ?')]),
  )
  await page.getByRole('button', { name: 'Vérifier le fichier' }).click()
  await expect(page.getByText('1 déjà en base')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Rien de nouveau à importer' })).toBeDisabled()
})

test('matières et thèmes: un test lié à toute une matière sort ses thèmes de l’entretien, archivage, création rapide', async ({
  page,
}) => {
  await connexion(page)
  await page.goto('/cours')

  // Une matière, puis deux thèmes dedans.
  await page.getByPlaceholder('Ex. Anglais', { exact: true }).fill('Anglais')
  await page.getByRole('button', { name: 'Créer la matière' }).click()
  await expect(page.getByRole('heading', { name: 'Anglais' })).toBeVisible()
  for (const theme of ['Anglais conditionnel', 'English present perfect']) {
    await page.getByPlaceholder('Ex. Anglais conditionnel').fill(theme)
    await page.getByLabel('Matière du nouveau thème').selectOption('Anglais')
    await page.getByRole('button', { name: 'Créer le thème' }).click()
    await expect(page.getByText(theme, { exact: true })).toBeVisible()
  }
  // Aucun test à venir: les deux thèmes sont en entretien.
  await expect(page.getByText('2/2 en entretien')).toBeVisible()

  // Un test lié à TOUTE la matière: ses thèmes repassent en mode test.
  await page.goto('/tests')
  await page.getByRole('button', { name: '+ Ajouter' }).click()
  await page.getByRole('button', { name: 'Toute la matière', exact: true }).click()
  const dans30Jours = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10)
  await page.locator('input[type="date"]').first().fill(dans30Jours)
  await page.getByPlaceholder('Ex. TE conditionnel').fill('Examen de fin d’année')
  await page.getByRole('button', { name: 'Ajouter le test' }).click()
  await expect(page.getByText(/Toute la matière Anglais/)).toBeVisible()

  await page.goto('/cours')
  await expect(page.getByText('0/2 en entretien')).toBeVisible()

  // Archivage réversible d'un thème.
  await page.getByRole('button', { name: 'Archiver' }).first().click()
  await expect(page.getByText('Afficher les thèmes archivés (1)')).toBeVisible()
  await expect(page.getByText('1/1 en entretien').or(page.getByText('0/1 en entretien'))).toBeVisible()

  // « Ajouter »: matière puis thème, et création rapide d'un thème.
  await page.goto('/ajouter')
  await page.getByLabel('Matière', { exact: true }).selectOption('Anglais')
  await expect(page.getByLabel('Thème', { exact: true }).locator('option')).toHaveCount(1)
  await page.getByRole('button', { name: /\+ Nouveau thème dans Anglais/ }).click()
  await page.getByLabel('Nom du nouveau thème').fill('English vocabulary')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()
  await expect(page.getByLabel('Thème', { exact: true })).toHaveValue('English vocabulary')
})

test('réglages en points: plafond, poids par type, entretien', async ({ page }) => {
  await connexion(page)
  await page.goto('/reglages')
  await expect(page.getByLabel('Plafond quotidien')).toHaveValue('40')
  await expect(page.getByLabel('Poids Problème (calcul)')).toHaveValue('2')
  await expect(page.getByLabel('Poids Rédaction')).toHaveValue('4')
  await expect(page.getByLabel('Poids Vocabulaire (prime sur le type)')).toHaveValue('0.1')
  await expect(page.getByLabel('Quota hebdomadaire par thème (points)')).toHaveValue('3')
  await expect(page.getByLabel('Plafond d’entretien par jour (points)')).toHaveValue('8')

  const qcm = page.getByLabel('Poids QCM')
  await qcm.fill('0.75')
  await qcm.blur()
  await expect(page.getByText('Enregistré.')).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Poids QCM')).toHaveValue('0.75')
})

test('le bouton FR/EN traduit l\'interface et la langue est mémorisée', async ({ page }) => {
  await connexion(page)

  await page.getByRole('button', { name: 'EN', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: 'Today' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Library' }).first()).toBeVisible()

  // Mémorisée: toujours en anglais après rechargement, sur une autre page.
  await page.goto('/reglages')
  await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
  await expect(page.getByText('Daily cap (in points)')).toBeVisible()

  await page.getByRole('button', { name: 'FR', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: 'Réglages' })).toBeVisible()
})

test('bibliothèque: le filtre par type partitionne les questions', async ({ page }) => {
  await connexion(page)
  await page.goto('/bibliotheque')

  const compter = async () => {
    const vide = page.getByText('Aucune question ne correspond.')
    const total = page.getByText(/^\d+ questions?$/)
    await expect(vide.or(total)).toBeVisible()
    return (await vide.isVisible()) ? 0 : Number((await total.innerText()).split(' ')[0])
  }

  const total = await compter()
  expect(total).toBeGreaterThan(0)

  const select = page.getByLabel('Type de question')
  const types = (await select.locator('option').evaluateAll((o) =>
    o.map((x) => (x as HTMLOptionElement).value),
  )).filter(Boolean)
  expect(types.length).toBe(6)

  // Chaque question a exactement un type: la somme des filtres = le total.
  let somme = 0
  for (const type of types) {
    await select.selectOption(type)
    somme += await compter()
  }
  expect(somme).toBe(total)

  await select.selectOption('')
  expect(await compter()).toBe(total)
})

// --- Correction par IA (réponses libres et rédactions) ----------------------
// Le serveur est simulé: ces tests interceptent /api/session, /api/juger et
// /api/reponse pour exercer l'interface seule — aucun appel payant, jamais.

function file(juge: 'reel' | 'simule' | 'indisponible', questions: unknown[]) {
  return {
    jour: '2026-01-05', session_id: 's1', total: questions.length,
    total_dues: questions.length, plafond: 40, points: questions.length,
    points_entretien: 0, juge, questions,
  }
}

const base = {
  type: 'x', cours: 'english', couleur_cours: '#8B7CFF',
  jours_retard: 0, nouvelle: true, poids: 1, entretien: false,
}
const libre = (id: string) => ({
  ...base, card_id: id, type: 'libre_llm',
  payload: {
    type: 'libre_llm', enonce: `Quand emploie-t-on le zero conditional ? (${id})`,
    reponse_attendue: 'Pour ce qui est généralement vrai.',
  },
})
const redaction = {
  ...base, card_id: 'r1', type: 'redaction',
  payload: {
    type: 'redaction', consigne: 'Écris une introduction.', contraintes: '100 mots',
    criteres: ['Une amorce', 'Une problématique', 'Un plan annoncé'],
    exemple_reference: 'Modèle.',
  },
}

test('correction par IA: verdict, note suggérée, source « juge » — puis repli et rédaction', async ({
  page,
}) => {
  await connexion(page)
  const envoyees: Record<string, unknown>[] = []
  const demandees: Record<string, unknown>[] = []

  await page.route('**/api/session**', (r) =>
    r.fulfill({ json: file('reel', [libre('l1'), libre('l2'), redaction]) }),
  )
  await page.route('**/api/reponse', (r) => {
    envoyees.push(r.request().postDataJSON())
    return r.fulfill({ json: { ok: true } })
  })
  await page.route('**/api/juger', (r) => {
    const demande = r.request().postDataJSON()
    demandees.push(demande)
    if (demande.card_id === 'l1')
      return r.fulfill({ json: {
        disponible: true, etat: 'reel', note_suggeree: 2, verdict: 'partiel',
        commentaire: 'Il manque la condition.', criteres: null, raison: null,
      } })
    if (demande.card_id === 'l2')
      return r.fulfill({ json: {
        disponible: false, etat: 'reel', note_suggeree: null, verdict: null,
        commentaire: null, criteres: null,
        raison: 'Plafond journalier atteint.',
      } })
    return r.fulfill({ json: {
      disponible: true, etat: 'reel', note_suggeree: 2, verdict: null,
      commentaire: 'Il manque le plan.', criteres: [true, true, false], raison: null,
    } })
  })

  await page.goto('/session')

  // 1. Réponse libre corrigée: verdict + commentaire, note suggérée mise en avant.
  const boutonIA = page.getByRole('button', { name: "Corriger avec l'IA" })
  // Absent tant que rien n'est écrit: pas de correction (donc pas de coût) sur du vide.
  await expect(boutonIA).toHaveCount(0)
  await page.locator('textarea').fill('Pour une vérité générale.')
  await boutonIA.click()
  await expect(page.getByText('Partiellement juste')).toBeVisible()
  await expect(page.getByText('Il manque la condition.')).toBeVisible()
  expect(demandees[0]).toMatchObject({ card_id: 'l1', reponse: 'Pour une vérité générale.', langue: 'fr' })
  await expect(page.getByRole('button', { name: /Difficile.*suggérée/ })).toBeVisible()
  await page.getByRole('button', { name: /^Difficile/ }).click()
  expect(envoyees[0]).toMatchObject({ card_id: 'l1', rating: 2, source: 'juge', correct: null })

  // 2. Juge indisponible (plafond): on le dit, on retombe sur l'auto-évaluation.
  await page.locator('textarea').fill('Une réponse.')
  await boutonIA.click()
  await expect(page.getByText(/Correction par IA indisponible : Plafond journalier atteint\./)).toBeVisible()
  await expect(page.getByRole('button', { name: /suggérée/ })).toHaveCount(0)
  await page.getByRole('button', { name: /^Bien/ }).click()
  expect(envoyees[1]).toMatchObject({ card_id: 'l2', rating: 3, source: 'manuel' })

  // 3. Rédaction: trop court = pas de correction (rien à payer); assez long = critères
  // cochés par le juge, ajustables; note inchangée = source « juge ».
  await page.locator('textarea').fill('Trop court')
  await expect(boutonIA).toHaveCount(0)
  await page.locator('textarea').fill('Une introduction suffisamment longue pour être corrigée.')
  await boutonIA.click()
  await expect(page.getByText('Il manque le plan.')).toBeVisible()
  await expect(page.locator('input[type="checkbox"]:checked')).toHaveCount(2)
  await page.getByRole('button', { name: /Valider mon auto-évaluation \(2\/3\)/ }).click()
  expect(envoyees[2]).toMatchObject({ card_id: 'r1', rating: 2, source: 'juge' })
  await expect(page.getByText('Session terminée')).toBeVisible()
})

test('juge inactif: pas de bouton IA, auto-évaluation seule (comportement d\'avant)', async ({
  page,
}) => {
  await connexion(page)
  await page.route('**/api/session**', (r) =>
    r.fulfill({ json: file('indisponible', [libre('l1'), redaction]) }),
  )
  await page.route('**/api/juger', () => {
    throw new Error('aucune correction ne doit être demandée quand le juge est inactif')
  })
  await page.goto('/session')
  await page.locator('textarea').fill('Une réponse.')
  await expect(page.getByRole('button', { name: "Corriger avec l'IA" })).toHaveCount(0)
  await page.getByRole('button', { name: 'Voir la réponse attendue' }).click()
  await expect(page.getByRole('button', { name: /suggérée/ })).toHaveCount(0)
})

test('mode par cours: un menu permet de ne réviser qu\'un cours', async ({ page }) => {
  await connexion(page)
  for (const nom of ['chimie', 'optique']) {
    await creerCours(page, nom)
    await ajouterMatiere(page, nom)
    await page.getByRole('button', { name: /Valider et générer/ }).click()
    await expect(page.getByText(/questions? créée/)).toBeVisible({ timeout: 20_000 })
  }

  await page.goto('/')
  // Le menu n'existe qu'en mode « par cours ».
  await expect(page.getByLabel('Cours à réviser')).toBeHidden()
  await page.getByRole('button', { name: 'Par cours' }).click()
  const menu = page.getByLabel('Cours à réviser')
  await expect(menu).toBeVisible()
  await menu.selectOption('optique')
  await page.getByRole('link', { name: 'Commencer' }).click()

  await expect(page).toHaveURL(/cours=optique/)
  // Aucune question de l'autre cours: son nom n'apparaît jamais dans la session.
  await expect(page.getByText('optique', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('chimie', { exact: true })).toHaveCount(0)
})

// --- Rôles et dépôt ---------------------------------------------------------

async function connexionComme(browser: Browser, identifiant: string, mdp: string) {
  const contexte = await browser.newContext({ baseURL: 'http://127.0.0.1:5174' })
  const page = await contexte.newPage()
  await page.goto('/')
  await page.getByLabel('Identifiant').fill(identifiant)
  await page.getByLabel('Mot de passe').fill(mdp)
  await page.getByRole('button', { name: 'Se connecter' }).click()
  await expect(page.getByRole('heading', { name: "Aujourd'hui" })).toBeVisible()
  return page
}

test('rôles: l\'admin crée les comptes, chaque rôle ne voit que ses pages', async ({
  page,
  browser,
}) => {
  await connexion(page)
  // L'admin voit les deux pages en plus.
  const rail = page.getByRole('navigation').last()
  await expect(rail.getByRole('link', { name: 'Dépôt' })).toBeVisible()
  await expect(rail.getByRole('link', { name: 'Comptes' })).toBeVisible()
  // Le personnage du compte connecté: rouge = admin.
  await expect(page.getByRole('img', { name: /^e2e · Administrateur/ })).toHaveAttribute('data-role', 'admin')

  await page.getByRole('link', { name: 'Comptes' }).click()
  await page.getByLabel('Identifiant', { exact: true }).fill('eleve')
  await page.getByPlaceholder('12 caractères minimum').fill('motdepasse-eleve')
  await page.getByRole('button', { name: 'Créer le compte' }).click()
  await expect(page.getByText('Compte créé.')).toBeVisible()
  await page.getByLabel('Identifiant', { exact: true }).fill('ecole')
  await page.getByPlaceholder('12 caractères minimum').fill('motdepasse-ecole')
  await page.getByLabel('Rôle du nouveau compte').selectOption('etendu')
  await page.getByRole('button', { name: 'Créer le compte' }).click()
  await expect(page.getByLabel('Rôle de ecole')).toHaveValue('etendu')

  // Standard: l'appli telle quelle, ni Dépôt ni Comptes (et le serveur refuse).
  const standard = await connexionComme(browser, 'eleve', 'motdepasse-eleve')
  const navStandard = standard.getByRole('navigation').last()
  await expect(standard.getByRole('img', { name: /^eleve · Standard/ })).toHaveAttribute('data-role', 'standard')
  await expect(navStandard.getByRole('link', { name: 'Tableau' })).toBeVisible()
  await expect(navStandard.getByRole('link', { name: 'Dépôt' })).toHaveCount(0)
  await expect(navStandard.getByRole('link', { name: 'Comptes' })).toHaveCount(0)
  expect((await standard.request.get('/api/depot')).status()).toBe(403)
  expect((await standard.request.get('/api/comptes')).status()).toBe(403)
  // Pas de réglage du modèle LLM pour un standard.
  await standard.goto('/reglages')
  await expect(standard.getByText('Modèle LLM')).toHaveCount(0)
  await standard.goto('/depot')
  await expect(standard).toHaveURL(/\/$/) // redirigé vers l'accueil

  // Étendu: la page Dépôt, pas Comptes. Il crée un cours puis y dépose un fichier.
  const etendu = await connexionComme(browser, 'ecole', 'motdepasse-ecole')
  const navEtendu = etendu.getByRole('navigation').last()
  await expect(etendu.getByRole('img', { name: /^ecole · Étendu/ })).toHaveAttribute('data-role', 'etendu')
  await expect(navEtendu.getByRole('link', { name: 'Comptes' })).toHaveCount(0)
  await navEtendu.getByRole('link', { name: 'Dépôt' }).click()
  await etendu.getByLabel('Nom du cours').fill('histoire')
  await etendu.getByRole('button', { name: 'Créer', exact: true }).click()
  await expect(etendu.getByLabel('Cours du dépôt')).toHaveValue('histoire')

  await etendu.getByLabel('Fichiers à déposer').setInputFiles([
    { name: 'chapitre1.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\ncontenu') },
    { name: 'faux.pdf', mimeType: 'application/pdf', buffer: Buffer.from('pas un pdf') },
  ])
  await etendu.getByRole('button', { name: 'Déposer', exact: true }).click()
  await expect(etendu.getByText('✓ chapitre1.pdf')).toBeVisible()
  await expect(etendu.getByText('✕ faux.pdf')).toBeVisible() // refusé, l'autre est passé
  await expect(etendu.getByRole('link', { name: 'chapitre1.pdf' })).toBeVisible()
  await expect(etendu.getByText('À traiter').first()).toBeVisible()

  // L'admin voit le même dépôt (données partagées).
  expect(((await page.request.get('/api/depot')).ok())).toBeTruthy()
})
