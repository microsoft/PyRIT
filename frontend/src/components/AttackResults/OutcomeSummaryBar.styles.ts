import { makeStyles, tokens } from '@fluentui/react-components'

export const useOutcomeSummaryBarStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  bar: {
    display: 'flex',
    width: '100%',
    height: '1rem',
    borderRadius: tokens.borderRadiusMedium,
    overflow: 'hidden',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  segment: {
    height: '100%',
    // Keep a non-zero slice visible even when its share rounds down to a sliver.
    minWidth: '2px',
  },
  legend: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalL}`,
    margin: 0,
    padding: 0,
    listStyle: 'none',
  },
  legendItem: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
  },
  swatch: {
    width: '0.75rem',
    height: '0.75rem',
    borderRadius: tokens.borderRadiusSmall,
    flexShrink: 0,
  },
  legendCount: {
    fontVariantNumeric: 'tabular-nums',
  },
  legendPercent: {
    color: tokens.colorNeutralForeground3,
    fontVariantNumeric: 'tabular-nums',
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  // Per-outcome fills, aligned with OutcomeBadge semantics. Applied to both the
  // bar segment and the legend swatch for the same outcome.
  colorSuccess: {
    backgroundColor: tokens.colorPaletteGreenForeground1,
  },
  colorFailure: {
    backgroundColor: tokens.colorPaletteRedForeground1,
  },
  colorUndetermined: {
    backgroundColor: tokens.colorNeutralForeground3,
  },
  colorError: {
    backgroundColor: tokens.colorPaletteDarkOrangeForeground1,
  },
})
