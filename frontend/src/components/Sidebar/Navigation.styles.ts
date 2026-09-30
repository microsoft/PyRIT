import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useNavigationStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minHeight: 0,
    overflowY: 'auto',
    overflowX: 'hidden',
    padding: tokens.spacingVerticalM,
    alignItems: 'center',
    gap: tokens.spacingVerticalM,
  },
  primaryNavigation: {
    display: 'flex',
    flexDirection: 'column',
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
  themeMenu: {
    maxHeight: `calc(100dvh - ${tokens.spacingVerticalXXL})`,
    maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL})`,
    overflowY: 'auto',
  },
  themeOption: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalXL,
  },
  themeMenuItem: {
    ...mobileTouchTargetHeight,
  },
  themePreview: {
    display: 'inline-block',
    flexShrink: 0,
    width: tokens.spacingHorizontalXXXL,
    height: tokens.spacingVerticalXL,
    borderRadius: tokens.borderRadiusSmall,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundSize: 'cover',
    backgroundPosition: 'right bottom',
    '@media (forced-colors: active)': {
      display: 'none',
    },
  },
})
