import { makeStyles, tokens } from '@fluentui/react-components'

export const useScenarioModuleCardStyles = makeStyles({
  purposePreview: {
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase300,
    '& p': {
      marginBottom: tokens.spacingVerticalXS,
    },
    '& ul, & ol': {
      marginBottom: tokens.spacingVerticalXS,
    },
    '& h1, & h2, & h3, & h4, & h5, & h6': {
      margin: `${tokens.spacingVerticalXS} 0`,
      fontSize: tokens.fontSizeBase300,
      lineHeight: tokens.lineHeightBase300,
    },
    // Links in a description stay clickable above the row's stretched link.
    '& a': {
      position: 'relative',
      zIndex: 1,
    },
  },
})
