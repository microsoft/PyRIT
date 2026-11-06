import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useNavigationStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minHeight: 0,
    overflowY: 'auto',
    overflowX: 'hidden',
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalS}`,
    alignItems: 'stretch',
    gap: tokens.spacingVerticalS,
  },
  expanded: {
    '& button': {
      width: '100%',
      justifyContent: 'flex-start',
      paddingInline: tokens.spacingHorizontalM,
      gap: tokens.spacingHorizontalM,
    },
  },
  primaryNavigation: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: tokens.spacingVerticalXS,
  },
  navButton: {
    width: '44px',
    height: '44px',
    minWidth: '44px',
    padding: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    flexShrink: 0,
    color: tokens.colorNeutralForeground2,
    borderRadius: tokens.borderRadiusMedium,
    ...mobileTouchTarget,
    '&[data-active="true"]': {
      backgroundColor: tokens.colorBrandBackground2,
      color: tokens.colorBrandForeground1,
      fontWeight: tokens.fontWeightSemibold,
      position: 'relative',
      '::before': {
        content: '""',
        position: 'absolute',
        left: 0,
        width: tokens.strokeWidthThick,
        height: tokens.spacingVerticalXL,
        borderRadius: tokens.borderRadiusCircular,
        backgroundColor: tokens.colorBrandForeground1,
      },
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
