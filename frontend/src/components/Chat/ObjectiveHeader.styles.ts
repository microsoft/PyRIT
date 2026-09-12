import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
  mobileTouchTargetHeight,
} from '../../styles/touchTargets'

export const useObjectiveHeaderStyles = makeStyles({
  root: {
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    backgroundColor: tokens.colorNeutralBackground2,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    borderLeft: `3px solid ${tokens.colorBrandStroke1}`,
    [NARROW_VIEWPORT_QUERY]: {
      alignItems: 'stretch',
    },
  },
  emptyRoot: {
    alignItems: 'stretch',
  },
  headerSection: {
    display: 'flex',
    flexDirection: 'row',
    alignItems: 'baseline',
    columnGap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalL}`,
    minWidth: 0,
    [NARROW_VIEWPORT_QUERY]: {
      alignItems: 'center',
      flexWrap: 'wrap',
      rowGap: tokens.spacingVerticalS,
      padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    },
  },
  label: {
    flexShrink: 0,
  },
  outcomeSection: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    flexShrink: 0,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalL} ${tokens.spacingVerticalS}`,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    [NARROW_VIEWPORT_QUERY]: {
      padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalM} ${tokens.spacingVerticalS}`,
    },
  },
  outcomeButton: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 0,
    padding: 0,
    ...mobileTouchTargetHeight,
  },
  resultPopover: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    width: 'min(420px, calc(100vw - 32px))',
    maxHeight: 'calc(100vh - 32px)',
    overflowY: 'auto',
  },
  resultScoreRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
    minWidth: 0,
  },
  scorerIdentity: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    padding: tokens.spacingVerticalS,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  identityValue: {
    overflowWrap: 'anywhere',
  },
  resultActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: tokens.spacingHorizontalS,
  },
  resultAction: {
    ...mobileTouchTargetHeight,
  },
  scoreValueButton: {
    fontSize: tokens.fontSizeBase300,
    fontWeight: tokens.fontWeightRegular,
  },
  scoreValueText: {
    fontSize: tokens.fontSizeBase300,
  },
  verdictOptions: {
    display: 'flex',
    justifyContent: 'center',
  },
  content: {
    flexGrow: 1,
    minWidth: 0,
    color: tokens.colorNeutralForeground1,
    fontSize: tokens.fontSizeBase300,
  },
  contentCollapsed: {
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  contentExpanded: {
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    maxHeight: '30vh',
    overflowY: 'auto',
  },
  input: {
    flexGrow: 1,
    minWidth: 0,
    ...mobileTouchTargetHeight,
    '& input': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    [NARROW_VIEWPORT_QUERY]: {
      flexBasis: '100%',
      order: 2,
    },
  },
  addButton: {
    ...mobileTouchTargetHeight,
  },
  toggle: {
    flexShrink: 0,
    minWidth: 'auto',
    whiteSpace: 'nowrap',
    color: tokens.colorBrandForeground1,
    ...mobileTouchTargetHeight,
  },
  editorAction: {
    ...mobileTouchTargetHeight,
    [NARROW_VIEWPORT_QUERY]: {
      order: 3,
    },
  },
})
