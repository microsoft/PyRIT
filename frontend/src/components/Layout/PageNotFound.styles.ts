import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const usePageNotFoundStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    flex: 1,
    minHeight: 0,
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalXXL,
    overflowY: 'auto',
    textAlign: 'center',
  },
  heading: {
    margin: 0,
  },
  homeLink: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: tokens.colorBrandForegroundLink,
    fontWeight: tokens.fontWeightSemibold,
    ...mobileTouchTarget,
  },
})
