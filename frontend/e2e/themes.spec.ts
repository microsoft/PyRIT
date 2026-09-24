import { expect, test } from '@playwright/test'
import type { Page, Route, TestInfo } from '@playwright/test'

interface PresetCase {
  readonly id: string
  readonly label: string
  readonly resolved: 'light' | 'dark'
  readonly background?: { readonly canvas: string; readonly chrome: string }
}

const PRESETS: readonly PresetCase[] = [
  { id: 'light', label: 'Light', resolved: 'light' },
  { id: 'dark', label: 'Dark', resolved: 'dark' },
  { id: 'raccoon', label: 'Raccoon', resolved: 'light', background: { canvas: '#eeeae4', chrome: '#e1dcd4' } },
  { id: 'jimothy', label: 'Jimothy', resolved: 'light', background: { canvas: '#e5eee8', chrome: '#d5e2d9' } },
  { id: 'pirate', label: 'Pirate', resolved: 'dark', background: { canvas: '#111f2c', chrome: '#203648' } },
  { id: 'seattle-rain', label: 'Seattle Rain', resolved: 'dark', background: { canvas: '#303436', chrome: '#42474a' } },
  { id: 'evergreen', label: 'Evergreen', resolved: 'dark', background: { canvas: '#11251f', chrome: '#234336' } },
  { id: 'blueprint', label: 'Blueprint', resolved: 'dark', background: { canvas: '#102a45', chrome: '#224763' } },
  { id: 'night-sky', label: 'Night Sky', resolved: 'dark', background: { canvas: '#171e36', chrome: '#2e3957' } },
]

const EMPTY_PAGE = {
  items: [],
  pagination: { limit: 50, has_more: false, next_cursor: null, prev_cursor: null },
}

const API_RESPONSES: Record<string, unknown> = {
  '/api/auth/config': { clientId: '', tenantId: '', allowedGroupIds: '' },
  '/api/auth/access': { isAdmin: true },
  '/api/health': { status: 'healthy' },
  '/api/version': { version: 'theme-preview', display: 'theme-preview' },
  '/api/targets': EMPTY_PAGE,
  '/api/targets/catalog': { items: [] },
  '/api/attacks': EMPTY_PAGE,
  '/api/attacks/attack-options': { attack_types: [] },
  '/api/attacks/converter-options': { converter_types: [] },
  '/api/labels': { source: 'attacks', labels: {} },
  '/api/converters': { items: [] },
  '/api/converters/catalog': { items: [] },
  '/api/scenarios/catalog': EMPTY_PAGE,
  '/api/scenarios/runs': EMPTY_PAGE,
  '/api/scenarios/runs/queue': {
    revision: 0,
    snapshot_at: '2026-01-01T00:00:00Z',
    active: null,
    queued: [],
  },
  '/api/config': { content: 'initializers: []\n', source: 'theme-preview', version: '1' },
  '/api/config/env-files': { items: [] },
  '/api/initializers': EMPTY_PAGE,
  '/api/initializers/custom': { items: [] },
  '/api/initializers/settings': { configured: [] },
}

async function installAppearanceFixtures(page: Page): Promise<void> {
  await page.route('**/api/**', async (route: Route) => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() !== 'GET') {
      throw new Error(`Changing appearance must not write to the backend: ${path}`)
    }
    if (
      path === '/api/scenarios/catalog/missing'
      || path === '/api/scenarios/runs/missing'
      || path === '/api/scenarios/runs/missing/progress'
    ) {
      await route.fulfill({ status: 404, json: { detail: 'Not found' } })
      return
    }
    if (!Object.prototype.hasOwnProperty.call(API_RESPONSES, path)) {
      throw new Error(`Unhandled appearance fixture: ${path}`)
    }
    await route.fulfill({ json: API_RESPONSES[path] })
  })
}

async function chooseTheme(page: Page, label: string): Promise<void> {
  await page.getByRole('button', { name: /^Theme:/ }).click()
  await page.getByRole('menuitemradio', { name: label, exact: true }).click()
  await expect(page.getByRole('button', { name: `Theme: ${label}`, exact: true })).toBeVisible()
}

function cssColor(hex: string): string {
  const components = [1, 3, 5].map((offset: number) => Number.parseInt(hex.slice(offset, offset + 2), 16))
  return `rgb(${components.join(', ')})`
}

