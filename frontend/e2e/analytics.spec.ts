import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { expect, test } from '@playwright/test'
import type { Page, Request, Response } from '@playwright/test'

import type {
  AttackAnalyticsCell, AttackAnalyticsFilter, AttackAnalyticsOption, AttackAnalyticsReport, AttackAnalyticsResultRow,
  AttackAnalyticsViewState, AttackOutcome,
} from '../src/types'
import {
  DEFAULT_ANALYTICS_VIEW, analyticsViewToHash, analyticsViewToSearchParams,
  formatAnalyticsCount, formatAnalyticsPercent, analyticsSuccessCountsLabel, analyticsValueKey,
} from '../src/utils/attackAnalytics'
import { compatibilityHeaders, getCompatibilityId } from './_compatibility'

const BASELINE: AttackAnalyticsFilter = {
  dimension: { name: 'operation' }, values: [{ kind: 'value', value: '00000000-0000-0000-0000-00000000a001' }], match_mode: 'any',
}
const OVERVIEW: AttackAnalyticsFilter = {
  dimension: { name: 'label', label_key: 'cohort' }, values: [{ kind: 'value', value: 'overview' }], match_mode: 'any',
}
const errors = new WeakMap<Page, string[]>()

function reportResponse(response: Response): boolean {
  return response.url().endsWith('/api/analytics/attacks/query') && response.request().method() === 'POST'
}

async function readReport(response: Response): Promise<AttackAnalyticsReport> {
  expect(response.status()).toBe(200)
  const report: AttackAnalyticsReport = await response.json()
  expect(report.summary.success_rate_decided).toBe(report.summary.success_rate)
  return report
}

async function openReport(page: Page, view?: AttackAnalyticsViewState): Promise<AttackAnalyticsReport> {
  const pending = page.waitForResponse(reportResponse)
  await page.goto(`/analytics${view ? analyticsViewToHash(view) : ''}`)
  const report = await readReport(await pending)
  await expect(page.getByRole('region', { name: 'Outcome summary' })).toBeVisible()
  await expect(page.getByRole('row', { name: `Open result ${report.results.items[0].attack_result_id}` })).toBeVisible()
  return report
}

function observe(page: Page) {
  const reports: Request[] = []
  const results: Request[] = []
  const facets: Request[] = []
  page.on('request', (request: Request) => {
    if (request.url().endsWith('/analytics/attacks/query')) reports.push(request)
    if (request.url().endsWith('/analytics/attacks/results')) results.push(request)
    if (request.url().endsWith('/analytics/attacks/facets')) facets.push(request)
  })
  return { reports, results, facets }
}

