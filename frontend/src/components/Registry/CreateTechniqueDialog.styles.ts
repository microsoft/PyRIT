import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useCreateTechniqueDialogStyles = makeStyles({
  surface: {
    width: '100%', minWidth: 0, maxWidth: '37.5rem', maxHeight: '90vh',
    [NARROW_VIEWPORT_QUERY]: {
      maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL} - ${tokens.spacingHorizontalXXL})`,
    },
  },
  content: { minWidth: 0, overflowY: 'auto', maxHeight: '65vh' },
  form: {
    display: 'flex', flexDirection: 'column', width: '100%', minWidth: 0,
    maxWidth: '100%', gap: tokens.spacingVerticalL,
  },
  select: { width: '100%', minWidth: 0, '& select': { width: '100%', minWidth: 0 } },
  action: { ...mobileTouchTargetHeight },
})
