import { makeStyles, tokens } from '@fluentui/react-components'

export const useObjectiveScorerDetailsStyles = makeStyles({
  card: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  documentationLink: {
    alignSelf: 'flex-start',
    color: tokens.colorBrandForegroundLink,
    fontSize: tokens.fontSizeBase200,
  },
})
