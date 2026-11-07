import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAttackAttemptDetailsStyles = makeStyles({
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXL,
    minWidth: 0,
    overflowWrap: 'anywhere',
  },
  framed: {
    padding: `${tokens.spacingVerticalXL} ${tokens.spacingHorizontalXL}`,
    border: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    [NARROW_VIEWPORT_QUERY]: {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalL}`,
    },
  },
  divided: {
    paddingTop: tokens.spacingVerticalXL,
    borderTop: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
  },
  components: {
    rowGap: tokens.spacingVerticalM,
  },
  objective: {
    display: 'block',
    maxWidth: '75ch',
    whiteSpace: 'pre-wrap',
  },
  rationale: {
    maxWidth: '75ch',
    whiteSpace: 'pre-wrap',
    color: tokens.colorNeutralForeground2,
  },
  meta: {
    color: tokens.colorNeutralForeground3,
    fontVariantNumeric: 'tabular-nums',
  },
  error: {
    alignSelf: 'stretch',
  },
  findings: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    margin: 0,
    padding: 0,
    listStyleType: 'none',
  },
  finding: {
    display: 'grid',
    gridTemplateColumns: 'auto minmax(0, 1fr)',
    alignItems: 'baseline',
    gap: tokens.spacingHorizontalS,
  },
  actions: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
    [NARROW_VIEWPORT_QUERY]: { flexDirection: 'column', alignItems: 'stretch' },
  },
  conversationLink: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    boxSizing: 'border-box',
    color: tokens.colorBrandForegroundLink,
    backgroundColor: tokens.colorBrandBackground2,
    borderRadius: tokens.borderRadiusMedium,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    fontWeight: tokens.fontWeightSemibold,
    textDecorationLine: 'none',
    ':hover': { backgroundColor: tokens.colorBrandBackground2Hover },
    [NARROW_VIEWPORT_QUERY]: { justifyContent: 'center' },
  },
})
