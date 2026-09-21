import {
  createDarkTheme,
  createLightTheme,
  webDarkTheme,
  webLightTheme,
} from '@fluentui/react-components'
import type { BrandVariants, Theme } from '@fluentui/react-components'

import type { ThemeMode, ThemePreset } from '@/types'

interface Palette {
  readonly resolved: 'light' | 'dark'
  readonly brand: BrandVariants
  readonly canvas: string
  readonly surface: string
  readonly raised: string
  readonly foreground: string
  readonly secondary: string
  readonly stroke: string
}

const STATUS_COLORS = {
  light: {
    red: '#751d1f',
    green: '#094509',
    darkOrange: '#7a2101',
    yellow: '#4c4400',
    danger: '#6e0811',
    warning: '#6b2b05',
  },
  dark: {
    red: '#f6d1d1',
    green: '#c1e6c1',
    darkOrange: '#f8d6c8',
    yellow: '#fffde0',
    danger: '#f6d3d7',
    warning: '#fdd9c4',
  },
} as const

function createPaletteTheme(palette: Palette): Theme {
  const theme = palette.resolved === 'dark'
    ? createDarkTheme(palette.brand)
    : createLightTheme(palette.brand)
  const status = STATUS_COLORS[palette.resolved]

  return {
    ...theme,
    // Status text also sits on custom neutrals and artwork, not just Fluent's tinted surfaces.
    colorPaletteRedForeground1: status.red,
    colorPaletteRedForeground3: status.red,
    colorPaletteGreenForeground1: status.green,
    colorPaletteGreenForeground3: status.green,
    colorPaletteDarkOrangeForeground1: status.darkOrange,
    colorPaletteDarkOrangeForeground3: status.darkOrange,
    colorPaletteYellowForeground1: status.yellow,
    colorPaletteYellowForeground2: status.yellow,
    colorStatusDangerForeground1: status.danger,
    colorStatusDangerForeground3: status.danger,
    colorStatusSuccessForeground1: status.green,
    colorStatusSuccessForeground3: status.green,
    colorStatusWarningForeground1: status.warning,
    colorStatusWarningForeground3: status.warning,
    colorNeutralBackground1: palette.surface,
    colorNeutralBackground1Hover: palette.raised,
    colorNeutralBackground1Pressed: palette.canvas,
    colorNeutralBackground1Selected: palette.raised,
    colorNeutralBackground2: palette.canvas,
    colorNeutralBackground2Hover: palette.surface,
    colorNeutralBackground2Pressed: palette.raised,
    colorNeutralBackground2Selected: palette.surface,
    colorNeutralBackground3: palette.raised,
    colorNeutralBackground3Hover: palette.surface,
    colorNeutralBackground3Pressed: palette.canvas,
    colorNeutralBackground3Selected: palette.surface,
    colorNeutralBackground4: palette.raised,
    colorNeutralBackground5: palette.canvas,
    colorNeutralBackground6: palette.raised,
    colorNeutralForeground1: palette.foreground,
    colorNeutralForeground1Hover: palette.foreground,
    colorNeutralForeground1Pressed: palette.foreground,
    colorNeutralForeground1Selected: palette.foreground,
    colorNeutralForeground2: palette.secondary,
    colorNeutralForeground2Hover: palette.foreground,
    colorNeutralForeground2Pressed: palette.foreground,
    colorNeutralForeground2Selected: palette.foreground,
    colorNeutralForeground3: palette.secondary,
    colorNeutralForeground3Hover: palette.foreground,
    colorNeutralForeground3Pressed: palette.foreground,
    colorNeutralForeground3Selected: palette.foreground,
    colorNeutralForeground4: palette.secondary,
    colorNeutralStroke1: palette.stroke,
    colorNeutralStroke2: palette.stroke,
    colorNeutralStrokeAccessible: palette.secondary,
    colorBrandForeground1: palette.brand[palette.resolved === 'dark' ? 120 : 50],
    colorBrandForegroundLink: palette.brand[palette.resolved === 'dark' ? 120 : 50],
    colorBrandForegroundLinkHover: palette.brand[palette.resolved === 'dark' ? 140 : 40],
    colorBrandForegroundLinkPressed: palette.brand[palette.resolved === 'dark' ? 120 : 30],
    colorBrandForegroundLinkSelected: palette.brand[palette.resolved === 'dark' ? 120 : 50],
    // Keep white button labels readable across each palette's interaction states.
    colorBrandBackground: palette.brand[70],
    colorBrandBackgroundHover: palette.brand[80],
    colorBrandBackgroundPressed: palette.brand[60],
    colorBrandBackgroundSelected: palette.brand[70],
  }
}

