import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAnalyticsDimensionPickerStyles = makeStyles({
  root: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'end',
    gap: tokens.spacingHorizontalS,
    minWidth: 0,
    [NARROW_VIEWPORT_QUERY]: { width: '100%' },
  },
  field: {
    minWidth: '10rem',
    maxWidth: '100%',
    [NARROW_VIEWPORT_QUERY]: { flex: 1 },
  },
  input: { ...mobileTouchTargetHeight },
  button: { ...mobileTouchTargetHeight },
})
