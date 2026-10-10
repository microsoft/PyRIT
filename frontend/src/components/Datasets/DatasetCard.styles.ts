import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, NARROW_VIEWPORT_QUERY, TOUCH_INPUT_QUERY } from '@/styles/touchTargets'

export const useDatasetCardStyles = makeStyles({
  card: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    minWidth: 0,
    height: '100%',
    padding: tokens.spacingHorizontalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  title: {
    margin: 0,
    color: tokens.colorNeutralForeground1,
    fontSize: tokens.fontSizeBase400,
    lineHeight: tokens.lineHeightBase400,
    fontWeight: tokens.fontWeightSemibold,
    overflowWrap: 'anywhere',
  },
  cardLink: {
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    overflowWrap: 'anywhere',
    ':hover': {
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
    [TOUCH_INPUT_QUERY]: {
      display: 'inline-flex',
      alignItems: 'center',
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  badges: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
  },
  counts: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
    gap: tokens.spacingHorizontalS,
    margin: 0,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
    },
  },
  count: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  countTerm: {
    margin: 0,
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase200,
  },
  countValue: {
    margin: 0,
    color: tokens.colorNeutralForeground1,
    fontWeight: tokens.fontWeightSemibold,
    overflowWrap: 'anywhere',
  },
  metaBlock: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
  },
  metaLabel: {
    color: tokens.colorNeutralForeground3,
  },
  visuallyHidden: {
    position: 'absolute',
    width: '1px',
    height: '1px',
    padding: 0,
    margin: '-1px',
    overflow: 'hidden',
    clip: 'rect(0, 0, 0, 0)',
    whiteSpace: 'nowrap',
    border: 0,
  },
})
