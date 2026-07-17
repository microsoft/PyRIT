import { makeStyles, tokens } from '@fluentui/react-components'

export const useAttackNotFoundStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    height: '100%',
    padding: tokens.spacingHorizontalXXL,
    textAlign: 'center',
  },
  detail: {
    maxWidth: '420px',
    color: tokens.colorNeutralForeground2,
  },
  code: {
    fontFamily: tokens.fontFamilyMonospace,
    color: tokens.colorNeutralForeground1,
  },
  actions: {
    display: 'flex',
    gap: tokens.spacingHorizontalM,
  },
})
