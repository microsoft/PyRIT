import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useSeedExampleDetailStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    width: '100%',
    minWidth: 0,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  heading: {
    margin: 0,
    overflowWrap: 'anywhere',
    ':focus': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
  member: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    minWidth: 0,
    paddingTop: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  memberTitle: {
    margin: 0,
    overflowWrap: 'anywhere',
  },
  fields: {
    display: 'grid',
    gridTemplateColumns: 'minmax(8rem, 14rem) minmax(0, 1fr)',
    gap: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalM}`,
    margin: 0,
    minWidth: 0,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
    },
  },
  term: {
    margin: 0,
    color: tokens.colorNeutralForeground3,
    overflowWrap: 'anywhere',
  },
  detail: {
    margin: 0,
    minWidth: 0,
    overflowWrap: 'anywhere',
    [NARROW_VIEWPORT_QUERY]: {
      marginBottom: tokens.spacingVerticalS,
    },
  },
  badges: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
    minWidth: 0,
    maxWidth: '100%',
  },
  valueBadge: {
    '&&': {
      maxWidth: '100%',
      minWidth: 0,
      height: 'auto',
      minHeight: '20px',
      whiteSpace: 'normal',
      overflowWrap: 'anywhere',
      wordBreak: 'break-word',
      textAlign: 'start',
    },
  },
  value: {
    margin: 0,
    maxWidth: '100%',
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase200,
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    minWidth: 0,
  },
  touchTarget: {
    ...mobileTouchTargetHeight,
    maxWidth: '100%',
  },
  note: {
    margin: 0,
    maxWidth: '70ch',
    color: tokens.colorNeutralForeground2,
  },
  list: {
    margin: 0,
    paddingInlineStart: tokens.spacingHorizontalL,
    minWidth: 0,
  },
})
