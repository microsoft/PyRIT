import process from 'node:process'
import { defineConfig } from '@playwright/test'
import analytics from './playwright.analytics.config'

const port = Number(process.env.PYRIT_E2E_PAIRED_PORT ?? '18497')
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid paired outcomes test port')
const url = `http://127.0.0.1:${port}`

export default defineConfig({
  ...analytics,
  testMatch: 'paired-outcomes.spec.ts',
  use: {
    ...analytics.use,
    baseURL: url,
    storageState: { cookies: [], origins: [{ origin: url, localStorage: [{ name: 'pyrit-tour-completed', value: 'true' }] }] },
  },
  projects: [{ name: 'paired-outcomes', use: { viewport: { width: 1440, height: 1050 } } }],
  webServer: {
    command: `cd .. && uv run --no-sync python -m frontend.e2e.fixtures.paired_outcomes_backend --port ${port}`,
    env: { PYTHONIOENCODING: 'utf-8' }, url: `${url}/api/health`, reuseExistingServer: false, timeout: 120_000,
  },
})
