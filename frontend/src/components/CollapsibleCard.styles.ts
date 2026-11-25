import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useCollapsibleCardStyles = makeStyles({
  root: {
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    minWidth: 0,
  },
  attention: {
    borderLeft: `${tokens.strokeWidthThickest} solid ${tokens.colorStatusWarningBorderActive}`,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    columnGap: tokens.spacingHorizontalM,
    rowGap: tokens.spacingVerticalXS,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    [NARROW_VIEWPORT_QUERY]: {
      alignItems: 'flex-start',
    },
  },
  header: {
    minWidth: 0,
  },
  headerButton: {
    minWidth: 0,
    ...mobileTouchTargetHeight,
    paddingLeft: 0,
    overflowWrap: 'anywhere',
  },
  title: {
    display: 'inline-flex',
    alignItems: 'baseline',
    flexWrap: 'wrap',
    columnGap: tokens.spacingHorizontalS,
    minWidth: 0,
  },
  meta: {
    color: tokens.colorNeutralForeground3,
  },
  badges: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    marginLeft: 'auto',
    [NARROW_VIEWPORT_QUERY]: {
      marginLeft: 0,
      width: '100%',
    },
  },
  description: {
    display: 'block',
    color: tokens.colorNeutralForeground2,
    padding: `0 ${tokens.spacingHorizontalM} ${tokens.spacingVerticalM} ${tokens.spacingHorizontalXXXL}`,
    overflowWrap: 'anywhere',
    [NARROW_VIEWPORT_QUERY]: {
      paddingLeft: tokens.spacingHorizontalM,
    },
  },
  panel: {
    margin: 0,
    padding: `0 ${tokens.spacingHorizontalM} ${tokens.spacingVerticalM}`,
  },
})
