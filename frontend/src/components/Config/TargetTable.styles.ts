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
  stickyHeader: {
    position: 'sticky',
    top: 0,
    backgroundColor: tokens.colorNeutralBackground1,
    zIndex: 1,
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
