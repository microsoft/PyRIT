import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '../../styles/touchTargets'

import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useChatWindowStyles = makeStyles({
  root: {
    display: 'flex',
    height: '100%',
    width: '100%',
    overflow: 'hidden',
  },
  pageHeading: {
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
  chatArea: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
    overflow: 'hidden',
  },
  breadcrumbBar: {
    display: 'flex',
    alignItems: 'center',
    flexShrink: 0,
    minHeight: '36px',
    paddingInline: tokens.spacingHorizontalL,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground3,
    overflowX: 'auto',
  },
  breadcrumbLink: {
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    whiteSpace: 'nowrap',
    ':hover': {
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
  conversationDrawer: {
    width: '280px',
    minWidth: '280px',
    height: '100%',
  },
  narrowConversationDrawer: {
    width: '320px',
    minWidth: 0,
    maxWidth: '100vw',
  },
  ribbon: {
    height: '48px',
    minHeight: '48px',
    flexShrink: 0,
    backgroundColor: tokens.colorNeutralBackground3,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: `0 ${tokens.spacingHorizontalL}`,
    gap: tokens.spacingHorizontalM,
  },
  conversationInfo: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase300,
    flex: '1 1 auto',
    minWidth: 0,
    overflow: 'hidden',
  },
  sharedToolbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-end',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
    maxWidth: '100%',
  },
  sharedTarget: {
    maxWidth: '240px',
    [NARROW_VIEWPORT_QUERY]: {
      flexBasis: '100%',
      maxWidth: '100%',
      justifyContent: 'flex-end',
    },
  },
  noTarget: {
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic',
    flexShrink: 0,
  },
  ribbonActions: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    flexShrink: 0,
  },
  sharedActions: {
    flexShrink: 1,
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
    minWidth: 0,
  },
  ribbonAction: {
    ...mobileTouchTarget,
  },
  newAttackButton: {
    flexShrink: 0,
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: '32px',
    },
    ...mobileTouchTarget,
  },
  newAttackLabel: {
    '@media (max-width: 600px)': {
      display: 'none',
    },
  },
})
