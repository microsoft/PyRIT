import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingDialogStyles = makeStyles({
  form: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
  harmPicker: { minWidth: 0, width: '100%' },
  harmListbox: { maxHeight: '240px', overflowY: 'auto' },
})
