import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  mobileTouchTargetHeight,
  TOUCH_INPUT_QUERY,
} from '@/styles/touchTargets'

export const useParameterFieldStyles = makeStyles({
  control: {
    ...mobileTouchTargetHeight,
    '& > select': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    '& > input': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    '& > textarea': {
      minHeight: '6rem',
      fontFamily: tokens.fontFamilyMonospace,
      [TOUCH_INPUT_QUERY]: {
        minHeight: '6rem',
      },
    },
  },
  selectionControl: {
    ...mobileTouchTargetHeight,
  },
  checkboxGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  srOnly: {
    position: 'absolute',
    width: '1px',
    height: '1px',
    padding: '0',
    margin: '-1px',
    overflow: 'hidden',
    clip: 'rect(0,0,0,0)',
    whiteSpace: 'nowrap',
  },
  fieldHint: {
    color: tokens.colorNeutralForeground3,
    marginTop: tokens.spacingVerticalXXS,
  },
})
