import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useTechniqueEditorStyles = makeStyles({
  surface: {
    width: '100%', minWidth: 0, maxWidth: '37.5rem', maxHeight: '90vh',
    [NARROW_VIEWPORT_QUERY]: {
      maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL} - ${tokens.spacingHorizontalXXL})`,
    },
  },
  // The surface's height less its padding and border, so the form scrolls instead of pushing the actions out.
  body: { minHeight: 0, maxHeight: 'calc(90vh - 50px)' },
  content: { minWidth: 0, minHeight: 0, overflowY: 'auto', maxHeight: '65vh' },
  // DialogBody is a grid with the actions in its third row; while the warning shows, it takes that row and the
  // actions move below it.
  temporaryWarning: { gridRowStart: 3, gridRowEnd: 3, gridColumnStart: 1, gridColumnEnd: 4 },
  actionsBelowWarning: { gridRowStart: 4, gridRowEnd: 4 },
  form: {
    display: 'flex', flexDirection: 'column', width: '100%', minWidth: 0,
    maxWidth: '100%', gap: tokens.spacingVerticalL,
  },
  select: { width: '100%', minWidth: 0, '& select': { width: '100%', minWidth: 0 } },
  section: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  prompt: { minHeight: '7rem' },
  action: { ...mobileTouchTargetHeight },
})