/** Add a preset here to register its palette, wallpaper, menu entry, and stored ID. */
export const THEME_PRESETS = {
  light: {
    label: 'Light',
    resolved: 'light',
    theme: webLightTheme,
  },
  dark: {
    label: 'Dark',
    resolved: 'dark',
    theme: webDarkTheme,
  },
  raccoon: {
    label: 'Raccoon',
    resolved: 'light',
    theme: createPaletteTheme({
      resolved: 'light',
      brand: {
        10: '#14110f', 20: '#28211d', 30: '#342b24', 40: '#40362c',
        50: '#4c4034', 60: '#574a3b', 70: '#5f5140', 80: '#665744',
        90: '#867560', 100: '#998770', 110: '#ad9b85', 120: '#c1af9a',
        130: '#d5c4b1', 140: '#e3d5c5', 150: '#efe5d8', 160: '#f8f3ec',
      },
      canvas: '#eeeae4',
      surface: '#fffcf7',
      raised: '#e1dcd4',
      foreground: '#2b2926',
      secondary: '#504b44',
      stroke: '#b8aea0',
    }),
    background: { imageUrl: '/backgrounds/raccoon.svg', opacity: 0.16 },
  },
  jimothy: {
    label: 'Jimothy',
    resolved: 'light',
    theme: createPaletteTheme({
      resolved: 'light',
      brand: {
        10: '#071e14', 20: '#0e2d1f', 30: '#163a28', 40: '#19442e',
        50: '#1d4d34', 60: '#205637', 70: '#245f3d', 80: '#296744',
        90: '#42865e', 100: '#56956f', 110: '#70a586', 120: '#8ab69b',
        130: '#a5c8b2', 140: '#c0d9c9', 150: '#dcebe1', 160: '#f0f7f2',
      },
      canvas: '#e5eee8',
      surface: '#f7fbf7',
      raised: '#d5e2d9',
      foreground: '#25382e',
      secondary: '#364b3e',
      stroke: '#a7bbae',
    }),
    background: { imageUrl: '/backgrounds/jimothy.svg', opacity: 0.2 },
  },
  pirate: {
    label: 'Pirate',
    resolved: 'dark',
    theme: createPaletteTheme({
      resolved: 'dark',
      brand: {
        10: '#241807', 20: '#38260d', 30: '#493313', 40: '#574019',
        50: '#65491d', 60: '#725422', 70: '#7e5e28', 80: '#89672e',
        90: '#ad8944', 100: '#c9a966', 110: '#dcc085', 120: '#e5cea0',
        130: '#eddcb9', 140: '#f3e7d0', 150: '#f8efdf', 160: '#fcf7ec',
      },
      canvas: '#111f2c',
      surface: '#192c3d',
      raised: '#203648',
      foreground: '#f2e8d1',
      secondary: '#d0c4a9',
      stroke: '#4c5f70',
    }),
    background: { imageUrl: '/backgrounds/pirate.svg', opacity: 0.2 },
  },
  'seattle-rain': {
    label: 'Seattle Rain',
    resolved: 'dark',
    theme: createPaletteTheme({
      resolved: 'dark',
      brand: {
        10: '#151719', 20: '#25292b', 30: '#303639', 40: '#3a4246',
        50: '#434d52', 60: '#4c585e', 70: '#566268', 80: '#5f6c73',
        90: '#75848b', 100: '#91a0a7', 110: '#b9c3c7', 120: '#d2d8db',
        130: '#dfe4e6', 140: '#ebeeef', 150: '#f3f5f6', 160: '#fafbfb',
      },
      canvas: '#303436',
      surface: '#3a3f41',
      raised: '#42474a',
      foreground: '#f4f5f5',
      secondary: '#daddde',
      stroke: '#929a9e',
    }),
    background: { imageUrl: '/backgrounds/seattle-rain.svg', opacity: 0.2 },
  },
  evergreen: {
    label: 'Evergreen',
    resolved: 'dark',
    theme: createPaletteTheme({
      resolved: 'dark',
      brand: {
        10: '#071d10', 20: '#0f2b1a', 30: '#153922', 40: '#1e442b',
        50: '#244e32', 60: '#295839', 70: '#2f6241', 80: '#356d49',
        90: '#4f8a62', 100: '#6aa47c', 110: '#89bd98', 120: '#a4cfae',
        130: '#bedfca', 140: '#d5eadb', 150: '#e7f3e9', 160: '#f5faf5',
      },
      canvas: '#11251f',
      surface: '#19362b',
      raised: '#234336',
      foreground: '#ecf5e8',
      secondary: '#b7cfb8',
      stroke: '#526f5e',
    }),
    background: { imageUrl: '/backgrounds/evergreen.svg', opacity: 0.2 },
  },
  blueprint: {
    label: 'Blueprint',
    resolved: 'dark',
    theme: createPaletteTheme({
      resolved: 'dark',
      brand: {
        10: '#06192d', 20: '#0c2941', 30: '#11364e', 40: '#17415c',
        50: '#1d4a68', 60: '#225275', 70: '#265b80', 80: '#2b638b',
        90: '#427faa', 100: '#5f9dc4', 110: '#7fbbdf', 120: '#a0d2ed',
        130: '#bcdef4', 140: '#d3e9fa', 150: '#e7f3fc', 160: '#f5fbff',
      },
      canvas: '#102a45',
      surface: '#183853',
      raised: '#224763',
      foreground: '#e5f1ff',
      secondary: '#b8d2e6',
      stroke: '#56768e',
    }),
    background: { imageUrl: '/backgrounds/blueprint.svg', opacity: 0.2 },
  },
  'night-sky': {
    label: 'Night Sky',
    resolved: 'dark',
    theme: createPaletteTheme({
      resolved: 'dark',
      brand: {
        10: '#15152b', 20: '#24243f', 30: '#30304f', 40: '#3a3b5e',
        50: '#44456e', 60: '#4c4e7c', 70: '#55578b', 80: '#5d6099',
        90: '#7578b3', 100: '#9093cb', 110: '#aaaddd', 120: '#c2c4e9',
        130: '#d4d6f1', 140: '#e2e3f7', 150: '#eeeffb', 160: '#f8f8fe',
      },
      canvas: '#171e36',
      surface: '#222c49',
      raised: '#2e3957',
      foreground: '#f0edfa',
      secondary: '#c5c6e2',
      stroke: '#616981',
    }),
    background: { imageUrl: '/backgrounds/night-sky.svg', opacity: 0.2 },
  },
} as const satisfies Record<string, ThemePreset>

export function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === 'string'
    && (value === 'system' || Object.prototype.hasOwnProperty.call(THEME_PRESETS, value))
}
