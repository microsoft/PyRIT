import { makeStyles, tokens } from '@fluentui/react-components'

import { NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAttackActivityStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalL,
    paddingBlock: tokens.spacingVerticalM,
  },
  flow: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    color: tokens.colorBrandForeground1,
    fontSize: tokens.fontSizeBase500,
    flexShrink: 0,
  },
  connection: {
    display: 'inline-flex',
    animationName: {
      '0%, 100%': { opacity: 0.35, transform: 'translateX(-2px)' },
      '50%': { opacity: 1, transform: 'translateX(2px)' },
    },
    animationDuration: '1800ms',
    animationIterationCount: 'infinite',
    animationTimingFunction: 'ease-in-out',
    '@media (prefers-reduced-motion: reduce)': { animationName: 'none' },
  },
  copy: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    [NARROW_VIEWPORT_QUERY]: { flexBasis: '100%' },
  },
  hint: {
    color: tokens.colorNeutralForeground2,
  },
})
