import { makeStyles, tokens } from '@fluentui/react-components'

export const useAdditionalInitializersStyles = makeStyles({
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    width: '100%',
  },
  card: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  cardHeader: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
  },
  titleGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  parameterList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    marginBottom: tokens.spacingVerticalS,
  },
  parameterHint: {
    color: tokens.colorNeutralForeground3,
  },
  parametersEditor: {
    fontFamily: 'Consolas, "Courier New", monospace',
    minHeight: '10rem',
    width: '100%',
  },
  parametersBlock: {
    margin: 0,
    marginTop: tokens.spacingVerticalXS,
    padding: tokens.spacingVerticalM,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground3,
    overflowX: 'auto',
    fontFamily: 'Consolas, "Courier New", monospace',
  },
  dialogContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  actionsRow: {
    display: 'flex',
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  errorText: {
    color: tokens.colorPaletteRedForeground1,
    marginTop: tokens.spacingVerticalXS,
  },
  envVarText: {
    color: tokens.colorNeutralForeground3,
    display: 'block',
    marginTop: tokens.spacingVerticalXXS,
  },
  parameterFields: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
})