async function capture(page: Page, name: string): Promise<void> {
  const directory = process.env.PYRIT_ANALYTICS_SCREENSHOT_DIR
  if (!directory) return
  await expect(page.getByRole('region', { name: 'Outcome summary' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Reload', exact: true })).toBeEnabled()
  await mkdir(directory, { recursive: true })
  await page.screenshot({ path: path.join(directory, name), fullPage: true })
}

function channels(color: string): number[] {
  const values = color.match(/[\d.]+/g)?.slice(0, 3).map(Number)
  if (!values || values.length !== 3) throw new Error(`Expected RGB color, got ${color}`)
  return values
}

function contrast(foreground: string, background: string): number {
  const luminance = (color: string): number => {
    const values = channels(color).map((channel: number) => {
      const value = channel / 255
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    })
    return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722
  }
  const first = luminance(foreground)
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

test.beforeEach(async ({ page }: { page: Page }) => {
  const messages: string[] = []
  errors.set(page, messages)
  page.on('pageerror', (error: Error) => { messages.push(error.message) })
})

test.afterEach(async ({ page }: { page: Page }) => {
  expect(errors.get(page)).toEqual([])
})

test('counts every saved ID and keeps result paging, presentation, and freshness independent', async ({ page }: { page: Page }) => {
  const requests = observe(page)
  const report = await openReport(page)
  expect(report.summary).toMatchObject({ total_results: 204, successes: 83, failures: 41, errors: 20, undetermined: 60 })
  const summary = page.getByRole('region', { name: 'Outcome summary' })
  await expect(summary.getByText('204', { exact: true })).toBeVisible()
  await expect(summary.getByText('83 success / 124 decided')).toBeVisible()
  expect(requests.reports).toHaveLength(1)
  expect(requests.facets).toHaveLength(0)
  expect(requests.reports[0].headers()['pyrit-compatibility-id']).toBe(getCompatibilityId())
  const refreshed = await page.getByText(/Last refreshed:/).locator('time').getAttribute('datetime')
  const results = page.getByRole('region', { name: 'Matching AttackResults' })
  await results.getByRole('button', { name: 'Next', exact: true }).click()
  await expect(results.getByText('Page 2', { exact: true })).toBeVisible()
  expect(requests.results).toHaveLength(1)
  await page.getByLabel('Chart view', { exact: true }).selectOption('success-rate')
  await page.getByLabel('ASR denominator', { exact: true }).selectOption('all')
  await expect(summary.getByText(formatAnalyticsPercent(report.summary.success_rate_all), { exact: true })).toBeVisible()
  await expect(summary.getByText('83 success / 204 all results')).toBeVisible()
  await expect(results.getByText('Page 2', { exact: true })).toBeVisible()
  await page.clock.install()
  await page.clock.fastForward(300_000)
  expect(requests.reports).toHaveLength(1)
  expect(requests.results).toHaveLength(1)
  expect(await page.getByText(/Last refreshed:/).locator('time').getAttribute('datetime')).toBe(refreshed)
  const reloaded = page.waitForResponse(reportResponse)
  await page.getByRole('button', { name: 'Reload', exact: true }).click()
  await page.clock.fastForward(250)
  await readReport(await reloaded)
  await expect(results.getByText('Page 1', { exact: true })).toBeVisible()
  expect(requests.reports).toHaveLength(2)
})

test('drills into a real heatmap cell and opens its saved result through the normal route', async ({ page }: { page: Page }) => {
  await page.setViewportSize({ width: 1440, height: 1180 })
  const report = await openReport(page, {
    ...DEFAULT_ANALYTICS_VIEW, chart: 'heatmap', filters: { ...DEFAULT_ANALYTICS_VIEW.filters, dimensions: [BASELINE] },
  })
  expect(report.summary.total_results).toBe(80)
  const rowOption = report.rows.find((option: AttackAnalyticsOption) => option.label === 'Privacy')
  const columnOption = report.columns.find((option: AttackAnalyticsOption) => option.label === 'PromptSendingAttack')
  if (!rowOption || !columnOption) throw new Error('Synthetic heatmap axes are missing')
  const cell = report.cells.find((item: AttackAnalyticsCell) =>
    analyticsValueKey(item.row) === analyticsValueKey(rowOption.key) &&
    analyticsValueKey(item.column) === analyticsValueKey(columnOption.key))
  expect(cell).toBeDefined()
  if (!cell) throw new Error('Synthetic fixture is missing its Privacy / PromptSendingAttack cell')
  const button = page.getByRole('button', { name: /^Privacy \/ PromptSendingAttack:/ })
  await expect(button.getByText(analyticsSuccessCountsLabel(cell.statistics), { exact: true })).toBeVisible()
  await expect(button.getByText(`${formatAnalyticsCount(cell.statistics.total_results)} total`, { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Show cell data' }).click()
  await expect(page.getByRole('region', { name: 'Heatmap aggregate data' })).toBeVisible()
  await page.getByRole('button', { name: 'Hide cell data' }).click()
  const next = page.waitForResponse(reportResponse)
  await button.click()
  const filtered = await readReport(await next)
  expect(filtered.filters.dimensions).toEqual([...report.filters.dimensions, ...cell.drilldown_filters])
  expect(filtered.summary).toEqual(cell.statistics)
  const id = filtered.results.items[0].attack_result_id
  const row = page.getByRole('row', { name: `Open result ${id}` })
  await expect(row).toBeVisible()
  await capture(page, 'analytics-heatmap-drilldown.png')
  const sharedUrl = page.url()
  await row.focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(new RegExp(`/attacks/${id}$`))
  await expect(page.getByText('This is a safe, locally stored example response for analytics browser checks.')).toBeVisible()
  await page.getByRole('button', { name: 'Analytics', exact: true }).click()
  await expect(page).toHaveURL(sharedUrl)
  await expect(page.getByRole('row', { name: `Open result ${id}` })).toBeVisible()
  await page.getByRole('row', { name: `Open result ${id}` }).focus()
  await page.keyboard.press('Space')
  await expect(page).toHaveURL(new RegExp(`/attacks/${id}$`))
  await page.goBack()
  await expect(page).toHaveURL(sharedUrl)
  await expect(page.getByRole('row', { name: `Open result ${id}` })).toBeVisible()
})

test('keeps retries and all result roles when a saved scenario is selected', async ({ page }: { page: Page }) => {
  await openReport(page)
  await page.getByRole('button', { name: 'Add filter', exact: true }).click()
  await page.getByLabel('Filter dimension', { exact: true }).selectOption('scenario')
  await page.getByRole('checkbox', { name: /SyntheticRetryScenario/ }).check()
  const pending = page.waitForResponse(reportResponse)
  await page.getByRole('button', { name: 'Apply filter', exact: true }).click()
  const report = await readReport(await pending)
  expect(report.summary).toMatchObject({ total_results: 2, successes: 1, failures: 1, success_rate_decided: 0.5 })
  const ids = report.results.items.map((row: AttackAnalyticsResultRow) => row.attack_result_id)
  expect(new Set(ids).size).toBe(2)
  const conversations: string[] = []
  for (const id of ids) {
    const response = await page.request.get(`/api/attacks/${id}`, { headers: compatibilityHeaders() })
    expect(response.status()).toBe(200)
    const attack: { conversation_id: string } = await response.json()
    conversations.push(attack.conversation_id)
  }
  expect(new Set(conversations).size).toBe(1)
  const summary = page.getByRole('region', { name: 'Outcome summary' })
  await expect(summary.getByText('50%', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Filter to success: 1', exact: true }).click()
  await expect(summary.getByText('100%*', { exact: true })).toBeVisible()
  await expect(page.getByRole('note', { name: 'Outcome-filtered ASR' })).toContainText('Outcome filter active')
})

test('loads paginated facets on demand and preserves selections across pages and search', async ({ page }: { page: Page }) => {
  const requests = observe(page)
  await openReport(page)
  expect(requests.facets).toHaveLength(0)
  await page.getByRole('button', { name: 'Operation', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Synthetic baseline', exact: true }).check()
  await expect(page.getByRole('checkbox', { name: 'Synthetic follow-up (legacy name)', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Next values', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Synthetic follow-up (legacy name)', exact: true }).check()
  await page.getByLabel('Search values', { exact: true }).fill('nothing-matches')
  await expect(page.getByText(/No values match this search/)).toBeVisible()
  await expect(page.getByRole('checkbox', { name: 'Synthetic baseline', exact: true })).toBeChecked()
  await expect(page.getByRole('checkbox', { name: 'Synthetic follow-up (legacy name)', exact: true })).toBeChecked()
  const pending = page.waitForResponse(reportResponse)
  await page.getByRole('button', { name: 'Apply filter', exact: true }).click()
  const report = await readReport(await pending)
  expect(report.summary.total_results).toBe(104)
  expect(requests.facets).toHaveLength(3)
  expect(requests.reports).toHaveLength(2)
  await page.getByRole('button', { name: 'Add filter', exact: true }).click()
  await page.getByLabel('Filter dimension', { exact: true }).selectOption('request_converters')
  await page.getByLabel('Converter matching', { exact: true }).selectOption('all')
  await page.getByRole('checkbox', { name: 'Base64Converter', exact: true }).check()
  await page.getByRole('checkbox', { name: 'ROT13Converter', exact: true }).check()
  const converters = page.waitForResponse(reportResponse)
  await page.getByRole('button', { name: 'Apply filter', exact: true }).click()
  const filtered = await readReport(await converters)
  expect(filtered.filters.dimensions[1].match_mode).toBe('all')
  expect(filtered.results.items.every((row: AttackAnalyticsResultRow) => row.request_converters.includes('Base64Converter') &&
    row.request_converters.includes('ROT13Converter'))).toBe(true)
})

test('identifies Operations by ID and marks legacy names', async ({ page }: { page: Page }) => {
  const baseline = await openReport(page, {
    ...DEFAULT_ANALYTICS_VIEW, filters: { ...DEFAULT_ANALYTICS_VIEW.filters, dimensions: [BASELINE] },
  })
  expect(baseline.summary.total_results).toBe(80)
  await expect(page.getByRole('button', { name: 'Operation (ANY): Synthetic baseline', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Outcome summary' }).getByText('80', { exact: true })).toBeVisible()
  const report = await openReport(page)
  expect(report.groups).toEqual(expect.arrayContaining([
    expect.objectContaining({ key: BASELINE.values[0], label: 'Synthetic baseline' }),
    expect.objectContaining({
      key: { kind: 'legacy_operation_name', value: 'Synthetic follow-up' }, label: 'Synthetic follow-up (legacy name)',
    }),
  ]))
  await expect(page.getByRole('button', { name: 'Filter to Synthetic baseline', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Filter to Synthetic follow-up (legacy name)', exact: true })).toBeVisible()
})

test('large shared filters survive direct navigation, refresh, and Back within the server URL limit', async ({ page }: { page: Page }) => {
  const view: AttackAnalyticsViewState = {
    ...DEFAULT_ANALYTICS_VIEW,
    filters: { ...DEFAULT_ANALYTICS_VIEW.filters, dimensions: ['a', 'b'].map((key: string): AttackAnalyticsFilter => ({
      dimension: { name: 'label', label_key: `long-${key}` },
      values: [{ kind: 'value', value: (key === 'a' ? 'x' : 'y').repeat(4096) }], match_mode: 'any',
    })) },
  }
  const requests = observe(page)
  const targets: number[] = []
  page.on('request', (request: Request) => {
    if (request.isNavigationRequest()) {
      const url = new URL(request.url())
      targets.push(Buffer.byteLength(url.pathname + url.search))
    }
  })
  expect(Buffer.byteLength(`/analytics?${analyticsViewToSearchParams(view)}`)).toBeGreaterThan(8192)
  const report = await openReport(page, view)
  expect(report.summary.total_results).toBe(2)
  const pending = page.waitForResponse(reportResponse)
  await page.reload()
  expect((await readReport(await pending)).filters).toEqual(report.filters)
  await page.getByLabel('Chart view', { exact: true }).selectOption('success-rate')
  await page.goBack()
  await expect(page.getByLabel('Chart view', { exact: true })).toHaveValue('outcomes')
  await expect(page.getByRole('region', { name: 'Outcome summary' }).getByText('2 total', { exact: true })).toBeVisible()
  expect(targets.every((bytes: number) => bytes <= 8192)).toBe(true)
  expect(requests.reports).toHaveLength(2)
  const oversized = await page.request.get(`/analytics?oversized=${'x'.repeat(8192)}`)
  expect(oversized.status()).toBe(414)
})

test('recovers invalid links explicitly and migrates valid legacy query links without a duplicate read', async ({ page }: { page: Page }) => {
  const requests = observe(page)
  await page.goto('/analytics#analytics=invalid-json')
  await expect(page.getByText(/Cannot restore this analytics link/)).toBeVisible()
  expect(requests.reports).toHaveLength(0)
  await expect(page.getByRole('button', { name: 'Reload', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Reset analytics view' }).click()
  await expect(page.getByRole('region', { name: 'Outcome summary' })).toBeVisible()
  expect(requests.reports).toHaveLength(1)
  await page.goto(`/analytics?${analyticsViewToSearchParams(DEFAULT_ANALYTICS_VIEW)}`)
  await expect(page).toHaveURL(/\/analytics#analytics=/)
  await expect(page.getByRole('region', { name: 'Outcome summary' })).toBeVisible()
  expect(requests.reports).toHaveLength(2)
})

test('shows error-only ASR as unavailable by default rather than zero', async ({ page }: { page: Page }) => {
  const report = await openReport(page, { ...DEFAULT_ANALYTICS_VIEW, filters: {
    ...DEFAULT_ANALYTICS_VIEW.filters, outcomes: ['error'],
  } })
  expect(report.summary).toMatchObject({ errors: 20, total_results: 20, total_decided: 0, success_rate_decided: null })
  const summary = page.getByRole('region', { name: 'Outcome summary' })
  await expect(summary.getByText('Unavailable*', { exact: true })).toBeVisible()
  await expect(summary.getByText('0 success / 0 decided', { exact: true })).toBeVisible()
})

test('uses exactly matching, contrasting outcome fills in light and dark themes', async ({ page }: { page: Page }) => {
  await page.setViewportSize({ width: 1440, height: 1180 })
  await openReport(page, { ...DEFAULT_ANALYTICS_VIEW, filters: {
    ...DEFAULT_ANALYTICS_VIEW.filters, dimensions: [OVERVIEW],
  } })
  const summary = page.getByRole('region', { name: 'Outcome summary' })
  for (const theme of ['Light', 'Dark']) {
    await page.getByRole('button', { name: /^Theme:/ }).click()
    await page.getByRole('menuitemradio', { name: theme, exact: true }).click()
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme.toLowerCase())
    const fills: Partial<Record<AttackOutcome, string>> = {}
    for (const outcome of ['success', 'failure', 'error', 'undetermined'] as const) {
      const badge = summary.getByText(outcome, { exact: true })
      const colors = await badge.evaluate((element: HTMLElement) => ({
        fill: getComputedStyle(element).backgroundColor,
        foreground: getComputedStyle(element).color,
        icon: getComputedStyle(element.querySelector('svg') ?? element).color,
      }))
      fills[outcome] = colors.fill
      const segment = page.getByRole('button', { name: new RegExp(`^Synthetic baseline: ${outcome} segment;`) })
      const swatch = page.getByRole('button', { name: new RegExp(`^Synthetic baseline: \\d+ ${outcome};`) })
        .locator('[aria-hidden="true"]').first()
      await expect(segment).toHaveCSS('background-color', colors.fill)
      await expect(swatch).toHaveCSS('background-color', colors.fill)
      await expect(page.getByRole('table', { name: 'Saved attack results' }).getByText(outcome, { exact: true }).first())
        .toHaveCSS('background-color', colors.fill)
      expect(colors.icon).toBe(colors.foreground)
      expect(contrast(colors.foreground, colors.fill)).toBeGreaterThanOrEqual(4.5)
      await segment.hover()
      await expect(segment).toHaveCSS('background-color', colors.fill)
    }
    const blue = channels(fills.error ?? '')
    expect(blue[2]).toBeGreaterThan(blue[0])
    expect(blue[2]).toBeGreaterThan(blue[1])
    await page.getByRole('heading', { name: 'Analytics', exact: true }).hover()
    await capture(page, `analytics-overview-${theme.toLowerCase()}.png`)
  }
})

test.describe('narrow touch layout', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true })

  test('reflows controls and summary without page overflow and retains usable touch targets', async ({ page }: { page: Page }) => {
    await openReport(page, { ...DEFAULT_ANALYTICS_VIEW, filters: {
      ...DEFAULT_ANALYTICS_VIEW.filters, dimensions: [OVERVIEW],
    } })
    await expect(page.getByRole('button', { name: 'More filters', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'More filters', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Add filter', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Fewer filters', exact: true }).click()
    for (const name of ['Reload', 'Operation', 'Operator', 'Objective target']) {
      const box = await page.getByRole('button', { name, exact: true }).boundingBox()
      expect(box?.height).toBeGreaterThanOrEqual(44)
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
    expect(await page.getByTestId('analytics-page').evaluate((element: HTMLElement) => element.scrollWidth <= element.clientWidth)).toBe(true)
    await capture(page, 'analytics-narrow.png')
    await page.getByRole('region', { name: 'Outcome summary' }).evaluate((element: HTMLElement) => {
      element.scrollIntoView({ block: 'start' })
    })
    await capture(page, 'analytics-narrow-data.png')
  })
})
