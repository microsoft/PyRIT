import { makeStyles, tokens } from '@fluentui/react-components'

export const useCreateTargetDialogStyles = makeStyles({
  dialogSurface: {
    width: '100%',
    minWidth: 0,
    maxWidth: '37.5rem',
    '@media (max-width: 600px)': {
      maxWidth: `calc(100vw - ${tokens.spacingHorizontalXXL} - ${tokens.spacingHorizontalXXL})`,
    },
  },
  dialogContent: {
    minWidth: 0,
  },
  form: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    minWidth: 0,
    maxWidth: '100%',
    gap: tokens.spacingVerticalL,
  },
  formField: {
    minWidth: 0,
    maxWidth: '100%',
  },
  fullWidthSelect: {
    width: '100%',
    minWidth: 0,
    maxWidth: '100%',
    '& select': {
      width: '100%',
      minWidth: 0,
      maxWidth: '100%',
    },
  },
  warningMessage: {
    width: '100%',
  },
  warningMessageBody: {
    whiteSpace: 'normal',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
  },
  selectedTargetsSection: {
    minWidth: 0,
    maxWidth: '100%',
  },
  selectedTargetsLabel: {
    display: 'block',
    marginBottom: tokens.spacingVerticalXS,
  },
  selectedTargetsList: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    maxWidth: '100%',
    gap: tokens.spacingVerticalXS,
  },
  selectedTargetRow: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto',
    alignItems: 'center',
    minWidth: 0,
    maxWidth: '100%',
    gap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    backgroundColor: tokens.colorNeutralBackground2,
    borderRadius: tokens.borderRadiusSmall,
    '@media (max-width: 600px)': {
      gridTemplateColumns: 'minmax(0, 1fr)',
      alignItems: 'stretch',
    },
  },
  selectedTargetName: {
    display: 'block',
    minWidth: 0,
    maxWidth: '100%',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    cursor: 'help',
    '@media (max-width: 600px)': {
      overflow: 'visible',
      overflowWrap: 'anywhere',
      textOverflow: 'clip',
      whiteSpace: 'normal',
      cursor: 'text',
    },
  },
  targetNameTooltip: {
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
  },
  selectedTargetControlGroup: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
  },
  selectedTargetControls: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'flex-end',
    flexWrap: 'wrap',
    minWidth: 0,
    gap: tokens.spacingHorizontalXS,
    '@media (max-width: 600px)': {
      justifyContent: 'flex-start',
    },
  },
  weightInput: {
    width: '5rem',
    minWidth: '5rem',
  },
  weightError: {
    alignSelf: 'flex-end',
    marginTop: tokens.spacingVerticalXXS,
    color: tokens.colorPaletteRedForeground1,
    textAlign: 'right',
    '@media (max-width: 600px)': {
      alignSelf: 'flex-start',
      textAlign: 'left',
    },
  },
})
