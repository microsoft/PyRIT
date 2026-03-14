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
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
})
