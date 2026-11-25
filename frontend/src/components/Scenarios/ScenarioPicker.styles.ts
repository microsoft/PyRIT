import { makeStyles } from '@fluentui/react-components'

export const useScenarioPickerStyles = makeStyles({
  listbox: {
    // Keeps a long scenario list inside the window when the picker sits in a dialog.
    maxHeight: 'min(20rem, calc(100vh - 10rem))',
    overflowY: 'auto',
  },
})
