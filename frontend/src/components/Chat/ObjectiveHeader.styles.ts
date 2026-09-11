import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '../../styles/touchTargets'

export const useObjectiveHeaderStyles = makeStyles({
  root: {
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'row',
    alignItems: 'baseline',
    columnGap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalL}`,
    backgroundColor: tokens.colorNeutralBackground2,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    borderLeft: `3px solid ${tokens.colorBrandStroke1}`,
  },
  label: {
    flexShrink: 0,
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
  toggle: {
    flexShrink: 0,
    minWidth: 'auto',
    whiteSpace: 'nowrap',
    color: tokens.colorBrandForeground1,
    ...mobileTouchTargetHeight,
  },
})
