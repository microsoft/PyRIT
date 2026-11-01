import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useAttackExecutionTableStyles = makeStyles({
  tableScroll: {
    width: '100%',
    overflowX: 'auto',
    border: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  table: { minWidth: '64rem', tableLayout: 'auto' },
  headerRow: { backgroundColor: tokens.colorNeutralBackground3 },
  resultRow: {
    cursor: 'pointer',
    ':hover': { backgroundColor: tokens.colorNeutralBackground1Hover },
    ':focus-visible': {
      outline: `${tokens.strokeWidthThick} solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: `-${tokens.strokeWidthThick}`,
    },
  },
  resultCell: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    minWidth: '18rem',
  },
  identity: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
    overflowWrap: 'anywhere',
  },
  objectiveLink: {
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    ':hover': { textDecorationLine: 'underline' },
  },
  expandSpacer: { width: '2rem', flexShrink: 0, ...mobileTouchTarget },
  position: { color: tokens.colorNeutralForeground3, fontVariantNumeric: 'tabular-nums' },
  hint: { color: tokens.colorNeutralForeground3 },
  unresolved: {
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic',
    overflowWrap: 'anywhere',
  },
  nowrap: { whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' },
  footer: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    paddingTop: tokens.spacingVerticalXS,
  },
  touchTarget: { flexShrink: 0, ...mobileTouchTarget },
})
