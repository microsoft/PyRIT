import { makeStyles, tokens } from '@fluentui/react-components'

export const useInitializerSettingsDialogStyles = makeStyles({
  dialogSurface: {
    width: '100%',
    minWidth: 0,
    maxWidth: '50rem',
    '@media (max-width: 600px)': {
      maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL} - ${tokens.spacingHorizontalXXL})`,
    },
  },
  dialogContent: {
    minWidth: 0,
  },
})
