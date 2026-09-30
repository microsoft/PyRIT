import { makeStyles, tokens } from '@fluentui/react-components'
import {
  TOUCH_INPUT_QUERY,
  MINIMUM_TOUCH_TARGET_SIZE,
  mobileTouchTarget,
  mobileTouchTargetHeight,
} from '../../styles/touchTargets'

// Room for the filter name, a long choice, and the "(+N)" count; longer text ellipsizes.
const FILTER_WIDTH = '17rem'
// The touch-sized open button takes more of the width.
const TOUCH_FILTER_WIDTH = '18.5rem'

export const useTargetFiltersBarStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'flex-start',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    minWidth: 0,
    marginBottom: tokens.spacingVerticalS,
  },
  resetSlot: {
    // As tall as a dropdown, so the button centers on the row of dropdowns beside it.
    display: 'flex',
    alignItems: 'center',
    minHeight: '32px',
  },
  filters: {
    // Wrapped dropdowns line up under the first one instead of under the reset button.
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    flexGrow: 1,
    flexShrink: 1,
    flexBasis: FILTER_WIDTH,
    minWidth: 0,
    [TOUCH_INPUT_QUERY]: {
      flexBasis: TOUCH_FILTER_WIDTH,
    },
  },
  filterDropdown: {
    minWidth: `min(100%, ${FILTER_WIDTH})`,
    [TOUCH_INPUT_QUERY]: {
      minWidth: `min(100%, ${TOUCH_FILTER_WIDTH})`,
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
    '& > input': {
      textOverflow: 'ellipsis',
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    '& > [role="button"]': {
      [TOUCH_INPUT_QUERY]: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: MINIMUM_TOUCH_TARGET_SIZE,
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
  option: {
    ...mobileTouchTargetHeight,
  },
  resetButton: {
    ...mobileTouchTarget,
  },
})
