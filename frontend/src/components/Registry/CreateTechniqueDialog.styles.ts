import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useCreateTechniqueDialogStyles = makeStyles({
  surface: { width: 'min(640px, 90vw)', maxWidth: '90vw', maxHeight: '90vh' },
  content: { overflowY: 'auto', maxHeight: '65vh' },
  form: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  action: { ...mobileTouchTargetHeight },
})
