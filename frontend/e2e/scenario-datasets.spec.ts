import { expect, test, type APIRequestContext, type APIResponse, type Locator, type Page, type Response, type TestInfo } from '@playwright/test'
import { writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, relative, sep } from 'node:path'

import type {
  RunScenarioRequest, ScenarioDatasetSummary, ScenarioPreset, ScenarioPresetResponse,
  ScenarioRunSizeEstimateRequest, ScenarioRunSizeEstimateResponse, ScenarioRunSummary,
} from '../src/types'
import { presetEditorRoutePath, presetScannerRoutePath } from '../src/components/ScenarioPresets/presetRoutes'
import { scenarioRunRoutePath } from '../src/utils/routeParams'
import { compatibilityHeaders } from './_compatibility'

const SCENARIO = 'offline.datasets'
const TARGET = 'Local dataset target'
const ESTIMATE_PATH = `/api/scenarios/catalog/${SCENARIO}/estimate`
const STORED = [
  { name: 'acceptance_primary', count: 8 },
  { name: 'acceptance_searchable', count: 3 },
  { name: 'acceptance_secondary', count: 6 },
]

type ControlAction = 'reset' | 'status' | 'hold_estimate' | 'release_estimate' | 'fail_estimate'
  | 'hold_metadata' | 'release_metadata' | 'fail_metadata'

interface FixtureStatus {
  estimate_waiting: boolean
  metadata_waiting: boolean
  preparations: number
  provider_lookups: number
  preset_source: string
  preset_mutations: Array<{ action: 'save' | 'delete'; source: string; document: string; lock: string }>
  database_path: string
  configuration_paths: string[]
  sent: string[]
  stored: typeof STORED
  estimates: Array<{ scenario: string; request: ScenarioRunSizeEstimateRequest; response: ScenarioRunSizeEstimateResponse }>
}

interface DatasetTestContext {
  page: Page
  request: APIRequestContext
}

