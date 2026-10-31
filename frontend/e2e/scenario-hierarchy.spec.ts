import { expect, test, type Page } from '@playwright/test'
import { scenarioRunRoutePath } from '../src/utils/routeParams'

// Seeded by e2e/fixtures/scenario_hierarchy_backend.py
const run = scenarioRunRoutePath('00000000-0000-0000-0000-000000003602')

async function openHierarchy(page: Page) {
  await page.goto(run)
  const section = page.getByRole('region', { name: 'Result hierarchy', exact: true })
  await expect(section.getByText('Sequential parent', { exact: true })).toBeVisible()
  return section
}

test('browses a compound result through the real API, page by page, with stable totals', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const section = await openHierarchy(page)
  const totals = section.getByLabel('Run totals')
  const before = await totals.textContent()

  const firstPage = page.waitForResponse((response) => /\/hierarchy\/nodes\/[^/?]+\?limit=200$/.test(response.url()))
  await section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  expect((await firstPage).status()).toBe(200)

  const table = section.getByRole('table', { name: 'Sequential saved results' })
  const row = (text: string) => table.getByRole('row').filter({ hasText: text }).first()
  await expect(table.getByText('Step 1', { exact: true })).toBeVisible()
  await expect(row('#2')).toContainText('Unresolved reference')
  await expect(row('#4')).toContainText('Unresolved reference')
  // Children keep the parent's columns: outcome text sits under "Attack outcome", not nested in the row.
  await expect(row('Step 5').getByRole('cell').nth(2)).toHaveText('Success')
  await expect(section.getByText('Showing 200 of 520 saved children')).toBeVisible()

  for (const shown of [400, 520]) {
    const next = page.waitForResponse((response) => response.url().includes('children_cursor='))
    await section.getByRole('button', { name: 'Load more children' }).click()
    expect((await next).status()).toBe(200)
    await expect(section.getByText(`Showing ${shown} of 520 saved children`)).toBeVisible()
  }
  await expect(section.getByRole('button', { name: 'Load more children' })).toHaveCount(0)
  await expect(table.getByText('Step 520', { exact: true })).toBeVisible()

  await table.getByRole('button', { name: 'Expand children of #1 sequential: Step 1' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toBeVisible()
  expect(await totals.textContent()).toBe(before)

  await section.getByRole('button', { name: 'Open details for single: Single-turn attempt' }).click()
  await expect(page.getByRole('dialog', { name: 'single' })).toBeVisible()
  await page.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.getByRole('heading', { name: 'Result hierarchy', exact: true }).evaluate((heading) =>
    heading.scrollIntoView({ block: 'start' }),
  )
  await page.screenshot({ path: testInfo.outputPath('scenario-hierarchy-desktop.png') })
  expect(errors).toEqual([])
})

test.describe('on a narrow touch screen', () => {
  // Touch targets grow only for coarse pointers, as in touch-targets.spec.ts.
  test.use({ hasTouch: true })

  test('keeps the hierarchy usable at a narrow viewport and from the keyboard', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const section = await openHierarchy(page)

    const expand = section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' })
    await expand.focus()
    await page.keyboard.press('Enter')
    // The same button is renamed once expanded, so look it up by its new name.
    await expect(section.getByRole('button', { name: 'Collapse children of sequential: Sequential parent' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    await expect(section.getByText('Showing 200 of 520 saved children')).toBeVisible()

    const loadMore = section.getByRole('button', { name: 'Load more children' })
    const size = await loadMore.boundingBox()
    expect(size?.height).toBeGreaterThanOrEqual(44)
    const overflow = await section.evaluate((element) => element.scrollWidth > element.clientWidth)
    expect(overflow).toBe(false)

    await section.scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath('scenario-hierarchy-mobile.png') })
  })
})
