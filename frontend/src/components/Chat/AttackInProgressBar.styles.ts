import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAttackInProgressBarStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalL,
    flexShrink: 0,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalXL}`,
    borderTop: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
    [NARROW_VIEWPORT_QUERY]: {
      paddingInline: tokens.spacingHorizontalL,
    },
  },
  scanLink: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    ':hover': { textDecorationLine: 'underline' },
    ':focus-visible': {
      outline: `${tokens.strokeWidthThick} solid ${tokens.colorStrokeFocus2}`,
      borderRadius: tokens.borderRadiusSmall,
    },
  },
})
