import { expect, test } from '@playwright/test'
import { scenarioRunRoutePath } from '../src/utils/routeParams'

const run = scenarioRunRoutePath('00000000-0000-0000-0000-00000000238c')

test('compares persisted outcomes through the real API, including retries and exclusions', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto(run)
  const panel = page.getByRole('region', { name: 'Paired outcomes', exact: true })
  await expect(panel).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Compare outcomes' })).toBeDisabled()
  await panel.getByLabel('Baseline group').selectOption('baseline')
  await panel.getByLabel('Attack group').selectOption('attack')
  const response = page.waitForResponse((response) => response.url().includes('/paired-outcomes?'))
  await panel.getByRole('button', { name: 'Compare outcomes' }).click()
  const result = await response
  expect(result.status()).toBe(200)
  expect(await result.json()).toMatchObject({
    planned_pairs: 8, decided_pairs: 4, excluded_pairs: 4,
    failure_to_success: 1, success_to_failure: 1, failure_to_failure: 1, success_to_success: 1,
    baseline_success_rate: 0.5, attack_success_rate: 0.5, paired_difference: 0,
    missing_both: 1, missing_attack: 1, attack_errors: 1, attack_undetermined: 1,
    unmatched_baseline_seeds: 1, unmatched_attack_seeds: 1,
  })
  const comparison = panel.getByRole('region', { name: 'Paired comparison' })
  await expect(comparison.getByRole('group', { name: 'Decided pairs' })).toContainText('4 / 8')
  await expect(comparison.getByRole('group', { name: 'Baseline success rate' })).toContainText('50.0%')
  await expect(comparison.getByRole('group', { name: 'Paired difference' })).toContainText('0.0 pp')
  for (const label of ['Failure → Success', 'Success → Failure', 'Failure → Failure', 'Success → Success']) {
    await expect(comparison.getByRole('row', { name: `${label} 1` })).toBeVisible()
  }
  await panel.getByLabel('Attack group').selectOption('')
  await expect(comparison).toHaveCount(0)
  await expect(panel.getByRole('button', { name: 'Compare outcomes' })).toBeDisabled()
  expect(errors).toEqual([])
})

test('keeps comparisons usable at a narrow viewport and with keyboard controls', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(run)
  const panel = page.getByRole('region', { name: 'Paired outcomes', exact: true })
  await panel.getByLabel('Baseline group').selectOption('baseline')
  await panel.getByLabel('Attack group').selectOption('attack')
  await panel.getByLabel('Baseline group').focus()
  await page.keyboard.press('Tab')
  await expect(panel.getByLabel('Attack group')).toBeFocused()
  await page.keyboard.press('Tab')
  const button = panel.getByRole('button', { name: 'Compare outcomes' })
  await expect(button).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(panel.getByRole('region', { name: 'Paired comparison' })).toBeVisible()
  const overflow = await panel.evaluate((element) => element.scrollWidth > element.clientWidth)
  expect(overflow).toBe(false)
  const size = await button.boundingBox()
  expect(size?.height).toBeGreaterThanOrEqual(44)
  await panel.getByRole('table', { name: 'Outcome transitions' }).scrollIntoViewIfNeeded()
  await expect(panel.getByRole('row', { name: 'Failure → Success 1' })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('paired-outcomes-mobile.png') })
})

test('explains why legacy or failed runs cannot be compared', async ({ page }) => {
  await page.goto(scenarioRunRoutePath('00000000-0000-0000-0000-00000000238d'))
  await expect(page.getByRole('region', { name: 'Paired outcomes', exact: true })).toContainText('saved execution plan')
  await page.goto(scenarioRunRoutePath('00000000-0000-0000-0000-00000000238e'))
  await expect(page.getByRole('region', { name: 'Paired outcomes', exact: true })).toContainText('Complete the run')
})
