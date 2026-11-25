import { makeStyles, tokens } from '@fluentui/react-components'

export const useLaunchSuiteDialogStyles = makeStyles({
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  summary: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  summaryText: {
    color: tokens.colorNeutralForeground3,
  },
  entryIssue: {
    color: tokens.colorStatusDangerForeground1,
  },
  // Text renders inline; a block label starts below the preset name instead of running on.
  entryScorer: {
    display: 'block',
    margin: 0,
  },
  // DialogContent is a flex column; without this the bar shrinks under its list on narrow screens.
  issueBar: {
    flexShrink: 0,
  },
  issueList: {
    margin: 0,
    paddingLeft: tokens.spacingHorizontalL,
  },
  entryList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    margin: 0,
    paddingLeft: tokens.spacingHorizontalXL,
  },
  entryIdentity: {
    display: 'inline-flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
  },
  numberInput: {
    maxWidth: '10rem',
  },
})
