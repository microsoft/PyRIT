import { makeStyles, tokens } from '@fluentui/react-components'

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
    backgroundColor: tokens.colorNeutralBackground2,
    overflow: 'hidden',
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
  newAttackButton: {
    flexShrink: 0,
  },
  newAttackLabel: {
    '@media (max-width: 600px)': {
      display: 'none',
    },
  },
})
