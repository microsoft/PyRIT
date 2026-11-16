import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useOperationParentLinkStyles = makeStyles({
  link: {
    ...mobileTouchTargetHeight,
    display: 'inline-flex',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: tokens.spacingHorizontalXS,
    maxWidth: '100%',
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase200,
    fontWeight: tokens.fontWeightSemibold,
    textDecorationLine: 'none',
    overflowWrap: 'anywhere',
    ':hover': {
      color: tokens.colorBrandForegroundLinkHover,
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `${tokens.strokeWidthThick} solid ${tokens.colorStrokeFocus2}`,
      borderRadius: tokens.borderRadiusSmall,
    },
  },
  icon: {
    flexShrink: 0,
    fontSize: tokens.fontSizeBase400,
  },
})
