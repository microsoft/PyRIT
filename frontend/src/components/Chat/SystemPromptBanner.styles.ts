import { makeStyles, tokens } from '@fluentui/react-components'

export const useSystemPromptBannerStyles = makeStyles({
  root: {
    flexShrink: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalL}`,
    backgroundColor: tokens.colorNeutralBackground3,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
  },
  header: {
    alignSelf: 'flex-start',
    color: tokens.colorNeutralForeground2,
  },
  label: {
    alignSelf: 'flex-start',
    color: tokens.colorNeutralForeground2,
    fontWeight: tokens.fontWeightSemibold,
  },
  content: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    paddingLeft: tokens.spacingHorizontalL,
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
})
