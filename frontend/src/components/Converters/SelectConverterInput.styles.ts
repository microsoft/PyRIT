import { makeStyles, tokens } from '@fluentui/react-components'

export const useSelectConverterInputStyles = makeStyles({
  converterPicker: { width: '100%' },
  converterPickerListbox: { maxHeight: 'min(40rem, 75vh)', overflowY: 'auto' },
  createOption: {
    display: 'flex', alignItems: 'flex-start', width: '100%',
    gap: tokens.spacingHorizontalS, whiteSpace: 'normal',
  },
  registeredOption: {
    display: 'flex', flexDirection: 'column', width: '100%', minWidth: 0,
    gap: tokens.spacingVerticalXXS, whiteSpace: 'normal',
  },
  optionText: { display: 'flex', flexDirection: 'column', minWidth: 0, gap: tokens.spacingVerticalXXS },
  optionHeader: {
    display: 'flex', alignItems: 'baseline', justifyContent: 'flex-start', flexWrap: 'wrap',
    minWidth: 0, gap: `${tokens.spacingVerticalXXS} ${tokens.spacingHorizontalS}`,
  },
  hintText: { color: tokens.colorNeutralForeground3 },
  llmBadge: {
    display: 'inline-block', marginLeft: tokens.spacingHorizontalXS,
    padding: `0 ${tokens.spacingHorizontalXXS}`, borderRadius: tokens.borderRadiusSmall,
    backgroundColor: tokens.colorPalettePurpleBackground2, color: tokens.colorPalettePurpleForeground2,
    fontSize: tokens.fontSizeBase100, fontWeight: tokens.fontWeightSemibold,
    verticalAlign: 'middle', flexShrink: 0,
  },
})
