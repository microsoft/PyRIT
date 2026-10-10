import { makeStyles } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useChatTargetPickerStyles = makeStyles({
  root: {
    display: 'inline-flex',
    minWidth: 0,
    maxWidth: '100%',
    '& > span': {
      ...mobileTouchTarget,
    },
  },
  select: {
    position: 'absolute',
    inset: 0,
    width: '100%',
    height: '100%',
    opacity: 0,
    cursor: 'pointer',
    ':disabled': {
      cursor: 'default',
    },
  },
})
