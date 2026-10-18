import { existsSync } from 'node:fs'
import { basename, join } from 'node:path'

import { webDarkTheme, webLightTheme } from '@fluentui/react-components'

import type { ThemePreset } from '@/types'

import { isThemeMode, THEME_PRESETS } from './themePresets'

function channels(color: string): number[] {
  if (!/^#[0-9a-f]{6}$/i.test(color)) {
    throw new Error(`Expected a six-digit palette color, received ${color}`)
  }
  return [1, 3, 5].map((offset: number) => Number.parseInt(color.slice(offset, offset + 2), 16) / 255)
}

function luminance(rgb: number[]): number {
  const weights = [0.2126, 0.7152, 0.0722]
  return rgb.reduce((sum: number, channel: number, index: number) => {
    const linear = channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
    return sum + linear * weights[index]
  }, 0)
}

function contrast(foreground: string, background: number[]): number {
  const first = luminance(channels(foreground))
  const second = luminance(background)
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

const PRESET_ENTRIES: Array<[string, ThemePreset]> = Object.entries(THEME_PRESETS)
const CUSTOM_ENTRIES = PRESET_ENTRIES.filter(([, preset]: [string, ThemePreset]) => preset.background)
const READING_SURFACES = [
  'colorNeutralBackground1', 'colorNeutralBackground2', 'colorNeutralBackground3',
] as const
const STATUS_TEXT_TOKENS = [
  'colorPaletteRedForeground1', 'colorPaletteRedForeground3',
  'colorPaletteGreenForeground1', 'colorPaletteGreenForeground3',
  'colorPaletteDarkOrangeForeground1', 'colorPaletteDarkOrangeForeground3',
  'colorPaletteYellowForeground1', 'colorPaletteYellowForeground2',
  'colorStatusDangerForeground1', 'colorStatusDangerForeground3',
  'colorStatusSuccessForeground1', 'colorStatusSuccessForeground3',
  'colorStatusWarningForeground1', 'colorStatusWarningForeground3',
] as const
const ACCENT_TEXT_TOKENS = [
  'colorPaletteBlueForeground2', 'colorPaletteGreenForeground2',
  'colorPalettePurpleForeground2', 'colorPaletteRedForeground2',
] as const
const SEMANTIC_PAIRS = [
  ['colorPaletteRedForeground1', 'colorPaletteRedBackground1'],
  ['colorPaletteGreenForeground1', 'colorPaletteGreenBackground1'],
  ['colorPaletteDarkOrangeForeground1', 'colorPaletteDarkOrangeBackground1'],
  ['colorPaletteYellowForeground1', 'colorPaletteYellowBackground1'],
  ['colorPaletteRedForeground2', 'colorPaletteRedBackground2'],
  ['colorPaletteGreenForeground2', 'colorPaletteGreenBackground2'],
  ['colorPaletteBlueForeground2', 'colorPaletteBlueBackground2'],
  ['colorPalettePurpleForeground2', 'colorPalettePurpleBackground2'],
  ['colorPaletteYellowForeground2', 'colorPaletteYellowBackground2'],
  ['colorStatusDangerForeground1', 'colorStatusDangerBackground1'],
  ['colorStatusSuccessForeground1', 'colorStatusSuccessBackground1'],
  ['colorStatusWarningForeground1', 'colorStatusWarningBackground1'],
  ['colorStatusWarningForeground3', 'colorStatusWarningBackground1'],
  ['colorStatusDangerForeground2', 'colorStatusDangerBackground2'],
  ['colorStatusSuccessForeground2', 'colorStatusSuccessBackground2'],
  ['colorStatusWarningForeground2', 'colorStatusWarningBackground2'],
] as const

describe('themePresets', () => {
  it('keeps the existing standard Fluent themes unchanged', () => {
    expect(THEME_PRESETS.light.theme).toBe(webLightTheme)
    expect(THEME_PRESETS.dark.theme).toBe(webDarkTheme)
  })

  it('provides unique labels and the seven requested presets', () => {
    const labels = PRESET_ENTRIES.map(([, preset]: [string, ThemePreset]) => preset.label)
    expect(new Set(labels).size).toBe(labels.length)
    expect(Object.keys(THEME_PRESETS)).toEqual(expect.arrayContaining([
      'raccoon', 'jimothy', 'pirate', 'seattle-rain', 'evergreen', 'blueprint', 'night-sky',
    ]))
  })

  it.each(CUSTOM_ENTRIES)('bundles a local SVG for %s', (_id: string, preset: ThemePreset) => {
    expect(preset.background).toBeDefined()
    if (!preset.background) throw new Error('Custom preset is missing its background')
    expect(preset.background.imageUrl).toMatch(/^\/backgrounds\/[a-z-]+\.svg$/)
    expect(preset.background.opacity).toBeGreaterThan(0)
    expect(preset.background.opacity).toBeLessThanOrEqual(1)
    expect(existsSync(join(
      __dirname, '..', '..', 'public', 'backgrounds', basename(preset.background.imageUrl),
    ))).toBe(true)
  })

  it.each(['system', ...Object.keys(THEME_PRESETS)])('accepts registered preference %s', (mode: string) => {
    expect(isThemeMode(mode)).toBe(true)
  })

  it.each([null, undefined, 42, '', 'retired-theme', 'constructor', 'toString', '__proto__', {}])(
    'rejects unregistered preference %p',
    (value: unknown) => {
      expect(isThemeMode(value)).toBe(false)
    },
  )
})

const CONTENT_CONTRAST_CASES = CUSTOM_ENTRIES.flatMap(([id, { theme }]: [string, ThemePreset]) =>
  READING_SURFACES.flatMap(
    (surface: typeof READING_SURFACES[number]) => {
      const background = theme[surface]
      return [
        { name: `${id} primary on ${surface}`, foreground: theme.colorNeutralForeground1, background },
        { name: `${id} secondary on ${surface}`, foreground: theme.colorNeutralForeground3, background },
        { name: `${id} links on ${surface}`, foreground: theme.colorBrandForegroundLink, background },
        { name: `${id} hovered links on ${surface}`, foreground: theme.colorBrandForegroundLinkHover, background },
        { name: `${id} pressed links on ${surface}`, foreground: theme.colorBrandForegroundLinkPressed, background },
        ...[...STATUS_TEXT_TOKENS, ...ACCENT_TEXT_TOKENS].map(
          (token: typeof STATUS_TEXT_TOKENS[number] | typeof ACCENT_TEXT_TOKENS[number]) => ({
            name: `${id} ${token} on ${surface}`,
            foreground: theme[token],
            background,
          }),
        ),
      ]
    },
  ),
)

const BUTTON_CONTRAST_CASES = CUSTOM_ENTRIES.flatMap(([id, { theme }]: [string, ThemePreset]) =>
  [
    theme.colorBrandBackground,
    theme.colorBrandBackgroundHover,
    theme.colorBrandBackgroundPressed,
    theme.colorBrandBackgroundSelected,
  ].map((background: string, state: number) => ({
    name: `${id} button state ${state}`,
    foreground: theme.colorNeutralForegroundOnBrand,
    background,
  })),
)

const SEMANTIC_CONTRAST_CASES = CUSTOM_ENTRIES.flatMap(([id, { theme }]: [string, ThemePreset]) =>
  SEMANTIC_PAIRS.map(([foreground, background]: typeof SEMANTIC_PAIRS[number]) => ({
    name: `${id} ${foreground} on ${background}`,
    foreground: theme[foreground],
    background: theme[background],
  })),
)

describe('preset palette accessibility', () => {
  it.each([...CONTENT_CONTRAST_CASES, ...BUTTON_CONTRAST_CASES, ...SEMANTIC_CONTRAST_CASES])(
    '$name has AA text contrast',
    ({ foreground, background }: { foreground: string; background: string }) => {
      expect(contrast(foreground, channels(background))).toBeGreaterThanOrEqual(4.5)
    },
  )

  it.each(CUSTOM_ENTRIES)('%s preserves Fluent semantic backgrounds and borders', (
    _id: string, preset: ThemePreset,
  ) => {
    const baseTheme = preset.resolved === 'dark' ? webDarkTheme : webLightTheme
    const semanticSurfaces = Object.entries(baseTheme).filter(
      ([token]: [string, string]) => /^color(?:Palette|Status).*(?:Background|Border)/.test(token),
    )
    expect(preset.theme).toEqual(expect.objectContaining(Object.fromEntries(semanticSurfaces)))
  })

  it.each(CUSTOM_ENTRIES)(
    '%s keeps bare workspace text readable even over the strongest possible artwork',
    (_id: string, preset: ThemePreset) => {
      if (!preset.background) throw new Error('Custom preset is missing its background')
      const { opacity } = preset.background
      const artChannel = preset.resolved === 'light' ? 0 : 1
      const background = channels(preset.theme.colorNeutralBackground2).map(
        (channel: number) => channel * (1 - opacity) + artChannel * opacity,
      )
      for (const foreground of [
        preset.theme.colorNeutralForeground1,
        preset.theme.colorNeutralForeground3,
        preset.theme.colorBrandForegroundLink,
        preset.theme.colorBrandForegroundLinkHover,
        preset.theme.colorBrandForegroundLinkPressed,
        ...STATUS_TEXT_TOKENS.map((token: typeof STATUS_TEXT_TOKENS[number]) => preset.theme[token]),
      ]) {
        expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5)
      }
    },
  )
})
