import { makeStyles, tokens } from '@fluentui/react-components'

export const useNavigationStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    padding: tokens.spacingVerticalM,
    alignItems: 'center',
    gap: tokens.spacingVerticalM,
  },
  navButton: {
    width: '44px',
    height: '44px',
    minWidth: '44px',
    padding: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    '&[data-active="true"]': {
      backgroundColor: tokens.colorBrandBackground2,
      borderRadius: tokens.borderRadiusMedium,
    },
  },
  spacer: {
    flex: 1,
  },
})
