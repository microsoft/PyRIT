import { expect, test, type Page } from '@playwright/test'
import { scenarioRunRoutePath } from '../src/utils/routeParams'

// Seeded by e2e/fixtures/scenario_hierarchy_backend.py.
const scenarioResultId = '00000000-0000-0000-0000-000000003602'
const run = scenarioRunRoutePath(scenarioResultId)

async function openHierarchy(page: Page) {
  await page.goto(run)
  const section = page.getByRole('region', { name: 'Atomic attack groups', exact: true })
  await section.getByRole('button', { name: 'Expand attacks in Sequential' }).click()
  await expect(section.getByText('Sequential parent', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Result hierarchy', exact: true })).toHaveCount(0)
  return section
}

test('browses compound results in the existing attack-group table with stable totals', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const section = await openHierarchy(page)
  const totals = section.getByText('2 groups, 524 executions', { exact: true })
  const before = await totals.textContent()

  const firstPage = page.waitForResponse((response) => /\/hierarchy\/nodes\/[^/?]+\?limit=200$/.test(response.url()))
  await section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  expect((await firstPage).status()).toBe(200)

  const table = section.getByRole('table', { name: 'Sequential attack results' })
  const row = (text: string) => table.getByRole('row').filter({ hasText: text }).first()
  await expect(table.getByText('Step 1', { exact: true })).toBeVisible()
  await expect(row('#2')).toContainText('Unresolved reference')
  await expect(row('#4')).toContainText('Unresolved reference')
  await expect(row('Step 5').getByRole('cell').nth(2)).toHaveText('Success')
  await expect(section.getByText('Showing 200 of 520 saved child positions')).toBeVisible()

  for (const shown of [400, 520]) {
    const next = page.waitForResponse((response) => response.url().includes('children_cursor='))
    await section.getByRole('button', { name: 'Load more children' }).click()
    expect((await next).status()).toBe(200)
    await expect(section.getByText(`Showing ${shown} of 520 saved child positions`)).toBeVisible()
  }
  await expect(section.getByRole('button', { name: 'Load more children' })).toHaveCount(0)
  await expect(table.getByText('Step 520', { exact: true })).toBeVisible()

  await table.getByRole('button', { name: 'Expand children of #1 sequential: Step 1' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toBeVisible()
  await table.getByRole('button', { name: 'Collapse children of sequential: Sequential parent' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toHaveCount(0)
  await table.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  await expect(table.getByText('Nested step 2', { exact: true })).toBeVisible()
  await expect(section.getByText('Showing 520 of 520 saved child positions')).toBeVisible()

  await section.getByRole('button', { name: 'Collapse attacks in Sequential' }).click()
  await section.getByRole('button', { name: 'Expand attacks in Sequential' }).click()
  await expect(section.getByText('Showing 520 of 520 saved child positions')).toBeVisible()
  await section.getByRole('button', { name: 'Collapse atomic attack groups' }).click()
  await section.getByRole('button', { name: 'Expand atomic attack groups' }).click()
  await expect(section.getByText('Nested step 2', { exact: true })).toBeVisible()
  expect(await totals.textContent()).toBe(before)

  await section.getByRole('button', { name: 'Open details for #5 sequential: Step 5' }).click()
  const dialog = page.getByRole('dialog', { name: 'sequential' })
  await expect(dialog.getByText('Step 5', { exact: true })).toBeVisible()
  await dialog.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(section.getByRole('button', { name: 'Open details for #5 sequential: Step 5' })).toBeFocused()

  await page.getByRole('heading', { name: 'Atomic attack groups', exact: true }).evaluate((heading) =>
    heading.scrollIntoView({ block: 'start' }),
  )
  await page.screenshot({ path: testInfo.outputPath('scenario-hierarchy-desktop.png'), animations: 'disabled' })
  expect(errors).toEqual([])
})

test('opens an actual saved child chat and retains the existing scenario breadcrumb after a reload', async ({ page }, testInfo) => {
  const section = await openHierarchy(page)
  await section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' }).click()
  const row = section.getByRole('row', { name: 'View conversation for #5 sequential: Step 5' })
  const chatLink = row.getByRole('link', { name: 'Step 5', exact: true })
  await expect(chatLink).toHaveAttribute('href', new RegExp(`\\?scenarioResultId=${scenarioResultId}$`))
  await chatLink.click()

  await expect(page).toHaveURL(new RegExp(`/attack/chat/[^/]+/conversations/[^/?]+\\?scenarioResultId=${scenarioResultId}$`))
  await expect(page.getByText('Synthetic request for Step 5. No external model was called.', { exact: true })).toBeVisible()
  await expect(page.getByText('Stored example response for Step 5. This is offline sample data.', { exact: true })).toBeVisible()
  const breadcrumb = page.getByRole('link', { name: 'Back to scan', exact: true })
  await expect(breadcrumb).toHaveAttribute('href', run)

  await page.reload()
  await expect(page.getByText('Stored example response for Step 5. This is offline sample data.', { exact: true })).toBeVisible()
  await expect(breadcrumb).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('scenario-saved-chat-breadcrumb.png'), animations: 'disabled' })

  await breadcrumb.click()
  await expect(page).toHaveURL(run)
  const groups = page.getByRole('region', { name: 'Atomic attack groups', exact: true })
  await expect(groups.getByText('2 groups, 524 executions', { exact: true })).toBeVisible()
  await groups.getByRole('button', { name: 'Expand attacks in Single turn' }).click()
  const single = groups.getByRole('row', { name: 'View conversation for single: Single-turn attempt' })
  await single.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Stored example response for Single-turn attempt. This is offline sample data.', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Back to scan', exact: true }).click()
  await expect(page).toHaveURL(run)
})

test.describe('on a narrow touch screen', () => {
  test.use({ hasTouch: true })

  test('keeps grouped hierarchy rows usable on touch and from the keyboard', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const section = await openHierarchy(page)

    const expand = section.getByRole('button', { name: 'Expand children of sequential: Sequential parent' })
    await expand.focus()
    await page.keyboard.press('Enter')
    await expect(section.getByRole('button', { name: 'Collapse children of sequential: Sequential parent' })).toHaveAttribute(
      'aria-expanded', 'true',
    )
    await expect(section.getByText('Showing 200 of 520 saved child positions')).toBeVisible()

    const loadMore = section.getByRole('button', { name: 'Load more children' })
    const size = await loadMore.boundingBox()
    expect(size?.height).toBeGreaterThanOrEqual(44)
    const overflow = await section.evaluate((element) => element.scrollWidth > element.clientWidth)
    expect(overflow).toBe(false)

    await section.getByText('Sequential', { exact: true }).evaluate((label) => label.scrollIntoView({ block: 'start' }))
    await page.screenshot({ path: testInfo.outputPath('scenario-hierarchy-mobile.png'), animations: 'disabled' })
  })
})
