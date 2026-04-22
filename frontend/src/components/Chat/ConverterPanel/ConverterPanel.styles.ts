import { makeStyles, tokens } from '@fluentui/react-components'

export const useConverterPanelStyles = makeStyles({
  resizeContainer: {
    display: 'flex',
    flexDirection: 'row',
    height: '100%',
    flexShrink: 0,
  },
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    flex: 1,
    minWidth: 0,
    backgroundColor: tokens.colorNeutralBackground3,
    overflow: 'hidden',
  },
  resizeHandle: {
    width: '4px',
    cursor: 'col-resize',
    backgroundColor: 'transparent',
    borderRight: `1px solid ${tokens.colorNeutralStroke1}`,
    flexShrink: 0,
    ':hover': {
      backgroundColor: tokens.colorBrandBackground2,
    },
    ':active': {
      backgroundColor: tokens.colorBrandBackground,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    minHeight: '48px',
    gap: tokens.spacingHorizontalS,
  },
  tabBar: {
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    flexShrink: 0,
  },
  headerTitle: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalL,
    overflowY: 'auto',
    flex: 1,
  },
  hintText: {
    color: tokens.colorNeutralForeground3,
  },
  loading: {
    display: 'flex',
    justifyContent: 'center',
    paddingTop: tokens.spacingVerticalL,
  },
  outputFilterRow: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXXS,
    flexWrap: 'wrap',
  },
  filterChip: {
    minWidth: 'auto',
    padding: `0 ${tokens.spacingHorizontalS}`,
    height: '24px',
    fontSize: tokens.fontSizeBase200,
  },
  converterList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  groupHeader: {
    fontWeight: tokens.fontWeightSemibold as unknown as string,
    fontSize: tokens.fontSizeBase200,
  },
  header_text: {
    color: tokens.colorPaletteBlueForeground2,
  },
  header_image_path: {
    color: tokens.colorPaletteGreenForeground2,
  },
  header_audio_path: {
    color: tokens.colorPaletteYellowForeground2,
  },
  header_video_path: {
    color: tokens.colorPalettePurpleForeground2,
  },
  header_binary_path: {
    color: tokens.colorPaletteRedForeground2,
  },
  converterCard: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    padding: tokens.spacingVerticalS,
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  converterName: {
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  metaRow: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
    rowGap: tokens.spacingVerticalXXS,
  },
  badgeText: {
    color: tokens.colorNeutralForeground2,
  },
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  llmBadge: {
    display: 'inline-block',
    marginLeft: tokens.spacingHorizontalXS,
    padding: `0 ${tokens.spacingHorizontalXXS}`,
    borderRadius: tokens.borderRadiusSmall,
    backgroundColor: tokens.colorPalettePurpleBackground2,
    color: tokens.colorPalettePurpleForeground2,
    fontSize: tokens.fontSizeBase100,
    fontWeight: tokens.fontWeightSemibold as unknown as string,
    verticalAlign: 'middle',
  },
  optionContent: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    width: '100%',
    gap: tokens.spacingHorizontalXS,
  },
  optionBadges: {
    display: 'flex',
    alignItems: 'center',
    gap: '2px',
    flexShrink: 0,
  },
  typeBadge: {
    display: 'inline-block',
    padding: `0 3px`,
    borderRadius: tokens.borderRadiusSmall,
    fontSize: '10px',
    fontWeight: tokens.fontWeightSemibold as unknown as string,
    lineHeight: '16px',
    verticalAlign: 'middle',
  },
  typeArrow: {
    fontSize: '10px',
    color: tokens.colorNeutralForeground3,
  },
  // Input type colors (solid backgrounds)
  input_text: {
    backgroundColor: tokens.colorPaletteBlueBackground2,
    color: tokens.colorPaletteBlueForeground2,
  },
  input_image_path: {
    backgroundColor: tokens.colorPaletteGreenBackground2,
    color: tokens.colorPaletteGreenForeground2,
  },
  input_audio_path: {
    backgroundColor: tokens.colorPaletteYellowBackground2,
    color: tokens.colorPaletteYellowForeground2,
  },
  input_video_path: {
    backgroundColor: tokens.colorPalettePurpleBackground2,
    color: tokens.colorPalettePurpleForeground2,
  },
  // Output type colors (outlined/lighter)
  output_text: {
    backgroundColor: 'transparent',
    color: tokens.colorPaletteBlueForeground2,
    border: `1px solid ${tokens.colorPaletteBlueBorderActive}`,
  },
  output_image_path: {
    backgroundColor: 'transparent',
    color: tokens.colorPaletteGreenForeground2,
    border: `1px solid ${tokens.colorPaletteGreenBorderActive}`,
  },
  output_audio_path: {
    backgroundColor: 'transparent',
    color: tokens.colorPaletteYellowForeground2,
    border: `1px solid ${tokens.colorPaletteYellowBorderActive}`,
  },
  output_video_path: {
    backgroundColor: 'transparent',
    color: tokens.colorPalettePurpleForeground2,
    border: `1px solid ${tokens.colorPalettePurpleBorderActive}`,
  },
  outputSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    marginTop: tokens.spacingVerticalS,
  },
  paramsSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    padding: tokens.spacingVerticalS,
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  paramsSectionHeader: {
    justifyContent: 'flex-start',
    fontWeight: tokens.fontWeightSemibold as unknown as string,
    padding: 0,
  },
  paramBlock: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  paramLabel: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXXS,
  },
  paramInfo: {
    display: 'inline-flex',
    alignItems: 'center',
    color: tokens.colorNeutralForeground3,
    cursor: 'help',
  },
  paramInputError: {
    borderTopColor: tokens.colorPaletteRedBorder1,
    borderRightColor: tokens.colorPaletteRedBorder1,
    borderBottomColor: tokens.colorPaletteRedBorder1,
    borderLeftColor: tokens.colorPaletteRedBorder1,
    ':focus': {
      borderTopColor: tokens.colorPaletteRedBorder1,
      borderRightColor: tokens.colorPaletteRedBorder1,
      borderBottomColor: tokens.colorPaletteRedBorder1,
      borderLeftColor: tokens.colorPaletteRedBorder1,
    },
  },
  paramErrorText: {
    color: tokens.colorPaletteRedForeground1,
    fontWeight: tokens.fontWeightSemibold as unknown as string,
  },
  filePickerRow: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
  },
  outputBox: {
    padding: tokens.spacingVerticalS,
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
    minHeight: '80px',
    whiteSpace: 'pre-wrap' as const,
    wordBreak: 'break-word' as const,
    overflowY: 'auto' as const,
    maxHeight: '200px',
  },
  previewPre: {
    margin: 0,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground1,
  },
  errorBody: {
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    overflow: 'visible',
  },
  previewImage: {
    maxWidth: '100%',
    maxHeight: '200px',
    borderRadius: tokens.borderRadiusMedium,
    objectFit: 'contain' as const,
  },
  previewAudio: {
    width: '100%',
  },
  previewVideo: {
    maxWidth: '100%',
    maxHeight: '200px',
    borderRadius: tokens.borderRadiusMedium,
  },
})
