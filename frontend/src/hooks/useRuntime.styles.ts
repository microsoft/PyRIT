import { makeStyles, tokens } from '@fluentui/react-components'

export const useRuntimeBannerStyles = makeStyles({
  // The app doesn't scroll, so a long list scrolls on its own.
  failureList: {
    maxHeight: '30vh',
    overflowY: 'auto',
  },
  failureDelete: {
    marginLeft: tokens.spacingHorizontalS,
  },
})
