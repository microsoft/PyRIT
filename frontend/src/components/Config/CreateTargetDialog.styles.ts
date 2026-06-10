import { makeStyles, tokens } from '@fluentui/react-components'

export const useCreateTargetDialogStyles = makeStyles({
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
  },
  warningMessage: {
    width: '100%',
  },
  warningMessageBody: {
    whiteSpace: 'normal',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
  },
  /** Container for the list of selected inner targets in the RoundRobin form. */
  selectedTargetsList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  /** A single row in the selected targets list: target name + weight + remove button. */
  selectedTargetRow: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    backgroundColor: tokens.colorNeutralBackground2,
    borderRadius: tokens.borderRadiusSmall,
  },
})
