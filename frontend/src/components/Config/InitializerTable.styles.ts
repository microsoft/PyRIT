import { makeStyles, tokens } from '@fluentui/react-components'

export const useInitializerTableStyles = makeStyles({
  tableContainer: {
    width: '100%',
    overflowX: 'auto',
  },
  table: {
    width: '100%',
    tableLayout: 'fixed',
  },
  cell: {
    verticalAlign: 'top',
    paddingTop: tokens.spacingVerticalM,
  },
  parametersCell: {
    minWidth: '26rem',
    verticalAlign: 'top',
    paddingTop: tokens.spacingVerticalM,
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
  actionsCell: {
    verticalAlign: 'top',
    paddingTop: tokens.spacingVerticalM,
  },
  actionsColumn: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  sourceBadge: {
    textTransform: 'capitalize',
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
})
