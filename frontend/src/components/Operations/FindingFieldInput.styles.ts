import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

const LISTBOX_MAX_HEIGHT = '240px'
// The wrapper's border would leave the inner input short of the target, so both grow.
const TOUCH_HEIGHT = { ...mobileTouchTargetHeight, '& > input': mobileTouchTargetHeight }

export const useFindingFieldInputStyles = makeStyles({
  levels: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  picker: { minWidth: 0, width: '100%', ...TOUCH_HEIGHT },
  input: TOUCH_HEIGHT,
  listbox: { maxHeight: LISTBOX_MAX_HEIGHT, overflowY: 'auto' },
})
