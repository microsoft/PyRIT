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
  capabilityCell: {
    width: '75px',
    textAlign: 'center',
  },
  modalityCell: {
    width: '110px',
    textAlign: 'center',
  },
  inputsModalityCell: {
    width: '160px',
    textAlign: 'center',
  },
  modalityRow: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingHorizontalXS,
    flexWrap: 'wrap',
  },
  modalityIcon: {
    fontSize: tokens.fontSizeBase500,
    color: tokens.colorNeutralForeground2,
  },
  compositeIcon: {
    position: 'relative',
    display: 'inline-flex',
    lineHeight: 0,
  },
  compositeBadge: {
    position: 'absolute',
    top: '-4px',
    right: '-6px',
    fontSize: tokens.fontSizeBase300,
    color: tokens.colorNeutralForeground2,
  },
  capabilityIconSupported: {
    color: tokens.colorPaletteGreenForeground1,
    fontSize: tokens.fontSizeBase500,
  },
  capabilityIconUnsupported: {
    color: tokens.colorPaletteRedForeground1,
    fontSize: tokens.fontSizeBase500,
  },
  helpHeader: {
    cursor: 'help',
  },
  /** Sub-row for inner targets of a RoundRobinTarget — visually indented with a
   *  lighter background so it's clear these are children, not standalone targets. */
  innerTargetRow: {
    backgroundColor: tokens.colorNeutralBackground2,
    opacity: 0.85,
  },
})
