import { makeStyles } from '@fluentui/react-components'

export const useMobileTouchTargetStyles = makeStyles({
  control: {
    '@media (max-width: 600px)': {
      minWidth: '44px',
      minHeight: '44px',
    },
  },
  field: {
    '@media (max-width: 600px)': {
      minWidth: '44px',
      minHeight: '44px',
      '& input': {
        minHeight: '44px',
      },
      '& select': {
        minHeight: '44px',
      },
    },
  },
  row: {
    '@media (max-width: 600px)': {
      minWidth: '44px',
      minHeight: '44px',
      height: '44px',
    },
  },
})
