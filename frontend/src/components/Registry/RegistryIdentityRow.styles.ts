import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useRegistryIdentityRowStyles = makeStyles({
  row: {
    backgroundColor: tokens.colorNeutralBackground1,
  },
  name: {
    ...mobileTouchTargetHeight,
    paddingBlock: tokens.spacingVerticalS,
    justifyContent: 'flex-start',
    textAlign: 'left',
    overflowWrap: 'anywhere',
    fontFamily: tokens.fontFamilyBase,
    fontSize: tokens.fontSizeBase300,
    fontWeight: tokens.fontWeightRegular,
    lineHeight: tokens.lineHeightBase300,
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
})
