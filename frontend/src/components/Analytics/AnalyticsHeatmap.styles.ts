import { makeStyles, shorthands, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useAnalyticsHeatmapStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS, minWidth: 0 },
  scroll: { overflow: 'auto', maxHeight: '28rem', minWidth: 0 },
  table: { tableLayout: 'auto', width: 'max-content', minWidth: '100%' },
  caption: {
    paddingBlock: tokens.spacingVerticalS,
    fontWeight: tokens.fontWeightSemibold,
    textAlign: 'start',
  },
  heading: { minWidth: '9rem', maxWidth: '16rem', overflowWrap: 'anywhere' },
  cell: { padding: tokens.spacingHorizontalXXS, minWidth: '9rem' },
  button: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    minWidth: '9rem',
    gap: tokens.spacingVerticalXXS,
    padding: tokens.spacingHorizontalS,
    fontVariantNumeric: 'tabular-nums',
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusSmall,
    ...mobileTouchTargetHeight,
  },
  level0: { backgroundColor: tokens.colorNeutralBackground1, color: tokens.colorNeutralForeground1 },
  level1: { backgroundColor: tokens.colorBrandBackground2, color: tokens.colorBrandForeground2 },
  level2: { backgroundColor: tokens.colorPaletteBlueBackground2, color: tokens.colorPaletteBlueForeground2 },
  level3: { backgroundColor: tokens.colorBrandBackground, color: tokens.colorNeutralForegroundOnBrand },
  level4: { backgroundColor: tokens.colorBrandBackgroundPressed, color: tokens.colorNeutralForegroundOnBrand },
  unavailable: {
    backgroundColor: tokens.colorNeutralBackground3,
    color: tokens.colorNeutralForeground2,
    ...shorthands.borderStyle('dashed'),
  },
  empty: {
    backgroundColor: tokens.colorNeutralBackground2,
    color: tokens.colorNeutralForeground2,
    ...shorthands.borderStyle('dotted'),
  },
  legend: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
  scrollTable: { overflowX: 'auto' },
  dataTable: { minWidth: '55rem' },
  touchTarget: { ...mobileTouchTargetHeight },
})
