import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useScorerEvaluationDetailsStyles = makeStyles({
  trigger: {
    ...mobileTouchTarget,
    color: tokens.colorBrandForegroundLink,
  },
  surface: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    width: '28rem',
    maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL} - ${tokens.spacingHorizontalXXL})`,
    maxHeight: '70vh',
    overflowY: 'auto',
    overflowWrap: 'anywhere',
  },
  heading: { margin: 0 },
  description: { color: tokens.colorNeutralForeground2 },
})
