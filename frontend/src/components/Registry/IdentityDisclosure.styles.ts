import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useIdentityDisclosureStyles = makeStyles({
  toggle: {
    ...mobileTouchTargetHeight,
    justifyContent: 'flex-start',
    textAlign: 'left',
    overflowWrap: 'anywhere',
    fontFamily: tokens.fontFamilyBase,
    fontSize: tokens.fontSizeBase300,
    fontWeight: tokens.fontWeightRegular,
    lineHeight: tokens.lineHeightBase300,
  },
  inlineToggle: {
    minWidth: 0,
    minHeight: 0,
    padding: 0,
    color: tokens.colorNeutralForeground1,
  },
  details: {
    paddingBlock: tokens.spacingVerticalM,
  },
  json: {
    margin: 0,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalM}`,
    backgroundColor: tokens.colorNeutralBackground2,
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase200,
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
  },
  disclosure: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    minWidth: 0,
  },
  inlineDetails: {
    alignSelf: 'stretch',
  },
})
