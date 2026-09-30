import { makeStyles, tokens } from '@fluentui/react-components'

export const useMarkdownContentStyles = makeStyles({
  root: {
    wordBreak: 'break-word',
    // Collapse outer block margins so the renderer composes cleanly in chat,
    // catalog, and detail surfaces.
    '& > :first-child': { marginTop: 0 },
    '& > :last-child': { marginBottom: 0 },
    '& p': {
      margin: `0 0 ${tokens.spacingVerticalM} 0`,
      lineHeight: tokens.lineHeightBase300,
    },
    '& h1, & h2, & h3, & h4, & h5, & h6': {
      margin: `${tokens.spacingVerticalM} 0 ${tokens.spacingVerticalS} 0`,
      lineHeight: tokens.lineHeightBase400,
      fontWeight: tokens.fontWeightSemibold,
    },
    '& ul, & ol': {
      margin: `0 0 ${tokens.spacingVerticalM} 0`,
      paddingLeft: tokens.spacingHorizontalXL,
    },
    '& li': {
      marginBottom: tokens.spacingVerticalXS,
    },
    '& a': {
      color: tokens.colorBrandForegroundLink,
      textDecorationLine: 'underline',
    },
    '& code': {
      fontFamily: tokens.fontFamilyMonospace,
      fontSize: tokens.fontSizeBase200,
      backgroundColor: tokens.colorNeutralBackground3,
      padding: `0 ${tokens.spacingHorizontalXS}`,
      borderRadius: tokens.borderRadiusSmall,
    },
    '& pre': {
      margin: `0 0 ${tokens.spacingVerticalM} 0`,
      padding: tokens.spacingHorizontalS,
      backgroundColor: tokens.colorNeutralBackground3,
      border: `1px solid ${tokens.colorNeutralStroke2}`,
      borderRadius: tokens.borderRadiusMedium,
      overflowX: 'auto',
    },
    // Code inside a fenced block should not repeat the inline chip styling.
    '& pre code': {
      padding: 0,
      backgroundColor: 'transparent',
    },
    '& blockquote': {
      margin: `0 0 ${tokens.spacingVerticalM} 0`,
      paddingLeft: tokens.spacingHorizontalM,
      borderLeft: `1px solid ${tokens.colorNeutralStroke1}`,
      color: tokens.colorNeutralForeground2,
    },
    '& table': {
      borderCollapse: 'collapse',
      margin: `0 0 ${tokens.spacingVerticalM} 0`,
    },
    '& th, & td': {
      border: `1px solid ${tokens.colorNeutralStroke2}`,
      padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
      textAlign: 'left',
    },
    '& img': {
      maxWidth: '100%',
      height: 'auto',
    },
  },
})
