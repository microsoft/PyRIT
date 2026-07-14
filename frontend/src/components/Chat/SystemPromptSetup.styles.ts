import { makeStyles, tokens } from '@fluentui/react-components'

export const useSystemPromptSetupStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalL} 0`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
  },
  header: {
    color: tokens.colorNeutralForeground2,
  },
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    paddingBottom: tokens.spacingVerticalS,
  },
  textareaRoot: {
    width: '100%',
  },
  textareaInner: {
    minHeight: '96px',
    maxHeight: '30vh',
  },
  counter: {
    alignSelf: 'flex-end',
    color: tokens.colorNeutralForeground3,
  },
  counterOver: {
    color: tokens.colorPaletteYellowForeground2,
  },
  reason: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXXS,
    color: tokens.colorPaletteYellowForeground2,
  },
})
