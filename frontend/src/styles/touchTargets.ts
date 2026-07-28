// Screen width only - for layout/reflow decisions (wrapping, stacking,
// hiding columns).
export const NARROW_VIEWPORT_QUERY = '@media (max-width: 600px)'

// Input mechanism only - for sizing hit areas for a finger vs. a cursor.
export const TOUCH_INPUT_QUERY = '@media (pointer: coarse)'

export const MINIMUM_TOUCH_TARGET_SIZE = '2.75rem'

export const mobileTouchTarget = {
  [TOUCH_INPUT_QUERY]: {
    minWidth: MINIMUM_TOUCH_TARGET_SIZE,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
}

export const mobileTouchTargetHeight = {
  [TOUCH_INPUT_QUERY]: {
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
}
