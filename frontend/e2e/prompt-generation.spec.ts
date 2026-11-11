import { expect, test, type Locator, type Page, type Request, type Response, type TestInfo } from '@playwright/test'

import type {
  PromptGenerationCreateRequest,
  PromptGenerationDetail,
  PromptGenerationPreflightResponse,
  PromptGenerationReview,
  DatasetListResponse,
  ScenarioDatasetLoadResponse,
  ScenarioRunSummary,
} from '../src/types'
import { formatRange } from '../src/components/PromptGeneration/promptGenerationPresentation'

const TARGET = 'Offline generation target'
const POLICY = "Explain published return policies. Do not disclose another customer's private order details."
const EDITED_DEFINITION = 'Explains the published return policy, including the 30-day window.'
const FOLLOW_UP_SCENARIO = 'foundry.assert_policy'
const GENERATION_PATH = /\/datasets\/generate\/(?!new$)[^/?#]+$/
// Offline stages finish in about a second; the job page polls every two seconds.
const JOB_TIMEOUT_MS = 20_000
// The first dataset summary scans every registered provider and can take several seconds cold.
const CATALOG_TIMEOUT_MS = 20_000
const pageErrors = new WeakMap<Page, string[]>()

interface JourneyContext {
  readonly page: Page
}

interface PreparationCounts {
  readonly offline_provider: number
  readonly offline_retry_provider: number
  readonly offline_added_provider: number
  readonly offline_stored: number
}

async function preparationCounts(page: Page, compatibilityId: string): Promise<PreparationCounts> {
  const response = await page.request.get('/api/offline-dataset-preparation', {
    headers: { 'PyRIT-Compatibility-ID': compatibilityId },
  })
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

async function limitObjectives(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Edit Maximum objectives', exact: true }).click()
  await page.getByRole('checkbox', { name: 'All available for Maximum objectives', exact: true }).uncheck()
  await page.getByRole('textbox', { name: 'Maximum objectives', exact: true }).fill('2')
  await page.keyboard.press('Escape')
}

async function verifyAutomaticPreparation(page: Page, testInfo: TestInfo): Promise<void> {
  const catalog = page.waitForResponse((response: Response) =>
    apiPath(response) === '/api/datasets' && response.request().method() === 'GET')
  await page.goto('/datasets')
  const listed = await catalog
  expect(listed.status(), await listed.text()).toBe(200)
  const compatibilityId = listed.request().headers()['pyrit-compatibility-id']
  if (!compatibilityId) throw new Error('The dataset catalog did not send its compatibility identity.')
  expect(await preparationCounts(page, compatibilityId)).toEqual({
    offline_provider: 0, offline_retry_provider: 0, offline_added_provider: 0, offline_stored: 0,
  })
  const path = '/scanner/launch/offline.auto_prepare'
  const loadPath = '/api/scenarios/catalog/offline.auto_prepare/datasets/load'
  let preparations = 0
  const countPreparation = (request: Request): void => {
    if (new URL(request.url()).pathname === loadPath && request.method() === 'POST') preparations += 1
  }
  page.on('request', countPreparation)
  const preparingDefaults = page.waitForResponse((response: Response) => apiPath(response) === loadPath)
  await page.goto(path)
  const defaults = await preparingDefaults
  expect(defaults.status(), await defaults.text()).toBe(200)
  const defaultData: ScenarioDatasetLoadResponse = await defaults.json()
  expect(defaultData.items).toEqual(expect.arrayContaining([
    expect.objectContaining({ name: 'offline_provider', loaded: true, logical_examples: 3 }),
  ]))
  // Preparation does not rewrite the scenario's SDK policy or fetch stored data.
  expect(defaultData.settings.sources[0]?.fetch).toBe('never')
  expect(await preparationCounts(page, compatibilityId)).toEqual({
    offline_provider: 1, offline_retry_provider: 0, offline_added_provider: 0, offline_stored: 0,
  })
  await expect(page.getByRole('radiogroup', { name: 'Dataset loading' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Load datasets', exact: true })).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
  await expect(page.getByTestId('launch-scenario-btn')).toBeEnabled()
  const sizing = page.waitForResponse((response: Response) => apiPath(response).endsWith('/offline.auto_prepare/estimate')
    && response.request().postDataJSON()?.datasets?.max_total === 2)
  await limitObjectives(page)
  expect((await sizing).status()).toBe(200)
  expect(preparations).toBe(1)

  const failing = page.waitForResponse((response: Response) => apiPath(response) === loadPath)
  await page.goto(`${path}?dataset=offline_stored&dataset=offline_retry_provider`)
  const failed = await failing
  expect(failed.status(), await failed.text()).toBe(400)
  await expect(page.getByRole('alert').filter({ hasText: 'Synthetic dataset provider is temporarily unavailable.' }).first())
    .toBeVisible()
  await expect(page.getByRole('table', { name: 'Selected datasets' })).toContainText('offline_stored')
  await expect(page.getByRole('table', { name: 'Selected datasets' })).toContainText('offline_retry_provider')
  await expect(page.getByTestId('launch-scenario-btn')).toBeDisabled()
  await limitObjectives(page)
  expect(preparations).toBe(2)
  const retrying = page.waitForResponse((response: Response) => apiPath(response) === loadPath)
  await page.getByRole('button', { name: 'Retry dataset preparation', exact: true }).click()
  const retried = await retrying
  expect(retried.status(), await retried.text()).toBe(200)
  await expect(page.getByRole('button', { name: 'Edit Maximum objectives' })).toContainText('Up to 2')
  await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
  await expect(page.getByTestId('launch-scenario-btn')).toBeEnabled()
  expect(await preparationCounts(page, compatibilityId)).toEqual({
    offline_provider: 1, offline_retry_provider: 2, offline_added_provider: 0, offline_stored: 0,
  })

  const adding = page.waitForResponse((response: Response) => apiPath(response) === loadPath)
  await page.getByRole('combobox', { name: 'Add dataset', exact: true }).fill('offline_added_provider')
  await page.getByRole('option', { name: 'offline_added_provider (unknown)', exact: true }).click()
  const added = await adding
  expect(added.status(), await added.text()).toBe(200)
  const addedData: ScenarioDatasetLoadResponse = await added.json()
  expect(addedData.settings.max_total).toBe(2)
  await expect(page.getByTestId('launch-scenario-btn')).toBeEnabled()
  expect(await preparationCounts(page, compatibilityId)).toEqual({
    offline_provider: 1, offline_retry_provider: 2, offline_added_provider: 1, offline_stored: 0,
  })
  const original = await page.request.get('/api/datasets/seeds?selection_key=dataset%3Anamed%3Aoffline_stored', {
    headers: { 'PyRIT-Compatibility-ID': compatibilityId },
  })
  expect(original.status(), await original.text()).toBe(200)
  expect(JSON.stringify(await original.json())).toContain('Original stored synthetic objective.')
  for (const [label, width, height] of [['desktop', 1440, 1050], ['mobile', 390, 844]] as const) {
    await page.setViewportSize({ width, height })
    await page.getByRole('region', { name: 'Datasets', exact: true }).scrollIntoViewIfNeeded()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`automatic-datasets-${label}.png`), fullPage: true, animations: 'disabled' })
  }
  await page.setViewportSize({ width: 1440, height: 1050 })
  const missing = page.waitForResponse((response: Response) => apiPath(response) === loadPath)
  await page.goto(`${path}?dataset=offline_missing`)
  const unavailable = await missing
  expect(unavailable.status(), await unavailable.text()).toBe(400)
  await expect(page.getByRole('alert').filter({ hasText: 'offline_missing' }).first()).toBeVisible()
  await expect(page.getByRole('table', { name: 'Selected datasets' })).toContainText('offline_missing')
  await expect(page.getByTestId('launch-scenario-btn')).toBeDisabled()
  expect(preparations).toBe(5)
  page.off('request', countPreparation)
}

function datasetName(prefix: string, testInfo: TestInfo): string {
  const project = testInfo.project.name.replace(/[^A-Za-z0-9]+/g, '_')
  return `${prefix}_${project}_repeat${testInfo.repeatEachIndex}_retry${testInfo.retry}`
}

function apiPath(response: Response): string {
  return new URL(response.url()).pathname
}

/** Waits for the preflight of a request that matches the form's final state. */
function nextPreflight(
  page: Page,
  matches: (body: PromptGenerationCreateRequest) => boolean,
): Promise<Response> {
  return page.waitForResponse((response: Response) => apiPath(response) === '/api/prompt-generations/preflight'
    && response.request().method() === 'POST'
    && matches(response.request().postDataJSON() as PromptGenerationCreateRequest))
}

async function expectEstimate(page: Page, response: Response): Promise<void> {
  expect(response.status(), await response.text()).toBe(200)
  const preflight: PromptGenerationPreflightResponse = await response.json()
  expect(preflight).toMatchObject({ valid: true, errors: [], dataset_name_available: true })
  if (!preflight.estimate) throw new Error('The preflight returned no estimate.')
  const card = page.getByRole('region', { name: 'Estimate', exact: true })
  await expect(card.getByTestId('estimate-calls'))
    .toHaveText(formatRange(preflight.estimate.min_calls, preflight.estimate.max_calls))
  await expect(card.getByTestId('estimate-tokens'))
    .toHaveText(formatRange(preflight.estimate.low_tokens, preflight.estimate.high_tokens))
  await expect(card.getByText(/^Approximate; excludes target-side usage/)).toBeVisible()
}

async function startGeneration(page: Page): Promise<PromptGenerationDetail> {
  const generate = page.getByRole('button', { name: 'Generate', exact: true })
  await expect(generate).toBeEnabled()
  const creating = page.waitForResponse((response: Response) =>
    apiPath(response) === '/api/prompt-generations' && response.request().method() === 'POST')
  await generate.click()
  const created = await creating
  expect(created.status(), await created.text()).toBe(201)
  await expect(page).toHaveURL(GENERATION_PATH)
  return created.json()
}

function summaryValue(summary: Locator, label: string): Locator {
  return summary.getByRole('term').filter({ hasText: new RegExp(`^${label}$`) }).locator('..').getByRole('definition')
}

function statusBadge(page: Page): Locator {
  return page.getByTestId('generation-status-badge')
}

async function chooseGenerator(page: Page, displayName: string): Promise<void> {
  await page.getByRole('button', { name: 'New generation', exact: true }).click()
  await page.getByRole('menuitem', { name: displayName, exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: `New ${displayName}` })).toBeVisible()
}

test.beforeEach(async ({ page }: JourneyContext): Promise<void> => {
  const errors: string[] = []
  pageErrors.set(page, errors)
  page.on('pageerror', (error: Error) => { errors.push(error.message) })
})

test.afterEach(async ({ page }: JourneyContext): Promise<void> => {
  expect(pageErrors.get(page)).toEqual([])
})

test('generates a reviewed ASSERT dataset, opens its scenario preselected, and launches it from an empty form', async ({ page }: JourneyContext, testInfo: TestInfo): Promise<void> => {
  test.setTimeout(120_000)
  await verifyAutomaticPreparation(page, testInfo)
  const name = datasetName('returns_privacy', testInfo)
  await page.goto('/datasets')
  await expect(page.getByRole('heading', { level: 1, name: 'Datasets' })).toBeVisible()
  await page.getByRole('button', { name: 'Generate dataset', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Generate datasets' })).toBeVisible()
  await chooseGenerator(page, 'ASSERT policy dataset')
  await expect(page.getByRole('checkbox', { name: /^Include tester instructions/ })).toBeChecked()

  await page.getByRole('textbox', { name: /^Behavior name/ }).fill('returns_privacy')
  await page.getByRole('textbox', { name: /^Policy text/ }).fill(POLICY)
  const groups = page.getByRole('spinbutton', { name: /^Seed groups/ })
  await groups.fill('4')
  await page.getByRole('button', { name: 'Add dimension', exact: true }).click()
  await page.getByRole('textbox', { name: /^Dimension 1 name/ }).fill('channel')
  await page.getByRole('textbox', { name: /^Dimension 1 description/ }).fill('Where the customer writes from.')
  // foundry.assert_policy's default technique needs the tester instructions this adds to each group.
  await page.getByRole('checkbox', { name: /^Include tester instructions/ }).check()
  await page.getByRole('combobox', { name: /^Generation target/ }).selectOption(TARGET)
  await page.getByRole('checkbox', { name: /^Pause for review after/ }).check()
  const preflight = nextPreflight(page, (body: PromptGenerationCreateRequest) => body.dataset_name === name
    && body.pause_for_review && body.targets.generation === TARGET
    && body.request.include_adversarial_prompt === true
    && body.request.group_count === 4 && body.request.behavior_description === POLICY)
  await page.getByRole('textbox', { name: /^Dataset name/ }).fill(name)
  await expectEstimate(page, await preflight)

  const generation = await startGeneration(page)
  expect(generation).toMatchObject({ generator: 'assert', dataset_name: name, pause_for_review: true })
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
  await expect(statusBadge(page)).toHaveText('Review needed', { timeout: JOB_TIMEOUT_MS })
  await expect(page.getByTestId('generation-stage-dimensions')).toContainText('(done)')

  const editor = page.getByTestId('assert-review-editor')
  await expect(editor.getByTestId('assert-review-systematization')).toContainText('Published policy questions')
  await expect(editor.getByText('2 categories: 1 permissible, 1 impermissible')).toBeVisible()
  await expect(editor.getByRole('textbox', { name: 'channel level 3 name' })).toHaveValue('level_2')
  const definition = editor.getByRole('textbox', { name: /^Category 1 definition/ })
  await expect(definition).toHaveValue('Explains the published return policy.')
  await definition.fill(EDITED_DEFINITION)
  const saving = page.waitForResponse((response: Response) =>
    apiPath(response) === `/api/prompt-generations/${generation.id}/review` && response.request().method() === 'PUT')
  await editor.getByRole('button', { name: 'Save changes', exact: true }).click()
  const saved = await saving
  expect(saved.status(), await saved.text()).toBe(200)
  const review: PromptGenerationReview = await saved.json()
  expect(JSON.stringify(review.payload)).toContain(EDITED_DEFINITION)
  await expect(editor.getByText('Changes saved.', { exact: true })).toBeVisible()
  await expect(editor.getByRole('region', { name: 'Remaining work' }).getByText('Model calls', { exact: true }))
    .toBeVisible()
  await editor.getByRole('button', { name: 'Continue generation', exact: true }).click()

  await expect(statusBadge(page)).toHaveText('Completed', { timeout: JOB_TIMEOUT_MS })
  const complete = page.getByTestId('generation-complete')
  const summary = complete.getByTestId('assert-generation-summary')
  await expect(summaryValue(summary, 'Seed groups')).toHaveText('4')
  const categoryFact = summaryValue(summary, 'Categories')
  await expect(categoryFact.getByTestId('summary-fact-value')).toHaveText('2')
  await expect(categoryFact.getByTestId('summary-fact-detail')).toHaveText('1 permissible, 1 impermissible')
  await expect(summary.getByText('Reviewed and edited: taxonomy', { exact: true })).toBeVisible()
  const categories = summary.getByRole('table', { name: 'Seed groups per category' })
  await expect(categories.getByRole('row').filter({ hasText: 'published_policy' })).toContainText('Permissible')
  await expect(categories.getByRole('row').filter({ hasText: 'private_disclosure' })).toContainText('Impermissible')

  const runScenario = complete.getByRole('link', { name: `Run ${FOLLOW_UP_SCENARIO}` })
  await expect(runScenario).toHaveAttribute(
    'href', `/scanner/launch/${FOLLOW_UP_SCENARIO}?dataset=${encodeURIComponent(name)}`,
  )
  const eligibilityPath = `/api/scenarios/catalog/${FOLLOW_UP_SCENARIO}/datasets`
  let eligibilityLoads = 0
  page.on('request', (request: Request) => {
    if (new URL(request.url()).pathname === eligibilityPath) eligibilityLoads += 1
  })
  const loadingChoices = page.waitForResponse((response: Response) => apiPath(response) === eligibilityPath)
  await runScenario.click()
  const eligible = await loadingChoices
  expect(eligible.status(), await eligible.text()).toBe(200)
  const choices: DatasetListResponse = await eligible.json()
  expect(choices.items.find((item) => item.name === name)?.logical_examples).toBe(4)
  expect(choices.items.some((item) => item.name === 'offline_plain')).toBe(false)
  await expect(page.getByRole('heading', { level: 2, name: FOLLOW_UP_SCENARIO })).toBeVisible({ timeout: JOB_TIMEOUT_MS })
  const selected = page.getByRole('table', { name: 'Selected datasets' })
  await expect(selected.getByRole('row').filter({ hasText: `${name} (generated)` })).toBeVisible({ timeout: CATALOG_TIMEOUT_MS })
  await expect(page.getByTestId('requested-datasets-warning')).toHaveCount(0)
  const actions = page.getByRole('group', { name: 'Dataset actions' })
  await expect(actions.getByRole('link', { name: 'Browse datasets', exact: true })).toBeVisible()
  await expect(actions.getByRole('link', { name: 'Generate datasets', exact: true }))
    .toHaveAttribute('href', '/datasets/generate/new?generator=assert')
  await actions.getByRole('link', { name: 'Browse datasets', exact: true }).focus()
  await page.keyboard.press('Tab')
  await expect(actions.getByRole('link', { name: 'Generate datasets', exact: true })).toBeFocused()
  for (const field of [
    'judgment_mode', 'target_system_prompt', 'judge_presets', 'judge_preset_files',
    'disabled_judge_dimensions', 'judge_samples', 'objective_dimension', 'trace_source', 'trace_requirement',
  ]) await expect(page.getByTestId(`scenario-param-${field}`)).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Objective scorer', exact: true })).toHaveCount(0)
  await expect(page.getByText('Exact count is calculated when the scan starts.', { exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
  for (const [label, width, height] of [['desktop', 1440, 1050], ['mobile', 390, 844]] as const) {
    await page.setViewportSize({ width, height })
    await page.getByRole('region', { name: 'Datasets', exact: true }).scrollIntoViewIfNeeded()
    await expect(actions.getByRole('link', { name: 'Generate datasets', exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`assert-launch-${label}.png`), fullPage: true, animations: 'disabled' })
  }
  await page.setViewportSize({ width: 1440, height: 1050 })
  expect(eligibilityLoads).toBe(1)
  await expect(page.getByRole('switch', { name: 'Show incompatible' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Reset datasets|Load and validate/ })).toHaveCount(0)
  const picker = page.getByRole('combobox', { name: 'Add dataset', exact: true })
  await picker.fill('offline_plain')
  await expect(page.getByRole('option', { name: /^offline_plain/ })).toHaveCount(0)
  await picker.press('Escape')
  await expect(selected.getByRole('row').filter({ hasText: `${name} (generated)` })).toBeVisible()
  expect(eligibilityLoads).toBe(1)

  // Without a link, the scenario starts with no dataset and the generated one can be chosen.
  await page.goto(`/scanner/launch/${FOLLOW_UP_SCENARIO}`)
  await expect(page.getByText('No dataset selected.', { exact: true })).toBeVisible({ timeout: CATALOG_TIMEOUT_MS })
  await expect(page.getByText('This scenario has no named datasets.')).toHaveCount(0)
  const estimating = page.waitForResponse((response: Response) =>
    apiPath(response) === `/api/scenarios/catalog/${FOLLOW_UP_SCENARIO}/estimate`
    && JSON.stringify(response.request().postDataJSON()?.datasets?.sources ?? []) === JSON.stringify([{ name }]))
  await page.getByRole('combobox', { name: 'Add dataset', exact: true }).click()
  await page.getByRole('option', { name: `${name} (generated)`, exact: true }).click()
  await expect(selected.getByRole('row').filter({ hasText: `${name} (generated)` })).toBeVisible()
  const estimate = await estimating
  expect(estimate.status(), await estimate.text()).toBe(200)
  const resizing = page.waitForResponse((response: Response) =>
    apiPath(response) === `/api/scenarios/catalog/${FOLLOW_UP_SCENARIO}/estimate`
    && response.request().postDataJSON()?.scenario_params?.max_turns === 2)
  await page.getByTestId('scenario-param-max_turns').fill('2')
  expect((await resizing).status()).toBe(200)
  expect(eligibilityLoads).toBe(2)

  await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
  // The adversarial role defaults to the fixture's adversarial_chat; an explicit choice must win.
  await page.getByRole('combobox', { name: 'Adversarial Target', exact: true }).selectOption(TARGET)
  await expect(page.getByTestId('launch-scenario-btn')).toBeEnabled()
  await page.getByTestId('launch-scenario-btn').click()
  const starting = page.waitForResponse((response: Response) =>
    apiPath(response) === '/api/scenarios/runs' && response.request().method() === 'POST')
  await page.getByTestId('confirm-launch-scenario-btn').click()
  const started = await starting
  expect(started.ok(), await started.text()).toBeTruthy()
  expect(started.request().postDataJSON()).toMatchObject({
    scenario_name: FOLLOW_UP_SCENARIO,
    target_name: TARGET,
    adversarial_target_name: TARGET,
    datasets: { sources: [{ name }] },
  })
  expect(started.request().postDataJSON().scenario_params).not.toEqual(expect.objectContaining({
    judge_samples: expect.anything(),
  }))
  const run: ScenarioRunSummary = await started.json()
  const compatibilityId = started.request().headers()['pyrit-compatibility-id']
  if (!compatibilityId) throw new Error('The scanner launch did not send its compatibility identity.')
  await expect.poll(async () => {
    const response = await page.request.get(`/api/scenarios/runs/${encodeURIComponent(run.scenario_result_id)}`, {
      headers: { 'PyRIT-Compatibility-ID': compatibilityId },
    })
    expect(response.status(), await response.text()).toBe(200)
    const current: ScenarioRunSummary = await response.json()
    if (current.status === 'FAILED' || current.status === 'CANCELLED') {
      throw new Error(`Offline scenario ended with ${current.status}: ${current.error}`)
    }
    if (current.status === 'COMPLETED') {
      expect(current.total_attacks).toBe(8)
      expect(current.completed_attacks).toBe(8)
    }
    return current.status
  }, { timeout: JOB_TIMEOUT_MS }).toBe('COMPLETED')
})

test('generates an objective dataset that appears in the catalog', async ({ page }: JourneyContext, testInfo: TestInfo): Promise<void> => {
  const name = datasetName('shopper_objectives', testInfo)
  await page.goto('/datasets/generate')
  await chooseGenerator(page, 'Objective dataset')
  await expect(page.getByRole('checkbox', { name: /^Pause for review/ })).toHaveCount(0)

  await page.getByRole('textbox', { name: /^Instructions/ })
    .fill("Ways a shopper might try to obtain another customer's order details.")
  await page.getByRole('spinbutton', { name: /^Number of objectives/ }).fill('3')
  await page.getByRole('textbox', { name: /^Harm categories/ }).fill('privacy')
  await page.getByRole('combobox', { name: /^Generation target/ }).selectOption(TARGET)
  const preflight = nextPreflight(page, (body: PromptGenerationCreateRequest) =>
    body.dataset_name === name && body.request.count === 3 && body.targets.generation === TARGET)
  await page.getByRole('textbox', { name: /^Dataset name/ }).fill(name)
  await expectEstimate(page, await preflight)

  const generation = await startGeneration(page)
  expect(generation).toMatchObject({
    generator: 'target_objective',
    request: { count: 3, harm_categories: ['privacy'] },
  })
  await expect(statusBadge(page)).toHaveText('Completed', { timeout: JOB_TIMEOUT_MS })
  const complete = page.getByTestId('generation-complete')
  await expect(complete.getByTestId('target-objective-summary')).toHaveText('Objectives3')
  await expect(complete.getByRole('link', { name: /^Run / })).toHaveCount(0)
  await complete.getByRole('link', { name: 'Browse dataset', exact: true }).click()
  await expect(page.getByRole('heading', { level: 1, name })).toBeVisible({ timeout: CATALOG_TIMEOUT_MS })

  await page.goto('/datasets')
  await page.getByRole('textbox', { name: 'Search datasets', exact: true }).fill(name)
  const card = page.getByRole('article', { name })
  await expect(card.getByRole('link', { name, exact: true })).toBeVisible({ timeout: CATALOG_TIMEOUT_MS })
  await expect(card.getByRole('term').filter({ hasText: 'Objectives' }).locator('..')).toContainText('3')
})

test('fills the policy from a bundled behavior template', async ({ page }: JourneyContext): Promise<void> => {
  await page.goto('/datasets/generate/new?generator=assert')
  const picker = page.getByRole('combobox', { name: /^Start from template/ })
  await picker.click()
  await picker.pressSequentially('prompt_inj')
  await page.getByRole('option', { name: /^prompt_injection/ }).click()

  await expect(page.getByRole('textbox', { name: /^Behavior name/ })).toHaveValue('prompt_injection')
  await expect(page.getByRole('textbox', { name: /^Policy text/ })).toHaveValue(/^# Prompt Injection/)
  await expect(page.getByRole('textbox', { name: /^Dataset name/ })).toHaveValue('prompt_injection')
  await expect(page.getByTestId('assert-template-details'))
    .toContainText('Detect vulnerability to adversarial prompt override attempts.')

  await page.goto('/datasets/generate/new?generator=assert&template=prompt_injection')
  await expect(page.getByRole('textbox', { name: /^Policy text/ })).toHaveValue(/^# Prompt Injection/)
})
