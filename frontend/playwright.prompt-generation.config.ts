import process from 'node:process'

import { defineConfig, devices } from '@playwright/test'

import type { UserPreferences } from './src/types'
import analytics from './playwright.analytics.config'

const port = Number(process.env.PYRIT_E2E_PROMPT_GENERATION_PORT ?? '18614')
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('PYRIT_E2E_PROMPT_GENERATION_PORT must be a valid TCP port.')
}
const url = `http://127.0.0.1:${port}`
const preferences: UserPreferences = {
  targets: { adversarial: null }, hiddenTargetRegistryNames: [], labels: {},
  theme: 'system', chatMarkdown: false, operation: null,
}

export default defineConfig({
  ...analytics,
  testMatch: 'prompt-generation.spec.ts',
  use: {
    ...analytics.use,
    baseURL: url,
    storageState: {
      cookies: [],
      origins: [{
        origin: url,
        localStorage: [
          { name: 'pyrit-tour-completed', value: 'true' },
          { name: 'pyrit.userPreferences.v1.local', value: JSON.stringify(preferences) },
        ],
      }],
    },
  },
  projects: [
    { name: 'prompt-generation-desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1050 } } },
  ],
  webServer: {
    command: `cd .. && uv run --no-sync python -m frontend.e2e.fixtures.prompt_generation_backend --port ${port}`,
    env: { PYTHONIOENCODING: 'utf-8' },
    url: `${url}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
