import { makeStyles } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useContextualHelpLabelStyles = makeStyles({
  root: {
    display: 'inline-flex',
    alignItems: 'center',
    maxWidth: '100%',
  },
  label: {
    cursor: 'help',
    fontSize: 'inherit',
    lineHeight: 'inherit',
  },
  infoButton: {
    ...mobileTouchTarget,
  },
  content: {
    maxWidth: '20rem',
    maxHeight: '50vh',
    overflowY: 'auto',
    overflowWrap: 'anywhere',
    whiteSpace: 'pre-line',
  },
  srOnly: {
    position: 'absolute',
    width: '1px',
    height: '1px',
    overflow: 'hidden',
    clip: 'rect(0, 0, 0, 0)',
    whiteSpace: 'nowrap',
  },
})
