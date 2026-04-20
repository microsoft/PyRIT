import { makeStyles, tokens } from '@fluentui/react-components'

export const useTargetTableStyles = makeStyles({
  tableContainer: {
    flex: 1,
    overflow: 'auto',
  },
  table: {
    tableLayout: 'fixed',
    width: '100%',
  },
  activeRow: {
    backgroundColor: tokens.colorBrandBackground2,
  },
  endpointCell: {
    overflowWrap: 'break-word',
    wordBreak: 'break-all',
  },
  paramsCell: {
    whiteSpace: 'pre-line',
    wordBreak: 'break-word',
  },
})
