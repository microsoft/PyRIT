import { makeStyles } from '@fluentui/react-components'

import { mobileTouchTargetHeight, TOUCH_INPUT_QUERY, MINIMUM_TOUCH_TARGET_SIZE } from '@/styles/touchTargets'

export const useTargetSelectStyles = makeStyles({
  select: {
    minWidth: 0,
    width: '100%',
    ...mobileTouchTargetHeight,
    '& > select': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
})
