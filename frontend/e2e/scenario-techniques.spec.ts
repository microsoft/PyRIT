import { execFileSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { expect, test, type Locator, type Page, type Response, type TestInfo } from '@playwright/test'

import type {
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioPreset,
  ScenarioPresetResponse,
  ScenarioProgressResult,
  ScenarioRunPlanAtomicGroup,
  ScenarioRunProgress,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunSizeEstimateResponse,
  ScenarioRunSummary,
} from '../src/types'
import { presetEditorRoutePath, presetScannerRoutePath, PRESETS_ROUTE } from '../src/components/ScenarioPresets/presetRoutes'
import { scenarioRunRoutePath } from '../src/utils/routeParams'
import { compatibilityHeaders, getCompatibilityId } from './_compatibility'

const SCENARIO = 'technique_acceptance'
const TECHNIQUES = ['offline_plain', 'offline_base64', 'offline_rot13']
const TARGET = 'technique_offline'
const ALTERNATE_TARGET = 'technique_offline_alternate'
const FAILURE = 'Deliberate offline estimate failure. Try another estimate.'
const VIEWPORTS = [
  { name: 'desktop', viewport: { width: 1440, height: 1050 }, touch: false },
  { name: 'narrow', viewport: { width: 390, height: 844 }, touch: true },
]

type EstimateAction = 'reset' | 'status' | 'hold' | 'release' | 'fail'
interface EstimateControlState {
  waiting: boolean
  isolation: {
    fixture_root: string
    database_path: string
    configuration_root: string
    preset_source: string
  }
}

async function control(page: Page, action: EstimateAction): Promise<EstimateControlState> {
  const response = await page.request.post('/api/e2e/techniques/estimate-control', {
    headers: compatibilityHeaders(), data: { action },
  })
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

function nextEstimate(page: Page, scenario: string = SCENARIO): Promise<Response> {
  return page.waitForResponse((response: Response): boolean =>
    new URL(response.url()).pathname === `/api/scenarios/catalog/${scenario}/estimate`
    && response.request().method() === 'POST',
  )
}

async function readEstimate(response: Response, count: number, baseline: boolean): Promise<ScenarioRunSizeEstimateResponse> {
  expect(response.status(), await response.text()).toBe(200)
  const estimate: ScenarioRunSizeEstimateResponse = await response.json()
  expect(estimate.status).toBe('approximate')
  expect(estimate.estimated_attack_count).toBe(count)
  expect(estimate.components.some(
    (component: ScenarioRunSizeEstimateResponse['components'][number]): boolean => component.is_baseline,
  )).toBe(baseline)
  return estimate
}

async function createPreset(page: Page, preset: ScenarioPreset): Promise<void> {
  const response = await page.request.post('/api/scenario-presets', { headers: compatibilityHeaders(), data: preset })
  expect(response.status(), await response.text()).toBe(201)
  const saved: ScenarioPresetResponse = await response.json()
  expect(saved.issues).toEqual([])
  expect(saved.origin).toBe('stored')
}

async function readPreset(page: Page, name: string): Promise<ScenarioPresetResponse> {
  const response = await page.request.get(`/api/scenario-presets/${name}`, { headers: compatibilityHeaders() })
  expect(response.status(), await response.text()).toBe(200)
  return response.json()
}

async function expectSelection(page: Page, techniques: string[], baseline: boolean): Promise<void> {
  const group = page.getByRole('group', { name: 'Techniques', exact: true })
  for (const name of TECHNIQUES) {
    if (techniques.includes(name)) await expect(group.getByRole('checkbox', { name, exact: true })).toBeChecked()
    else await expect(group.getByRole('checkbox', { name, exact: true })).not.toBeChecked()
  }
  if (baseline) await expect(group.getByRole('checkbox', { name: 'baseline', exact: true })).toBeChecked()
  else await expect(group.getByRole('checkbox', { name: 'baseline', exact: true })).not.toBeChecked()
}

async function tabTo(page: Page, target: Locator): Promise<void> {
  for (let index = 0; index < 80; index += 1) {
    await page.keyboard.press('Tab')
    if (await target.evaluate((element: Element): boolean => element === document.activeElement)) return
  }
  await expect(target, 'The control must be reachable with Tab.').toBeFocused()
}

async function expectUnclipped(page: Page, target: Locator): Promise<void> {
  await target.evaluate((element: Element): void => {
    element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' })
  })
  await expect(target).toBeVisible()
  const bounds = await target.boundingBox()
  expect(bounds).not.toBeNull()
  if (!bounds) throw new Error('The visible control has no browser bounds.')
  const viewport = page.viewportSize()
  if (!viewport) throw new Error('The test requires a fixed viewport.')
  expect(bounds.x).toBeGreaterThanOrEqual(0)
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(viewport.width)
  expect(bounds.y).toBeGreaterThanOrEqual(0)
  expect(bounds.y + bounds.height).toBeLessThanOrEqual(viewport.height)
}

async function capture(page: Page, testInfo: TestInfo, viewName: string, state: string): Promise<void> {
  const name = `${viewName}-${state}`
  const directory = process.env.PYRIT_TECHNIQUES_SCREENSHOT_DIR ?? testInfo.outputDir
  await mkdir(directory, { recursive: true })
  const screenshot = path.join(directory, `${name}.png`)
  const head = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim()
  const status = execFileSync('git', ['status', '--porcelain'], { encoding: 'utf8' }).trim()
  expect(getCompatibilityId()).toMatch(new RegExp(`\\+g${head}$`))
  const provenance = {
    state, test: testInfo.titlePath, retry: testInfo.retry, viewport: page.viewportSize(), url: page.url(),
    captured_at: new Date().toISOString(), source_head: head, worktree_status: status,
    compatibility_id: getCompatibilityId(),
    fixture: 'scenario_techniques_backend.py; private SQLite and preset storage; local target and SubStringScorer',
    isolation: (await control(page, 'status')).isolation,
    fault_injection: state.includes('loading') ? 'One configured estimate deliberately held before the real service.'
      : state.includes('error') ? 'One configured estimate deliberately returned HTTP 503.' : null,
  }
  await page.screenshot({ path: screenshot, animations: 'disabled' })
  await writeFile(path.join(directory, `${name}.json`), JSON.stringify(provenance, null, 2))
  await testInfo.attach(state, { path: screenshot, contentType: 'image/png' })
  await testInfo.attach(`${state}-provenance`, { body: JSON.stringify(provenance, null, 2), contentType: 'application/json' })
}

for (const view of VIEWPORTS) {
  test.describe(view.name, (): void => {
    test.use({ viewport: view.viewport, hasTouch: view.touch, isMobile: view.touch })

    test.beforeEach(async ({ page }: { page: Page }): Promise<void> => {
      const { isolation } = await control(page, 'reset')
      expect(path.relative(path.resolve('..'), isolation.fixture_root)).toMatch(/^\.pyrit_cache[\\/]/)
      for (const location of [isolation.database_path, isolation.configuration_root, isolation.preset_source]) {
        const relative = path.relative(isolation.fixture_root, location)
        expect(path.isAbsolute(relative)).toBe(false)
        expect(relative.split(path.sep)[0]).not.toBe('..')
      }
    })

    test.afterEach(async ({ page }: { page: Page }): Promise<void> => {
      await control(page, 'reset')
    })

    test('edits a saved preset and preserves its selection through real estimates and execution', async (
      { page }: { page: Page }, testInfo: TestInfo,
    ): Promise<void> => {
      const name = `techniques_${view.name}_${testInfo.retry}`
      const errors: string[] = []
      page.on('pageerror', (error: Error): void => { errors.push(error.message) })
      await createPreset(page, {
        name, scenario_name: SCENARIO, techniques: ['offline_plain'], include_baseline: true,
        description: 'Original local acceptance preset.',
      })

      await page.goto(PRESETS_ROUTE)
      await page.getByRole('region', { name: 'Default Labels' }).getByRole('button', { name: 'Clear', exact: true }).click()
      await page.getByRole('link', { name, exact: true }).click()
      await expect(page).toHaveURL(presetEditorRoutePath(name))
      await expect(page.getByTestId('scenario-preset-editor')).toBeVisible({ timeout: 30_000 })
      await expectSelection(page, ['offline_plain'], true)
      await page.getByRole('checkbox', { name: 'offline_plain', exact: true }).uncheck()
      await page.getByRole('checkbox', { name: 'offline_base64', exact: true }).check()
      await page.getByRole('checkbox', { name: 'baseline', exact: true }).uncheck()
      await page.getByRole('textbox', { name: 'Description', exact: true }).fill('Edited local technique selection.')
      await page.getByRole('group', { name: 'Techniques', exact: true }).scrollIntoViewIfNeeded()
      await capture(page, testInfo, view.name, 'preset-edited')
      await page.getByRole('button', { name: 'Save preset', exact: true }).click()
      await expect(page).toHaveURL(PRESETS_ROUTE)
      const saved = await readPreset(page, name)
      expect(saved.preset).toMatchObject({
        techniques: ['offline_base64'], include_baseline: false, description: 'Edited local technique selection.',
      })

      await page.goto('/scanner/launch')
      await page.getByRole('link', { name, exact: true }).click()
      await expect(page).toHaveURL(presetScannerRoutePath(SCENARIO, name))
      await expect(page.getByTestId('preset-seed-banner')).toContainText(name)
      await expectSelection(page, ['offline_base64'], false)
      const targeted = nextEstimate(page)
      await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
      const targetedResponse = await targeted
      expect(targetedResponse.request().postDataJSON()).toMatchObject({
        techniques: ['offline_base64'], include_baseline: false, target_name: TARGET,
      })
      await readEstimate(targetedResponse, 1, false)
      await tabTo(page, page.getByRole('checkbox', { name: 'offline_rot13', exact: true }))
      const selected = nextEstimate(page)
      await page.keyboard.press('Space')
      const selectedResponse = await selected
      expect(selectedResponse.request().postDataJSON()).toMatchObject({
        techniques: ['offline_base64', 'offline_rot13'], include_baseline: false,
      })
      await readEstimate(selectedResponse, 2, false)
      await expectSelection(page, ['offline_base64', 'offline_rot13'], false)
      const plain = page.getByRole('checkbox', { name: 'offline_plain', exact: true })
      await expect(plain).toBeDisabled()
      await expect(plain).toHaveAccessibleDescription(/at most 2.*[Cc]lear a selected technique/)

      await control(page, 'hold')
      const held = nextEstimate(page)
      await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(ALTERNATE_TARGET)
      await expect.poll(async (): Promise<boolean> => (await control(page, 'status')).waiting).toBe(true)
      await expectSelection(page, ['offline_base64', 'offline_rot13'], false)
      const estimate = page.getByTestId('run-estimate')
      await expect(estimate.getByRole('status')).toContainText('Updating estimate')
      await estimate.scrollIntoViewIfNeeded()
      await capture(page, testInfo, view.name, 'estimate-loading')
      await control(page, 'release')
      const heldResponse = await held
      expect(heldResponse.request().postDataJSON()).toMatchObject({
        techniques: ['offline_base64', 'offline_rot13'], include_baseline: false, target_name: ALTERNATE_TARGET,
      })
      await readEstimate(heldResponse, 2, false)
      await expectSelection(page, ['offline_base64', 'offline_rot13'], false)

      await control(page, 'fail')
      const failed = nextEstimate(page)
      await page.getByRole('checkbox', { name: 'baseline', exact: true }).check()
      const failedResponse = await failed
      expect(failedResponse.status()).toBe(503)
      expect(failedResponse.request().postDataJSON()).toMatchObject({
        techniques: ['offline_base64', 'offline_rot13'], include_baseline: true, target_name: ALTERNATE_TARGET,
      })
      await expect(estimate.getByRole('status')).toContainText(FAILURE)
      await expectSelection(page, ['offline_base64', 'offline_rot13'], true)
      await estimate.scrollIntoViewIfNeeded()
      await capture(page, testInfo, view.name, 'estimate-error')

      const refreshed = nextEstimate(page)
      await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
      const refreshedResponse = await refreshed
      await readEstimate(refreshedResponse, 3, true)
      const estimateRequest: ScenarioRunSizeEstimateRequest = refreshedResponse.request().postDataJSON()
      expect(estimateRequest).toMatchObject({
        techniques: ['offline_base64', 'offline_rot13'], include_baseline: true, target_name: TARGET,
      })
      await expectSelection(page, ['offline_base64', 'offline_rot13'], true)
      const baseline = page.getByRole('checkbox', { name: 'baseline', exact: true })
      await tabTo(page, baseline)
      await expectUnclipped(page, baseline)
      await expect(baseline).toBeFocused()
      expect(await baseline.evaluate((element: Element): boolean => element.matches(':focus-visible'))).toBe(true)
      await capture(page, testInfo, view.name, 'selection-keyboard-focus')

      const launch = page.getByRole('button', { name: 'Launch scan', exact: true })
      await expect(launch).toBeEnabled()
      await tabTo(page, launch)
      await expectUnclipped(page, launch)
      await page.keyboard.press('Enter')
      const preview = page.getByRole('dialog', { name: 'Run preview' })
      await expect(preview).toHaveCSS('opacity', '1')
      await expect(preview.getByText('offline_base64', { exact: true })).toBeVisible()
      await expect(preview.getByText('offline_rot13', { exact: true })).toBeVisible()
      await expect(preview.getByText('baseline', { exact: true })).toBeVisible()
      await expect(preview.getByText('offline_plain', { exact: true })).toHaveCount(0)
      await page.keyboard.press('Escape')
      await expect(preview).toHaveCount(0)
      await expect(launch).toBeFocused()
      await page.keyboard.press('Enter')
      await expect(preview).toHaveCSS('opacity', '1')
      const confirm = preview.getByRole('button', { name: 'Launch scan', exact: true })
      await tabTo(page, confirm)
      await expectUnclipped(page, confirm)
      if (view.touch) {
        expect(await page.evaluate((): boolean => matchMedia('(pointer: coarse)').matches)).toBe(true)
        for (const action of [confirm, preview.getByRole('button', { name: 'Cancel', exact: true })]) {
          await expect.poll(async (): Promise<number | undefined> => (await action.boundingBox())?.height)
            .toBeGreaterThanOrEqual(44)
          await expectUnclipped(page, action)
        }
      }
      await expect(confirm).toBeFocused()
      await capture(page, testInfo, view.name, 'run-preview')
      const launched = page.waitForResponse((response: Response): boolean =>
        new URL(response.url()).pathname === '/api/scenarios/runs' && response.request().method() === 'POST',
      )
      await page.keyboard.press('Enter')
      const launchResponse = await launched
      expect(launchResponse.status(), await launchResponse.text()).toBe(202)
      const request: RunScenarioRequest = launchResponse.request().postDataJSON()
      expect(request).toMatchObject({ ...estimateRequest, scenario_name: SCENARIO })
      const summary: ScenarioRunSummary = await launchResponse.json()
      const runId = summary.scenario_result_id
      await expect(page).toHaveURL(scenarioRunRoutePath(runId))
      await expect(page.getByTestId('run-state-badge')).toHaveText('Completed', { timeout: 30_000 })
      const configuration = await page.request.get(`/api/scenarios/runs/${runId}/launch-configuration`, {
        headers: compatibilityHeaders(),
      })
      expect(configuration.status()).toBe(200)
      const savedConfiguration: RunScenarioRequest = await configuration.json()
      expect(savedConfiguration).toMatchObject({ ...request })
      const progressResponse = await page.request.get(`/api/scenarios/runs/${runId}/progress`, {
        headers: compatibilityHeaders(),
      })
      expect(progressResponse.status()).toBe(200)
      const progress: ScenarioRunProgress = await progressResponse.json()
      expect(progress.plan_complete).toBe(true)
      expect(progress.plan?.atomic_groups.filter(
        (group: ScenarioRunPlanAtomicGroup): boolean => group.kind === 'baseline',
      )).toHaveLength(1)
      expect(progress.plan?.atomic_groups.filter((group: ScenarioRunPlanAtomicGroup): boolean => group.kind !== 'baseline')
        .map((group: ScenarioRunPlanAtomicGroup): string | null | undefined => group.technique_name).sort())
        .toEqual(['offline_base64', 'offline_rot13'])
      expect(progress.results).toHaveLength(3)
      expect(progress.results.every((result: ScenarioProgressResult): boolean => result.outcome === 'success')).toBe(true)
      const executionEvidence = JSON.stringify({
        source_head: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
        compatibility_id: getCompatibilityId(), scenario_result_id: runId, viewport: page.viewportSize(),
        estimate_request: estimateRequest, launch_request: request, saved_configuration: savedConfiguration, progress,
        estimate_requests: {
          preset_target: targetedResponse.request().postDataJSON(),
          edited_selection: selectedResponse.request().postDataJSON(),
          held: heldResponse.request().postDataJSON(),
          failed: failedResponse.request().postDataJSON(),
          refreshed: estimateRequest,
        },
      }, null, 2)
      const evidencePath = path.join(
        process.env.PYRIT_TECHNIQUES_SCREENSHOT_DIR ?? testInfo.outputDir, `${view.name}-execution-evidence.json`,
      )
      await writeFile(evidencePath, executionEvidence)
      await testInfo.attach('execution-evidence', { path: evidencePath, contentType: 'application/json' })
      await capture(page, testInfo, view.name, 'completed-run')
      expect((await readPreset(page, name)).preset).toEqual(saved.preset)
      expect(errors).toEqual([])
    })

    test('uses scenario-relative defaults and explains a forbidden baseline', async (
      { page }: { page: Page }, testInfo: TestInfo,
    ): Promise<void> => {
      await page.goto('/scanner/launch')
      for (const [scenarioName, technique, policy, baseline] of [
        [SCENARIO, 'offline_plain', 'enabled', true],
        ['technique_no_baseline', 'offline_rot13', 'forbidden', false],
        ['technique_optional_baseline', 'offline_base64', 'disabled', false],
      ] as const) {
        const metadataResponse = await page.request.get(`/api/scenarios/catalog/${scenarioName}`, {
          headers: compatibilityHeaders(),
        })
        expect(metadataResponse.status(), await metadataResponse.text()).toBe(200)
        const metadata: RegisteredScenario = await metadataResponse.json()
        expect(metadata.default_techniques).toEqual([technique])
        expect(metadata.baseline_policy).toBe(policy)
        expect(metadata.include_baseline_by_default).toBe(baseline)
        await page.getByRole('link', { name: scenarioName, exact: true }).click()
        await expect(page).toHaveURL(`/scanner/launch/${scenarioName}`)
        await expectSelection(page, metadata.default_techniques, metadata.include_baseline_by_default)
        const checkbox = page.getByRole('checkbox', { name: 'baseline', exact: true })
        if (policy === 'forbidden') {
          await expect(checkbox).toBeDisabled()
          await expect(checkbox).toHaveAccessibleDescription('This scenario does not support a baseline comparison.')
          await expect(page.getByText('This scenario does not support a baseline comparison.')).toBeVisible()
          await expectUnclipped(page, checkbox)
          await capture(page, testInfo, view.name, 'baseline-unavailable')
        } else {
          await expect(checkbox).toBeEnabled()
          await tabTo(page, checkbox)
          await expectUnclipped(page, checkbox)
          await capture(page, testInfo, view.name, `baseline-${policy}`)
        }
        await page.getByRole('checkbox', { name: technique, exact: true }).uncheck()
        await page.getByRole('checkbox', {
          name: technique === 'offline_plain' ? 'offline_base64' : 'offline_plain', exact: true,
        }).check()
        if (policy !== 'forbidden') await checkbox.setChecked(!baseline)
        await page.getByRole('link', { name: 'Back to scanners', exact: true }).click()
      }
    })

    test('keeps an unpinned preset tracking scenario defaults after a real description edit', async (
      { page }: { page: Page }, testInfo: TestInfo,
    ): Promise<void> => {
      const name = `techniques_defaults_${view.name}_${testInfo.retry}`
      await createPreset(page, { name, scenario_name: 'technique_optional_baseline' })
      await page.goto(presetEditorRoutePath(name))
      await expectSelection(page, ['offline_base64'], false)
      await page.getByRole('textbox', { name: 'Description', exact: true }).fill('Keep scenario-owned defaults.')
      await page.getByRole('button', { name: 'Save preset', exact: true }).click()
      await expect(page).toHaveURL(PRESETS_ROUTE)
      const saved = await readPreset(page, name)
      expect(saved.preset.techniques ?? null).toBeNull()
      expect(saved.preset.include_baseline ?? null).toBeNull()
      await page.goto(presetScannerRoutePath('technique_optional_baseline', name))
      await expectSelection(page, ['offline_base64'], false)
      const estimated = nextEstimate(page, 'technique_optional_baseline')
      await page.getByRole('combobox', { name: 'Objective Target', exact: true }).selectOption(TARGET)
      await readEstimate(await estimated, 1, false)
    })

    test('expands a saved aggregate using the real scenario catalog without selecting unrelated techniques', async (
      { page }: { page: Page }, testInfo: TestInfo,
    ): Promise<void> => {
      const name = `techniques_aggregate_${view.name}_${testInfo.retry}`
      await createPreset(page, {
        name, scenario_name: SCENARIO, techniques: ['encoded'], include_baseline: false,
      })
      const estimated = nextEstimate(page)
      await page.goto(presetScannerRoutePath(SCENARIO, name))
      await expectSelection(page, ['offline_base64', 'offline_rot13'], false)
      await readEstimate(await estimated, 2, false)
      expect((await readPreset(page, name)).preset.techniques).toEqual(['encoded'])
    })
  })
}
