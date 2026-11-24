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
