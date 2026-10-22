import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

import type { AttackSummary, Finding, Operation, ScenarioRunListItem, ScenarioRunSummary, UserPreferences } from '../src/types'
import { compatibilityHeaders } from './_compatibility'
import { waitForAppReady } from './_runtime'

const LEGACY_ATTACK_ID = '11111111-1111-4111-8111-111111111111'
const PROPOSED_NAME = 'browser_proposed'

test.use({
  // eslint-disable-next-line no-empty-pattern
  extraHTTPHeaders: async ({}, runTest) => runTest(compatibilityHeaders()),
  storageState: async ({ baseURL }, runTest) => {
    if (!baseURL) throw new Error('Browser acceptance requires a frontend base URL.')
    await runTest({
      cookies: [],
      origins: [{
        origin: new URL(baseURL).origin,
        localStorage: [{ name: 'pyrit-tour-completed', value: 'true' }],
      }],
    })
  },
})

async function createOperation(request: APIRequestContext, name: string): Promise<Operation> {
  const response = await request.post('/api/operations', { headers: compatibilityHeaders(), data: { name } })
  expect(response.status()).toBe(201)
  return response.json()
}

// The stock scenario catalog builds its scorers from these targets; they are never called offline.
async function ensureCatalogTargets(request: APIRequestContext): Promise<void> {
  for (const name of ['objective_scorer_chat', 'adversarial_chat']) {
    if ((await request.get(`/api/targets/${name}`)).status() !== 404) continue
    const created = await request.post('/api/targets', { data: {
      name, type: 'OpenAIChatTarget', auth_mode: 'api_key',
      params: { endpoint: 'http://127.0.0.1:9/v1', model_name: 'local-test', api_key: 'local-test-only' },
    } })
    expect(created.ok(), await created.text()).toBe(true)
  }
}

async function sendChat(page: Page, request: APIRequestContext, text: string): Promise<AttackSummary> {
  await page.goto('/chat')
  await page.getByRole('combobox', { name: 'Chat target', exact: true }).selectOption('browser_offline')
  await page.getByRole('textbox', { name: 'Type prompt here', exact: true }).fill(text)
  const created = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/attacks' && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  const response = await created
  expect(response.status()).toBe(201)
  const attack: AttackSummary = await response.json()
  await expect(page.getByText('Offline browser response.', { exact: true })).toBeVisible()
  const stored = await request.get(`/api/attacks/${attack.attack_result_id}`)
  expect(stored.ok()).toBeTruthy()
  return stored.json()
}

for (const width of [1280, 390]) {
  test(`explicitly resolves a proposed name without startup registration at ${width}px @seeded`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 })
    const before = await (await request.get('/api/operations')).json()
    const version = await (await request.get('/api/version')).json()
    expect(version.default_operation).toBe(PROPOSED_NAME)
    await page.goto('/chat')
    await waitForAppReady(page)
    const bar = page.getByRole('region', { name: 'Default Labels' })
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue(PROPOSED_NAME)
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveAccessibleDescription('Not registered')
    await page.getByRole('combobox', { name: 'Chat target', exact: true }).selectOption('browser_offline')
    await page.getByRole('textbox', { name: 'Type prompt here', exact: true }).fill('Blocked until resolved.')
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeDisabled()
    expect((await (await request.get('/api/operations')).json()).items).toEqual(before.items)

    // Even an existing matching name must be chosen explicitly.
    const existing: Operation | undefined = before.items.find((item: Operation) => item.name === PROPOSED_NAME)
    const operation = existing ?? await createOperation(request, PROPOSED_NAME)
    await page.reload()
    await waitForAppReady(page)
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveAccessibleDescription('Not registered')
    await bar.getByRole('button', { name: 'Register', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(PROPOSED_NAME)
    await dialog.getByRole('button', { name: 'Create operation', exact: true }).click()
    await expect(dialog.getByText(/already exists/i)).toBeVisible()
    await dialog.getByRole('button', { name: 'Use existing', exact: true }).click()
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue(PROPOSED_NAME)
    await expect(bar.getByText('Not registered', { exact: true })).toHaveCount(0)
    await expect.poll(() => page.evaluate(() =>
      JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
    )).toEqual({ id: operation.id })
  })

  test(`keeps clearing an operation across reloads and resolves the restored default at ${width}px @seeded`, async ({ page, request }) => {
    await page.setViewportSize({ width, height: 844 })
    const directory = await (await request.get('/api/operations')).json()
    const existing: Operation | undefined = directory.items.find((item: Operation) => item.name === PROPOSED_NAME)
    const operation = existing ?? await createOperation(request, PROPOSED_NAME)
    await page.goto('/chat')
    await waitForAppReady(page)
    const bar = page.getByRole('region', { name: 'Default Labels' })
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveAccessibleDescription('Not registered')
    await bar.getByRole('combobox', { name: 'Operation', exact: true }).click()
    await page.getByRole('option', { name: PROPOSED_NAME, exact: true }).click()
    await expect.poll(() => page.evaluate(() =>
      JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
    )).toEqual({ id: operation.id })
    await bar.getByRole('button', { name: 'Clear operation', exact: true }).click()
    await expect.poll(() => page.evaluate(() =>
      JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
    )).toBeNull()
    await page.reload()
    await waitForAppReady(page)
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue('')
    await page.evaluate(() => localStorage.removeItem('pyrit.userPreferences.v1.local'))
    await page.reload()
    await waitForAppReady(page)
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue(PROPOSED_NAME)
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveAccessibleDescription('Not registered')
    await bar.getByRole('combobox', { name: 'Operation', exact: true }).click()
    await page.getByRole('option', { name: PROPOSED_NAME, exact: true }).click()
    await expect(bar.getByText('Not registered', { exact: true })).toHaveCount(0)
    await expect.poll(() => page.evaluate(() =>
      JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
    )).toEqual({ id: operation.id })
  })

  test(`registers an operation name restored from preferences at ${width}px @seeded`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    const newName = `Restored ${width} ${Date.now()}`
    await page.addInitScript((name: string) => {
      const preferences: UserPreferences = {
        targets: { objective: null, adversarial: null },
        hiddenTargetRegistryNames: [],
        labels: {},
        theme: 'system',
        chatMarkdown: false,
        operation: { name },
      }
      localStorage.setItem('pyrit.userPreferences.v1.local', JSON.stringify(preferences))
    }, newName)
    await page.goto('/chat')
    await waitForAppReady(page)
    const bar = page.getByRole('region', { name: 'Default Labels' })
    const dialog = page.getByRole('dialog')
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue(newName)
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveAccessibleDescription('Not registered')
    await bar.getByRole('button', { name: 'Register', exact: true }).click()
    await expect(dialog.getByRole('textbox', { name: 'Name', exact: true })).toHaveValue(newName)
    const registered = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/api/operations' && response.request().method() === 'POST')
    await dialog.getByRole('button', { name: 'Create operation', exact: true }).click()
    const registration = await registered
    expect(registration.status()).toBe(201)
    const saved: Operation = await registration.json()
    await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue(newName)
    await expect.poll(() => page.evaluate(() =>
      JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
    )).toEqual({ id: saved.id })
  })
}