test.describe('Theme presets', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test.beforeEach(async ({ page }: { page: Page }) => {
    await installAppearanceFixtures(page)
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'Theme: System' })).toBeVisible()
  })

  for (const preset of PRESETS) {
    const { id } = preset
    test(`${preset.label} persists and resolves its palette`, async ({ page }: { page: Page }, testInfo: TestInfo) => {
      await chooseTheme(page, preset.label)
      await page.reload()
      await expect(page.getByRole('button', { name: `Theme: ${preset.label}` })).toBeVisible()
      await expect(page.locator('html')).toHaveAttribute('data-theme', preset.resolved)

      const background = page.getByTestId('workspace-background')
      if (preset.background) {
        await expect(page.getByRole('complementary')).toHaveCSS(
          'background-color', cssColor(preset.background.chrome),
        )
        await expect(background).toHaveCSS('background-image', new RegExp(`${id}\\.svg`))
        await expect(background).toHaveCSS('pointer-events', 'none')
        await expect(background).toHaveAttribute('aria-hidden', 'true')
        await expect(page.getByRole('main')).toHaveCSS(
          'background-color', cssColor(preset.background.canvas),
        )
        await page.evaluate(async (url: string) => {
          const image = new Image()
          image.src = url
          await image.decode()
        }, `/backgrounds/${id}.svg`)
        await page.screenshot({ path: testInfo.outputPath(`${id}-desktop.png`) })
      } else {
        await expect(background).toHaveCount(0)
      }

      await page.emulateMedia({ colorScheme: preset.resolved === 'light' ? 'dark' : 'light' })
      await expect(page.locator('html')).toHaveAttribute('data-theme', preset.resolved)

      await page.emulateMedia({ forcedColors: 'active' })
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'high-contrast')
      await expect(background).toHaveCount(0)
      expect(await page.evaluate(() => JSON.parse(
        localStorage.getItem('pyrit.userPreferences.v1.local') ?? '{}',
      ).theme)).toBe(id)
      if (id === 'jimothy') {
        await page.screenshot({ path: testInfo.outputPath('high-contrast.png') })
      }

      await page.emulateMedia({ forcedColors: 'none' })
      await expect(page.locator('html')).toHaveAttribute('data-theme', preset.resolved)
      await expect(background).toHaveCount(preset.background ? 1 : 0)
    })
  }

  test('standard modes remove decoration and System follows the OS', async ({ page }: { page: Page }) => {
    for (const label of ['Light', 'Dark', 'System']) {
      await chooseTheme(page, 'Jimothy')
      await chooseTheme(page, label)
      await expect(page.getByTestId('workspace-background')).toHaveCount(0)
    }
    await page.emulateMedia({ colorScheme: 'dark' })
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
    await page.emulateMedia({ colorScheme: 'light' })
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  })

  test('keyboard selection and Escape restore focus', async ({ page }: { page: Page }) => {
    const trigger = page.getByRole('button', { name: 'Theme: System' })
    await trigger.focus()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('menuitemradio', { name: 'System', exact: true })).toBeFocused()
    const lastChoice = page.getByRole('menuitemradio').last()
    const label = (await lastChoice.innerText()).trim()
    await page.keyboard.press('End')
    await expect(lastChoice).toBeFocused()
    await page.keyboard.press('Enter')

    const selectedTrigger = page.getByRole('button', { name: `Theme: ${label}` })
    await expect(selectedTrigger).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('menuitemradio', { name: label, checked: true })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('menu')).toHaveCount(0)
    await expect(selectedTrigger).toBeFocused()
  })

  test('switching presets preserves an unsaved configuration draft', async ({ page }: { page: Page }) => {
    await page.getByRole('button', { name: 'Configuration', exact: true }).click()
    const editor = page.getByRole('textbox', { name: 'Configuration YAML' })
    const draft = 'initializers: []\n# unsaved draft\n'
    await editor.fill(draft)
    await chooseTheme(page, 'Pirate')
    await expect(editor).toHaveValue(draft)
    await expect(page).toHaveURL(/\/config$/)
  })

  for (const path of [
    '/chat', '/history/attacks', '/history/scanner', '/targets', '/config',
    '/scanner', '/scanner/missing', '/scanner-history/missing', '/',
  ]) {
    test(`decoration is shared with ${path}`, async ({ page }: { page: Page }) => {
      await chooseTheme(page, 'Jimothy')
      await page.goto(path)
      await expect(page.getByRole('button', { name: 'Theme: Jimothy' })).toBeVisible()
      await expect(page.getByTestId('workspace-background')).toBeVisible()
      await expect(page.locator('#main-content')).toHaveCSS(
        'background-color', cssColor('#e5eee8'),
      )
    })
  }

  test('an unknown saved preset recovers to System', async ({ page }: { page: Page }) => {
    await page.evaluate(() => localStorage.setItem('pyrit.themeMode', 'retired-theme'))
    await page.reload()
    await expect(page.getByRole('button', { name: 'Theme: System' })).toBeVisible()
    await expect(page.getByTestId('workspace-background')).toHaveCount(0)
  })
})

test.describe('Theme picker on small touch screens', () => {
  test.use({ viewport: { width: 390, height: 480 }, isMobile: true, hasTouch: true })

  test('keeps all choices reachable without overflowing the viewport', async (
    { page }: { page: Page }, testInfo: TestInfo,
  ) => {
    await installAppearanceFixtures(page)
    await page.goto('/')
    await page.getByRole('button', { name: 'Theme: System' }).click()
    expect(await page.getByRole('menuitemradio').count()).toBeGreaterThanOrEqual(PRESETS.length + 1)
    const firstChoice = page.getByRole('menuitemradio', { name: 'System', exact: true })
    await firstChoice.scrollIntoViewIfNeeded()
    await expect(firstChoice).toBeInViewport({ ratio: 1 })
    const lastChoice = page.getByRole('menuitemradio').last()
    const label = (await lastChoice.innerText()).trim()
    await lastChoice.scrollIntoViewIfNeeded()
    await expect(lastChoice).toBeInViewport({ ratio: 1 })
    const box = await lastChoice.boundingBox()
    expect(box?.height).toBeGreaterThanOrEqual(44)
    await page.screenshot({ path: testInfo.outputPath('short-theme-menu.png') })
    await lastChoice.click()
    await expect(page.getByRole('button', { name: `Theme: ${label}` })).toBeVisible()

    await page.setViewportSize({ width: 390, height: 844 })
    await chooseTheme(page, 'Jimothy')
    await page.screenshot({ path: testInfo.outputPath('jimothy-mobile.png') })
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  })
})
