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
  steps: { margin: 0, paddingInlineStart: tokens.spacingHorizontalXL },
  // The list item keeps its number marker; its content lays out in a row inside it.
  step: { paddingBlock: tokens.spacingVerticalXXS, overflowWrap: 'anywhere' },
  stepRow: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalS,
  },
  stepTitle: { minWidth: 0 },
  stepActions: { display: 'flex', gap: tokens.spacingHorizontalXS },
  missing: { color: tokens.colorStatusWarningForeground1 },
  muted: { color: tokens.colorNeutralForeground3 },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: tokens.spacingHorizontalS },
  grow: { flex: '1 1 14rem', minWidth: 0 },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
})
