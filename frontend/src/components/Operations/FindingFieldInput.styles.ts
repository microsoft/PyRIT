import { makeStyles, tokens } from '@fluentui/react-components'

const LISTBOX_MAX_HEIGHT = '240px'

export const useFindingFieldInputStyles = makeStyles({
  levels: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  picker: { minWidth: 0, width: '100%' },
  listbox: { maxHeight: LISTBOX_MAX_HEIGHT, overflowY: 'auto' },
})
