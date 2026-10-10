import { makeStyles } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  mobileTouchTargetHeight,
  TOUCH_INPUT_QUERY,
} from '@/styles/touchTargets'

export const useScenarioDatasetFieldsStyles = makeStyles({
  control: {
    ...mobileTouchTargetHeight,
    '& > input': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
  numberInput: {
    maxWidth: '10rem',
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
})
