import { defineConfig, devices } from '@playwright/test'

/**
 * E2E: lance un backend en mode simulé (MOCK_LLM=1) sur une base jetable,
 * plus le frontend Vite. Aucun appel LLM réel.
 */
const PORT_API = 8001
const PORT_WEB = 5174
const DB = 'e2e.db'
// `uv` est installé dans ~/.local/bin, absent du PATH des sous-processus.
const UV = process.env.UV_BIN ?? `${process.env.HOME}/.local/bin/uv`

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT_WEB}`,
    trace: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: [
    {
      // `sh -c` pour enchaîner: base propre, compte, puis serveur.
      command:
        `sh -c "rm -f ${DB} && rm -rf depot-e2e && ` +
        `${UV} run flashcards web create-user --identifiant e2e --mot-de-passe motdepasse-e2e && ` +
        `${UV} run flashcards web serve --port ${PORT_API}"`,
      cwd: '..',
      port: PORT_API,
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      env: {
        MOCK_LLM: '1',
        FLASHCARDS_ENV: 'development',
        SESSION_SECRET: 'secret-e2e',
        FLASHCARDS_DB: DB,
        FLASHCARDS_UPLOADS: 'uploads-e2e',
        FLASHCARDS_DEPOT: 'depot-e2e',
      },
    },
    {
      command: `npm run dev -- --port ${PORT_WEB} --strictPort`,
      port: PORT_WEB,
      reuseExistingServer: false,
      stdout: 'pipe',
      stderr: 'pipe',
      env: { VITE_API_PORT: String(PORT_API) },
    },
  ],
})
