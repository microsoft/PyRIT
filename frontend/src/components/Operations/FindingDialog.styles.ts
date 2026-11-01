import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingDialogStyles = makeStyles({
  form: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  section: { margin: 0, marginTop: tokens.spacingVerticalS },
  actions: { display: 'flex', justifyContent: 'flex-end', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
})
