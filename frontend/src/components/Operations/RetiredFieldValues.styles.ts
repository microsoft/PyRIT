import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

const DASHED_PANEL = {
  padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
  backgroundColor: tokens.colorNeutralBackground2,
  border: `${tokens.strokeWidthThin} dashed ${tokens.colorNeutralStroke1}`,
  borderRadius: tokens.borderRadiusMedium,
}

export const useRetiredFieldValuesStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  group: { ...DASHED_PANEL, display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXS },
  heading: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
  },
  list: {
    margin: 0,
    padding: 0,
    listStyleType: 'none',
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  item: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: tokens.spacingHorizontalM,
  },
  entry: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXXS, minWidth: 0 },
  key: {
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    overflowWrap: 'anywhere',
  },
  titleKey: { fontFamily: tokens.fontFamilyMonospace },
  value: { margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', userSelect: 'text' },
  note: {
    ...DASHED_PANEL,
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    color: tokens.colorNeutralForeground3,
  },
  button: { flexShrink: 0, ...mobileTouchTarget },
})