test('persists selected IDs for chat and scanner history, and clear survives reload @seeded', async ({ page, request }) => {
  test.setTimeout(90_000)
  const operation = await createOperation(request, `Activity ${Date.now()}`)
  await page.goto('/')
  await waitForAppReady(page)
  const bar = page.getByRole('region', { name: 'Default Labels' })
  await bar.getByRole('combobox', { name: 'Operation', exact: true }).click()
  await page.getByRole('option', { name: operation.name, exact: true }).click()
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
  )).toEqual({ id: operation.id })
  const chat = await sendChat(page, request, 'Selected Operation chat.')
  expect(chat.operation_id).toBe(operation.id)

  await ensureCatalogTargets(request)
  await page.goto('/scanner/browser_offline')
  await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption('browser_offline')
  await page.getByTestId('launch-scenario-btn').click()
  const started = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/scenarios/runs' && response.request().method() === 'POST')
  await page.getByTestId('confirm-launch-scenario-btn').click()
  const startedResponse = await started
  expect(startedResponse.ok()).toBeTruthy()
  const run: ScenarioRunSummary = await startedResponse.json()
  await expect.poll(async () => {
    const response = await request.get(`/api/scenarios/runs/${run.scenario_result_id}`)
    expect(response.ok()).toBeTruthy()
    return (await response.json()).status
  }, { timeout: 30_000 }).toBe('COMPLETED')
  const scanHistory = await (await request.get(`/api/scenarios/runs?operation_id=${operation.id}`)).json()
  expect(scanHistory.items.find((item: ScenarioRunListItem) => item.scenario_result_id === run.scenario_result_id))
    .toMatchObject({ operation_id: operation.id, status: 'COMPLETED', completed_attacks: 1 })
  const scopedAttacks = await (await request.get(`/api/attacks?operation_id=${operation.id}`)).json()
  expect(scopedAttacks.items.length).toBe(2)
  expect(scopedAttacks.items.every((item: AttackSummary) => item.operation_id === operation.id)).toBeTruthy()

  await page.goto(`/operations/${operation.id}`)
  await page.getByRole('link', { name: 'All execution history', exact: true }).click()
  expect(new URL(page.url()).searchParams.getAll('operation_id')).toEqual([operation.id])
  await expect(page.getByTestId(`attack-row-${chat.attack_result_id}`)).toBeVisible()
  await page.getByRole('tab', { name: 'Scanner', exact: true }).click()
  expect(new URL(page.url()).searchParams.getAll('operation_id')).toEqual([operation.id])
  await expect(page.getByTestId(`scenario-history-row-${run.scenario_result_id}`)).toBeVisible()

  await bar.getByRole('button', { name: 'Clear operation', exact: true }).click()
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}').operation,
  )).toBeNull()
  await page.reload()
  await waitForAppReady(page)
  await expect(bar.getByRole('combobox', { name: 'Operation', exact: true })).toHaveValue('')
  const unassigned = await sendChat(page, request, 'Unassigned after reload.')
  expect(unassigned.operation_id).toBeNull()
})

