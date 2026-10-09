import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingEvidenceDialogStyles = makeStyles({
  content: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  pickerHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: tokens.spacingHorizontalM },
  recovery: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  list: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  context: { display: 'block', color: tokens.colorNeutralForeground3 },
  findingHeading: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
  pagination: { display: 'flex', alignItems: 'center', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
})
