import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useConverterRegistryPageStyles = makeStyles({
  subtitle: {
    color: tokens.colorNeutralForeground3,
  },
  filters: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'flex-end',
    gap: tokens.spacingHorizontalM,
    marginBottom: tokens.spacingVerticalL,
    [NARROW_VIEWPORT_QUERY]: {
      flexDirection: 'column',
      alignItems: 'stretch',
    },
  },
  search: {
    minWidth: '18rem',
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: 0,
    },
  },
  summary: {
    display: 'flex',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    marginBottom: tokens.spacingVerticalS,
  },
  table: {
    tableLayout: 'fixed',
  },
  tableContainer: {
    flexShrink: 0,
  },
  nameColumn: {
    width: '25%',
  },
  modalityColumn: {
    width: '12%',
  },
  actionColumn: {
    width: '5rem',
  },
  actionCell: {
    verticalAlign: 'top',
    textAlign: 'right',
    paddingTop: 0,
    paddingBottom: 0,
    paddingLeft: tokens.spacingHorizontalXXS,
    paddingRight: tokens.spacingHorizontalXXS,
  },
  removeButton: {
    ...mobileTouchTarget,
    color: tokens.colorPaletteRedForeground1,
    ':hover': {
      color: tokens.colorPaletteRedForeground1,
    },
    ':active': {
      color: tokens.colorPaletteRedForeground1,
    },
  },
  dismissIcon: {
    fontSize: tokens.fontSizeBase200,
  },
  type: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    overflowWrap: 'anywhere',
  },
})
