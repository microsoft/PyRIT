import { makeStyles, shorthands, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAnalyticsGroupsStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM, minWidth: 0 },
  groups: {
    display: 'flex',
    flexDirection: 'column',
    overflowY: 'auto',
    maxHeight: '26rem',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalXS,
  },
  group: {
    display: 'grid',
    gridTemplateColumns: 'minmax(8rem, 1fr) minmax(16rem, 3fr)',
    alignItems: 'center',
    gap: tokens.spacingHorizontalL,
    paddingBottom: tokens.spacingVerticalM,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    [NARROW_VIEWPORT_QUERY]: { gridTemplateColumns: 'minmax(0, 1fr)', gap: tokens.spacingVerticalS },
  },
  groupLabel: { display: 'flex', flexDirection: 'column', alignItems: 'flex-start', minWidth: 0 },
  groupButton: {
    maxWidth: '100%',
    overflowWrap: 'anywhere',
    justifyContent: 'flex-start',
    textAlign: 'start',
    ...mobileTouchTargetHeight,
  },
  visualization: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXS, minWidth: 0 },
  bar: {
    display: 'flex',
    width: '100%',
    height: tokens.spacingVerticalL,
    backgroundColor: tokens.colorNeutralBackground4,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    ...mobileTouchTargetHeight,
  },
  segment: {
    display: 'block',
    height: '100%',
    '@media (forced-colors: active)': { borderRight: '1px solid currentColor', boxSizing: 'border-box' },
  },
  rateButton: {
    minWidth: 0,
    padding: 0,
    borderRadius: 0,
    justifyContent: 'flex-start',
  },
  rate: { backgroundColor: tokens.colorBrandBackground },
  unavailable: { ...shorthands.borderStyle('dashed'), backgroundColor: tokens.colorNeutralBackground2 },
  number: { fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' },
  legend: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
})
