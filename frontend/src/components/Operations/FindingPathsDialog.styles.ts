import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingPathsDialogStyles = makeStyles({
  form: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalL },
  // Paths are separated by a rule rather than boxed, so a long list stays one calm form.
  path: {
    margin: 0,
    minWidth: 0,
    padding: `${tokens.spacingVerticalM} 0 0`,
    border: 'none',
    borderTop: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  legend: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    padding: 0,
    fontWeight: tokens.fontWeightSemibold,
    overflowWrap: 'anywhere',
  },
  // Each step shows its sub-finding's report number, so the list drops its own markers to avoid two numbers.
  steps: { margin: 0, padding: 0, listStyleType: 'none' },
  step: { paddingBlock: tokens.spacingVerticalXXS, overflowWrap: 'anywhere' },
  // The controls stay beside their step on narrow screens; a long title wraps instead.
  stepRow: {
    display: 'flex',
    flexWrap: 'nowrap',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalS,
  },
  stepTitle: { flex: '1 1 auto', minWidth: 0 },
  stepActions: { display: 'flex', flexShrink: 0, gap: tokens.spacingHorizontalXS },
  missing: { color: tokens.colorStatusWarningForeground1 },
  muted: { color: tokens.colorNeutralForeground3 },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: tokens.spacingHorizontalS },
  grow: { flex: '1 1 14rem', minWidth: 0 },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
})
