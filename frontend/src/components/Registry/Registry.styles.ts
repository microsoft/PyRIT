import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
} from '@/styles/touchTargets'

export const useRegistryLayoutStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minWidth: 0,
  },
  tabs: {
    flexShrink: 0,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalXXL} 0`,
    backgroundColor: tokens.colorNeutralBackground2,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    [NARROW_VIEWPORT_QUERY]: {
      paddingLeft: tokens.spacingHorizontalM,
      paddingRight: tokens.spacingHorizontalM,
    },
  },
  content: {
    flex: 1,
    minHeight: 0,
  },
})

export const useConverterRegistryStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    minWidth: 0,
    padding: tokens.spacingVerticalXXL,
    overflow: 'auto',
    backgroundColor: tokens.colorNeutralBackground2,
    [NARROW_VIEWPORT_QUERY]: {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingVerticalM,
    marginBottom: tokens.spacingVerticalXL,
  },
  headerText: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  action: {
    [NARROW_VIEWPORT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  state: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXXL,
    textAlign: 'center',
  },
  error: {
    color: tokens.colorPaletteRedForeground1,
  },
  tableContainer: {
    width: '100%',
    overflowX: 'auto',
  },
  table: {
    minWidth: '780px',
    backgroundColor: tokens.colorNeutralBackground1,
  },
  nameCell: {
    fontFamily: tokens.fontFamilyMonospace,
    overflowWrap: 'anywhere',
  },
  parameters: {
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
  },
  typeList: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
  },
  deleteButton: {
    color: tokens.colorPaletteRedForeground1,
  },
})

export const useCreateConverterDialogStyles = makeStyles({
  surface: {
    width: 'min(560px, calc(100vw - 32px))',
    maxWidth: '560px',
    maxHeight: 'calc(100vh - 32px)',
  },
  content: {
    maxHeight: '70vh',
    overflowY: 'auto',
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  typeDropdown: {
    width: '100%',
  },
  typeListbox: {
    maxHeight: 'calc(100vh - 10rem)',
    overflowY: 'auto',
  },
  typeOption: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    minWidth: 0,
    gap: tokens.spacingVerticalXXS,
    whiteSpace: 'normal',
  },
  typeOptionHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    width: '100%',
    minWidth: 0,
    gap: tokens.spacingHorizontalS,
  },
  typeDescription: {
    color: tokens.colorNeutralForeground3,
  },
  selectedTypeSummary: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    padding: tokens.spacingVerticalS,
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  selectedTypeHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
  },
  typeMetadata: {
    color: tokens.colorNeutralForeground3,
  },
  llmBadge: {
    display: 'inline-block',
    padding: `0 ${tokens.spacingHorizontalXXS}`,
    borderRadius: tokens.borderRadiusSmall,
    backgroundColor: tokens.colorPalettePurpleBackground2,
    color: tokens.colorPalettePurpleForeground2,
    fontSize: tokens.fontSizeBase100,
    fontWeight: tokens.fontWeightSemibold as unknown as string,
  },
  parameterGrid: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  parameterRow: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  fileRow: {
    display: 'flex',
    gap: tokens.spacingHorizontalS,
  },
  fileInput: {
    flex: 1,
  },
  errorText: {
    color: tokens.colorPaletteRedForeground1,
  },
})
