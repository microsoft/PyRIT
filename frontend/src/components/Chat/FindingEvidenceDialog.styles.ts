import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingEvidenceDialogStyles = makeStyles({
  content: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  pickerHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: tokens.spacingHorizontalM },
  list: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  findingLabel: { minWidth: 0, overflowWrap: 'anywhere' },
  context: { display: 'block', color: tokens.colorNeutralForeground3 },
  findingHeading: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
  findingTitle: { minWidth: 0 },
     pagination: { display: 'flex', alignItems: 'center', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
})