test('links cross-Operation and unassigned evidence without rewriting source attribution @seeded', async ({ page, request }) => {
  const operation = await createOperation(request, `Finding ${Date.now()}`)
  const sourceOperation = await createOperation(request, `Source ${Date.now()}`)
  const findingResponse = await request.post(`/api/operations/${operation.id}/findings`, {
    headers: compatibilityHeaders(), data: { title: 'Cross-Operation evidence', severity: 'low' },
  })
  expect(findingResponse.status()).toBe(201)
  const finding: Finding = await findingResponse.json()
  for (const sourceId of [sourceOperation.id, null]) {
    const response = await request.post('/api/attacks', {
      headers: compatibilityHeaders(), data: {
        operation_id: sourceId,
        prepended_conversation: [{ role: 'user', pieces: [{ original_value: 'Saved evidence source.' }] }],
      },
    })
    expect(response.status()).toBe(201)
    const source: AttackSummary = await response.json()
    await page.goto(`/attacks/${source.attack_result_id}/conversations/${source.conversation_id}`)
    await page.getByTestId('objective-outcome-button').click()
    await page.getByRole('button', { name: 'Add as finding' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('combobox', { name: 'Operation', exact: true }).click()
    await page.getByRole('option', { name: operation.name, exact: true }).click()
    await dialog.getByRole('radio', { name: /Cross-Operation evidence/ }).click()
    const attached = page.waitForResponse(result =>
      new URL(result.url()).pathname.endsWith(`/findings/${finding.id}/evidence`) && result.request().method() === 'POST')
    await dialog.getByRole('button', { name: 'Attach attack', exact: true }).click()
    expect((await attached).status()).toBe(201)
    const stored = await (await request.get(`/api/attacks/${source.attack_result_id}`)).json()
    expect(stored.operation_id).toBe(sourceId)
  }
  await page.goto(`/operations/${operation.id}`)
  await page.getByRole('button', { name: 'Evidence (2)', exact: true }).click()
  const evidence = page.getByRole('region', { name: 'Finding evidence', exact: true })
  await expect(evidence.getByText(`From operation: ${sourceOperation.name}`, { exact: true })).toBeVisible()
  await expect(evidence.getByText('Not in an operation', { exact: true })).toBeVisible()
  await expect(evidence.getByRole('link', { name: 'Open attack', exact: true })).toHaveCount(2)
})

test('keeps legacy name-only activity in paginated general history, outside ID-scoped history @seeded', async ({ page, request }) => {
  const directory = await (await request.get('/api/operations')).json()
  const existing: Operation | undefined = directory.items.find((item: Operation) => item.name === PROPOSED_NAME)
  const operation = existing ?? await createOperation(request, PROPOSED_NAME)
  const legacy = await (await request.get(`/api/attacks/${LEGACY_ATTACK_ID}`)).json()
  expect(legacy.operation).toBe(PROPOSED_NAME)
  expect(legacy.operation_id).toBeNull()
  for (let index = 0; index < 25; index++) {
    const newer = await request.post('/api/attacks', {
      data: {
        prepended_conversation: [{ role: 'user', pieces: [{ original_value: `Pagination fixture ${index}` }] }],
      },
    })
    expect(newer.status()).toBe(201)
  }
  await page.goto('/history/attacks')
  await expect(page.getByRole('table')).toBeVisible()
  const legacyRow = page.getByTestId(`attack-row-${LEGACY_ATTACK_ID}`)
  const next = page.getByRole('button', { name: 'Next', exact: true })
  await expect(next).toBeEnabled()
  while (!await legacyRow.isVisible() && await next.isEnabled()) {
    const loaded = page.waitForResponse(response => {
      const url = new URL(response.url())
      return url.pathname === '/api/attacks' && url.searchParams.has('cursor')
        && response.request().method() === 'GET'
    })
    await next.click()
    expect((await loaded).status()).toBe(200)
    await expect(page.getByRole('progressbar', { name: 'Loading attacks...' })).toHaveCount(0)
  }
  await expect(legacyRow).toBeVisible()
  await page.goto(`/history/attacks?operation_id=${operation.id}`)
  await expect(page.getByRole('combobox', { name: 'All operations' })).toHaveValue(PROPOSED_NAME)
  await expect(page.getByRole('progressbar', { name: 'Loading attacks...' })).toHaveCount(0)
  await expect(page.getByTestId(`attack-row-${LEGACY_ATTACK_ID}`)).toHaveCount(0)
})
