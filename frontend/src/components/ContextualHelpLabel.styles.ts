import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useContextualHelpLabelStyles = makeStyles({
  label: {
    display: 'inline-flex',
    alignItems: 'center',
    maxWidth: '100%',
    cursor: 'help',
    fontSize: 'inherit',
    lineHeight: 'inherit',
    borderRadius: tokens.borderRadiusSmall,
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
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
