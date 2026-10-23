import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useFindingExportDialogStyles = makeStyles({
  content: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  messageBar: { flexShrink: 0 },
  list: { margin: 0, padding: 0, listStyleType: 'none' },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: tokens.spacingHorizontalS, minWidth: 0 },
  checkbox: { flex: '1 1 12rem', minWidth: 0 },
  label: { minWidth: 0, overflowWrap: 'anywhere' },
  button: { ...mobileTouchTargetHeight },
})
