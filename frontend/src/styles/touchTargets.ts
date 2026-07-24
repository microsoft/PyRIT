export const MOBILE_BREAKPOINT = '@media (max-width: 600px)'
export const COARSE_POINTER = '@media (pointer: coarse)'
export const MINIMUM_TOUCH_TARGET_SIZE = '2.75rem'

export const mobileTouchTarget = {
  [COARSE_POINTER]: {
    minWidth: MINIMUM_TOUCH_TARGET_SIZE,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
}

export const mobileTouchTargetHeight = {
  [COARSE_POINTER]: {
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
}