async function control(request: APIRequestContext, action: ControlAction): Promise<FixtureStatus> {
  const response = await request.post('/api/e2e/scenario-datasets', {
    headers: compatibilityHeaders(), data: { action },
  })
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

async function capture(page: Page, testInfo: TestInfo, name: string, anchor: Locator): Promise<void> {
  await anchor.scrollIntoViewIfNeeded()
  const path = testInfo.outputPath(`${name}.png`)
  await page.screenshot({ path, animations: 'disabled' })
  await testInfo.attach(name, { path, contentType: 'image/png' })
}

async function attachJson(testInfo: TestInfo, name: string, value: object): Promise<void> {
  const path = testInfo.outputPath(`${name}.json`)
  await writeFile(path, JSON.stringify(value, null, 2))
  await testInfo.attach(name, { path, contentType: 'application/json' })
}

function nextEstimate(page: Page, path: string = ESTIMATE_PATH): Promise<Response> {
  return page.waitForResponse((response: Response) =>
    new URL(response.url()).pathname === path && response.request().method() === 'POST')
}

async function expectBudget(response: Response | APIResponse, count: number): Promise<ScenarioRunSizeEstimateResponse> {
  expect(response.status(), await response.text()).toBe(200)
  const estimate: ScenarioRunSizeEstimateResponse = await response.json()
  expect(estimate.status).toBe('approximate')
  expect(estimate.estimated_attack_count).toBe(count)
  expect(estimate.minimum_attack_count).toBeNull()
  expect(estimate.maximum_attack_count).toBeNull()
  for (const dataset of estimate.datasets) {
    expect(dataset.logical_seed_group_count).toBeNull()
    expect(dataset.selected_seed_group_count).toBeNull()
  }
  return estimate
}

async function openLimit(page: Page, label: string): Promise<Locator> {
  const trigger = page.getByRole('button', { name: `Edit ${label}`, exact: true })
  await trigger.focus()
  await trigger.press('Enter')
  const input = page.getByRole('textbox', { name: label, exact: true })
  await expect(input).toBeVisible()
  return input
}

async function setLimit(page: Page, label: string, value: string): Promise<void> {
  const input = await openLimit(page, label)
  const all = page.getByRole('checkbox', { name: `All available for ${label}`, exact: true })
  if (await all.isChecked()) {
    await all.focus()
    await all.press('Space')
  }
  await input.focus()
  await input.press('ControlOrMeta+A')
  await input.pressSequentially(value)
  await page.getByRole('button', { name: 'Done', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(input).toHaveCount(0)
}

function estimateValue(page: Page, label: string): Locator {
  return page.getByTestId('run-estimate').getByRole('term').filter({ hasText: label })
    .locator('..').getByRole('definition')
}

async function savePreset(request: APIRequestContext, preset: ScenarioPreset): Promise<void> {
  const response = await request.post('/api/scenario-presets', { headers: compatibilityHeaders(), data: preset })
  expect(response.status(), await response.text()).toBe(201)
  const saved: ScenarioPresetResponse = await response.json()
  expect(saved.issues).toEqual([])
}

async function assertNoPreparation(request: APIRequestContext): Promise<FixtureStatus> {
  const status = await control(request, 'status')
  expect(status.preparations).toBe(0)
  expect(status.provider_lookups).toBe(0)
  expect(status.sent).toEqual([])
  expect(status.stored).toEqual(STORED)
  return status
}

async function completedRun(request: APIRequestContext, run: ScenarioRunSummary): Promise<ScenarioRunSummary> {
  let completed = run
  await expect.poll(async (): Promise<string> => {
    const reading = await request.get(`/api/scenarios/runs/${run.scenario_result_id}`, { headers: compatibilityHeaders() })
    expect(reading.status()).toBe(200)
    completed = await reading.json()
    return completed.status
  }).toBe('COMPLETED')
  return completed
}

test.beforeEach(async ({ request }: { request: APIRequestContext }): Promise<void> => {
  await control(request, 'reset')
})

test.afterEach(async ({ request }: { request: APIRequestContext }, testInfo: TestInfo): Promise<void> => {
  const status = await control(request, 'status')
  const root = dirname(dirname(status.database_path))
  const paths = [
    status.preset_source, ...status.configuration_paths,
    ...status.preset_mutations.flatMap(({ source, document, lock }) => [source, document, lock]),
  ]
  for (const path of paths) {
    const inside = relative(root, path)
    expect(isAbsolute(inside)).toBe(false)
    expect(inside === '..' || inside.startsWith(`..${sep}`)).toBe(false)
  }
  if (testInfo.status === 'passed') expect(status.preset_mutations.length).toBeGreaterThan(0)
  await attachJson(testInfo, 'fixture-isolation-and-estimates', status)
})

test('configures, saves, refreshes, estimates without a target, and launches the same dataset request', async ({ page, request }: DatasetTestContext, testInfo: TestInfo): Promise<void> => {
  const name = `dataset_journey_${testInfo.project.name.endsWith('narrow') ? 'narrow' : 'desktop'}`
  const errors: string[] = []
  page.on('pageerror', (error: Error) => { errors.push(error.message) })
  await page.goto('/registry/scenario-presets/new')
  const presetName = page.getByRole('textbox', { name: 'Name', exact: true })
  await presetName.focus()
  await presetName.pressSequentially(name)
  const scenario = page.getByRole('combobox', { name: 'Scenario', exact: true })
  await scenario.focus()
  await scenario.press('Enter')
  const scenarioOption = page.getByRole('option', { name: SCENARIO, exact: true })
  await expect(scenarioOption).toBeVisible()
  const optionId = await scenarioOption.getAttribute('id')
  if (!optionId) throw new Error('The scenario option needs a keyboard focus identifier.')
  const optionCount = await page.getByRole('listbox').getByRole('option').count()
  for (let index = 0; index < optionCount; index += 1) {
    await scenario.press('ArrowDown')
    if (await scenario.getAttribute('aria-activedescendant') === optionId) break
  }
  await expect(scenario).toHaveAttribute('aria-activedescendant', optionId)
  await scenario.press('Enter')
  await expect(scenario).toHaveValue(SCENARIO)
  await expect(page.getByRole('button', { name: 'Edit acceptance_secondary objective limit' })).toHaveText('Up to 4 of 6')

  const search = page.getByRole('combobox', { name: 'Add dataset', exact: true })
  await search.focus()
  await search.pressSequentially('SEARCHABLE')
  await expect(page.getByRole('listbox').getByRole('option')).toHaveText(['acceptance_searchable (local)'])
  await capture(page, testInfo, 'dataset-search-keyboard', search)
  await search.press('ArrowDown')
  await search.press('Enter')
  await expect(page.getByRole('row').filter({ hasText: 'acceptance_searchable (local)' })).toBeVisible()
  await setLimit(page, 'acceptance_primary objective limit', '3')
  await setLimit(page, 'acceptance_searchable objective limit', '1')
  await setLimit(page, 'Maximum objectives', '4')
  await openLimit(page, 'acceptance_primary objective limit')
  await expect(page.getByText('Maximum objectives also limits the combined selection to 4.', { exact: true })).toBeVisible()
  await capture(page, testInfo, 'per-source-cap-and-origin', page.getByRole('textbox', { name: 'acceptance_primary objective limit' }))
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Edit acceptance_primary objective limit' })).toBeFocused()

  const saving = page.waitForResponse((response: Response) =>
    new URL(response.url()).pathname === '/api/scenario-presets' && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Save preset', exact: true }).focus()
  await page.keyboard.press('Enter')
  const savedResponse = await saving
  expect(savedResponse.status(), await savedResponse.text()).toBe(201)
  const saved: ScenarioPresetResponse = await savedResponse.json()
  const datasets = saved.preset.datasets
  expect(datasets).toMatchObject({
    sources: [
      { name: 'acceptance_primary', max_size: 3 },
      { name: 'acceptance_secondary' },
      { name: 'acceptance_searchable', max_size: 1 },
    ],
    max_total: 4,
  })
  expect(saved.preset).not.toHaveProperty('target_name')
  await expect(page).toHaveURL(/\/registry\/scenario-presets$/)
  await page.goto(presetEditorRoutePath(name))
  await page.reload()
  await expect(page.getByRole('button', { name: 'Edit acceptance_primary objective limit' })).toHaveText('Up to 3 of 8')
  await expect(page.getByRole('button', { name: 'Edit acceptance_searchable objective limit' })).toHaveText('Up to 1 of 3')

  const estimating = nextEstimate(page)
  await page.goto(presetScannerRoutePath(SCENARIO, name))
  const response = await estimating
  const estimate = await expectBudget(response, 4)
  expect(response.request().postDataJSON().target_name).toBeUndefined()
  expect(estimate.effective_datasets?.sources).toMatchObject([
    { name: 'acceptance_primary', max_size: 3 },
    { name: 'acceptance_secondary', max_size: 4 },
    { name: 'acceptance_searchable', max_size: 1 },
  ])
  expect(estimate.datasets.find((dataset: ScenarioDatasetSummary) => dataset.name === 'acceptance_primary')?.configured_caps)
    .toContainEqual({ label: 'per-dataset cap', count: 3, configured_on: 'dataset', dataset_name: 'acceptance_primary' })
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('Up to 4')
  await capture(page, testInfo, 'configuration-from-refreshed-preset', page.getByRole('region', { name: 'Datasets', exact: true }))
  await capture(page, testInfo, 'approximate-configured-estimate', page.getByTestId('run-estimate'))
  await assertNoPreparation(request)

  const launch = page.getByRole('button', { name: 'Launch scan', exact: true })
  await expect(launch).toBeEnabled()
  await launch.focus()
  await launch.press('Enter')
  await expect(page.getByRole('alert').filter({ hasText: 'Select a target.' })).toHaveText('Select a target.')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const rejected = await request.post('/api/scenarios/runs', {
    headers: compatibilityHeaders(), data: { ...response.request().postDataJSON(), scenario_name: SCENARIO },
  })
  expect(rejected.status()).toBe(400)
  await assertNoPreparation(request)
  const withTarget = nextEstimate(page)
  const targetSelect = page.getByTestId('scenario-target-select')
  await expect(targetSelect.locator('option').last()).toHaveAttribute('value', TARGET)
  await targetSelect.focus()
  await targetSelect.press('End')
  await targetSelect.press('Tab')
  await expect(targetSelect).toHaveValue(TARGET)
  await expectBudget(await withTarget, 4)
  await expect(launch).toBeEnabled()
  await launch.focus()
  await launch.press('Enter')
  const preview = page.getByRole('dialog', { name: 'Run preview' })
  await expect(preview).toContainText('Up to 4 attacks')
  await capture(page, testInfo, 'launch-preview', preview)
  const starting = page.waitForResponse((outgoing: Response) =>
    new URL(outgoing.url()).pathname === '/api/scenarios/runs' && outgoing.request().method() === 'POST')
  await preview.getByRole('button', { name: 'Launch scan', exact: true }).focus()
  await page.keyboard.press('Enter')
  const started = await starting
  expect(started.status(), await started.text()).toBe(202)
  const launchRequest: RunScenarioRequest = started.request().postDataJSON()
  expect(launchRequest.datasets).toEqual(datasets)
  const run: ScenarioRunSummary = await started.json()
  const completed = await completedRun(request, run)
  expect(completed).toMatchObject({ total_attacks: 4, completed_attacks: 4, planned_total_available: true, error: null })
  const configuration = await request.get(`/api/scenarios/runs/${run.scenario_result_id}/launch-configuration`, {
    headers: compatibilityHeaders(),
  })
  expect(configuration.status()).toBe(200)
  expect((await configuration.json()).datasets).toEqual(datasets)
  const finalStatus = await control(request, 'status')
  expect(finalStatus.preparations).toBe(1)
  expect(finalStatus.provider_lookups).toBe(0)
  expect(finalStatus.sent).toHaveLength(4)
  expect(finalStatus.stored).toEqual(STORED)
  for (const [dataset, cap] of [
    ['acceptance_primary', 3], ['acceptance_secondary', 4], ['acceptance_searchable', 1],
  ] as const) {
    expect(finalStatus.sent.filter((value: string) => value.includes(`for ${dataset} example `)).length)
      .toBeLessThanOrEqual(cap)
  }
  await expect(page).toHaveURL(new RegExp(`${scenarioRunRoutePath(run.scenario_result_id)}$`))
  await capture(page, testInfo, 'exact-initialized-run-plan', page.getByRole('heading', { level: 1, name: SCENARIO, exact: true }))
  await attachJson(testInfo, 'real-estimate-and-launch', { estimate, launchRequest, completed, finalStatus })

  // Without the combined cap, real execution must honor source overrides and the inherited per-dataset cap.
  const inheritedRequest: RunScenarioRequest = {
    ...launchRequest, datasets: { ...datasets, max_per_dataset: 2, max_total: 'all' },
  }
  const inheritedEstimate = await request.post(ESTIMATE_PATH, {
    headers: compatibilityHeaders(),
    data: { ...response.request().postDataJSON(), datasets: inheritedRequest.datasets },
  })
  const inheritedBudget = await expectBudget(inheritedEstimate, 6)
  expect((await control(request, 'status')).preparations).toBe(1)
  const inheritedLaunch = await request.post('/api/scenarios/runs', {
    headers: compatibilityHeaders(), data: inheritedRequest,
  })
  expect(inheritedLaunch.status(), await inheritedLaunch.text()).toBe(202)
  const inheritedCompleted = await completedRun(request, await inheritedLaunch.json())
  expect(inheritedCompleted).toMatchObject({
    total_attacks: 6, completed_attacks: 6, planned_total_available: true, error: null,
  })
  const inheritedStatus = await control(request, 'status')
  const delivered = inheritedStatus.sent.slice(finalStatus.sent.length)
  expect(delivered).toHaveLength(6)
  for (const [dataset, count] of [
    ['acceptance_primary', 3], ['acceptance_secondary', 2], ['acceptance_searchable', 1],
  ] as const) {
    expect(delivered.filter((value: string) => value.includes(`for ${dataset} example `))).toHaveLength(count)
  }
  expect(inheritedStatus.preparations).toBe(2)
  expect(inheritedStatus.provider_lookups).toBe(0)
  expect(inheritedStatus.stored).toEqual(STORED)
  await attachJson(testInfo, 'real-source-and-inherited-cap-execution', {
    inheritedBudget, inheritedRequest, inheritedCompleted, inheritedStatus,
  })
  expect(errors).toEqual([])
})

test('retains configured limits through deliberately held and failed estimates and metadata reads', async ({ page, request }: DatasetTestContext, testInfo: TestInfo): Promise<void> => {
  const name = `dataset_faults_${testInfo.project.name.endsWith('narrow') ? 'narrow' : 'desktop'}`
  await savePreset(request, { name, scenario_name: SCENARIO, include_baseline: false })
  await control(request, 'hold_estimate')
  await page.goto(presetScannerRoutePath(SCENARIO, name))
  await expect.poll(async (): Promise<boolean> => (await control(request, 'status')).estimate_waiting).toBe(true)
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('Calculating...')
  await capture(page, testInfo, 'initial-estimate-loading-deliberate-hold', page.getByTestId('run-estimate'))
  const initial = nextEstimate(page)
  await control(request, 'release_estimate')
  await expectBudget(await initial, 5)

  await control(request, 'hold_estimate')
  await setLimit(page, 'Maximum objectives', '3')
  await expect.poll(async (): Promise<boolean> => (await control(request, 'status')).estimate_waiting).toBe(true)
  await expect(page.getByText('Updating estimate...', { exact: true })).toBeVisible()
  await expect(estimateValue(page, 'Dataset selection limit')).toHaveText('3')
  await capture(page, testInfo, 'refreshing-estimate-deliberate-hold', page.getByTestId('run-estimate'))
  await control(request, 'fail_estimate')
  const failing = nextEstimate(page)
  await control(request, 'release_estimate')
  expect((await failing).status()).toBe(503)
  await expect(page.getByTestId('run-estimate')).toContainText('Showing the last successful estimate.')
  await expect(page.getByTestId('run-estimate')).toContainText('Deliberate offline estimate failure.')
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('Up to 5')
  await expect(estimateValue(page, 'Dataset selection limit')).toHaveText('3')
  await expect(page.getByRole('button', { name: 'Edit Maximum objectives' })).toHaveText('Up to 3')
  await capture(page, testInfo, 'stale-estimate-deliberate-error', page.getByTestId('run-estimate'))
  const recovery = nextEstimate(page)
  await setLimit(page, 'Maximum objectives', '4')
  await expectBudget(await recovery, 4)
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('Up to 4')
  await expect(page.getByText('Showing the last successful estimate.')).toHaveCount(0)

  await openLimit(page, 'Maximum objectives')
  const invalid = page.getByRole('textbox', { name: 'Maximum objectives', exact: true })
  await invalid.fill('0')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('region', { name: 'Datasets', exact: true }).getByRole('alert'))
    .toHaveText('Maximum objectives must be a positive integer.')
  await expect(page.getByRole('button', { name: 'Launch scan', exact: true })).toBeDisabled()
  await openLimit(page, 'Maximum objectives')
  await page.getByRole('button', { name: 'Reset to 5', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Edit Maximum objectives' })).toHaveText('Up to 5')

  await control(request, 'hold_metadata')
  await control(request, 'fail_estimate')
  await page.reload()
  await expect.poll(async (): Promise<boolean> => (await control(request, 'status')).metadata_waiting).toBe(true)
  const metadataStatus = page.getByRole('region', { name: 'Datasets', exact: true })
    .getByRole('status').filter({ hasText: 'Checking dataset details...' })
  await expect(metadataStatus).toBeVisible()
  await capture(page, testInfo, 'dataset-metadata-loading-deliberate-hold', page.getByRole('region', { name: 'Datasets', exact: true }))
  await expect(page.getByTestId('run-estimate')).toContainText('The backend estimate could not be refreshed.')
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('Unknown')
  await capture(page, testInfo, 'estimate-unavailable-deliberate-error', page.getByTestId('run-estimate'))
  await control(request, 'fail_metadata')
  await control(request, 'release_metadata')
  await expect(page.getByRole('region', { name: 'Datasets', exact: true }).getByRole('alert'))
    .toHaveText('Deliberate offline dataset metadata failure.')
  await expect(metadataStatus).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Edit Maximum objectives' })).toHaveText('Up to 5')
  await capture(page, testInfo, 'dataset-metadata-deliberate-error', page.getByRole('region', { name: 'Datasets', exact: true }))
  await page.getByRole('button', { name: 'Retry checking datasets', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('button', { name: 'Edit acceptance_primary objective limit' })).toHaveText('Up to 2 of 8')
  await assertNoPreparation(request)
})

test('uses real inherited caps, unlimited unavailability, and the benchmark conditional range without inventing precision', async ({ page, request }: DatasetTestContext, testInfo: TestInfo): Promise<void> => {
  const suffix = testInfo.project.name.endsWith('narrow') ? 'narrow' : 'desktop'
  const inheritedName = `dataset_inherited_${suffix}`
  await savePreset(request, {
    name: inheritedName, scenario_name: SCENARIO, include_baseline: false,
    datasets: { max_per_dataset: 2, max_total: 'all' },
  })
  const inherited = nextEstimate(page)
  await page.goto(presetScannerRoutePath(SCENARIO, inheritedName))
  const estimate = await expectBudget(await inherited, 4)
  expect(estimate.effective_datasets).toMatchObject({
    max_per_dataset: 2, max_total: 'all',
    sources: [{ name: 'acceptance_primary', max_size: 2 }, { name: 'acceptance_secondary', max_size: 2 }],
  })
  await expect(page.getByRole('button', { name: 'Edit acceptance_secondary objective limit' })).toHaveText('Up to 2 of 6')
  await expect(estimateValue(page, 'Dataset selection limit')).toHaveText('No total limit')
  await capture(page, testInfo, 'inherited-per-dataset-budget', page.getByRole('region', { name: 'Datasets', exact: true }))

  for (const source of ['acceptance_primary', 'acceptance_secondary']) {
    await openLimit(page, `${source} objective limit`)
    const all = page.getByRole('checkbox', { name: `All available for ${source} objective limit` })
    await all.focus()
    await all.press('Space')
    await page.keyboard.press('Escape')
  }
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('Unknown')
  await expect(page.getByTestId('run-estimate')).toContainText('No size limit is configured for at least one selected population.')
  await expect(page.getByRole('button', { name: 'Edit acceptance_primary objective limit' })).toHaveText('8 examples')
  await capture(page, testInfo, 'unlimited-contract-unavailable', page.getByTestId('run-estimate'))

  const rangeName = `dataset_cached_${suffix}`
  await savePreset(request, {
    name: rangeName, scenario_name: 'offline.dataset_cache', techniques: ['dataset_adversarial'], include_baseline: false,
    datasets: { sources: [{ name: 'acceptance_primary' }], max_total: 3 },
    scenario_params: { adversarial_targets: [TARGET], use_cached: true },
  })
  const ranging = nextEstimate(page, '/api/scenarios/catalog/offline.dataset_cache/estimate')
  await page.goto(presetScannerRoutePath('offline.dataset_cache', rangeName))
  const rangeResponse = await ranging
  expect(rangeResponse.status(), await rangeResponse.text()).toBe(200)
  const range: ScenarioRunSizeEstimateResponse = await rangeResponse.json()
  expect(range).toMatchObject({
    status: 'conditional', estimated_attack_count: null, minimum_attack_count: 0, maximum_attack_count: 3,
    condition: 'prior_execution_results',
  })
  expect(rangeResponse.request().postDataJSON().target_name).toBeUndefined()
  await expect(estimateValue(page, 'Total atomic attacks')).toHaveText('About 0-3')
  await capture(page, testInfo, 'backend-conditional-cache-range', page.getByTestId('run-estimate'))
  await attachJson(testInfo, 'real-conditional-estimate', range)
  await assertNoPreparation(request)
})
