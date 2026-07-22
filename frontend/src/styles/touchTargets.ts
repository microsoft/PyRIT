export const MOBILE_BREAKPOINT = '@media (max-width: 600px)'
export const MINIMUM_TOUCH_TARGET_SIZE = '44px'

export const mobileTouchTarget = {
  [MOBILE_BREAKPOINT]: {
    minWidth: MINIMUM_TOUCH_TARGET_SIZE,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
}

export const mobileTouchTargetHeight = {
  [MOBILE_BREAKPOINT]: {
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
}
