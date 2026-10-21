import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useScorerRegistryStyles = makeStyles({
  defaultPicker: {
    width: 'min(100%, 36rem)',
    flexShrink: 0,
  },
  separator: {
    flexGrow: 0,
    flexShrink: 0,
    marginBlock: tokens.spacingVerticalXL,
  },
  details: {
    paddingBlock: tokens.spacingVerticalM,
  },
  section: {
    flexShrink: 0,
    marginBottom: tokens.spacingVerticalXL,
  },
  heading: {
    display: 'block',
    marginTop: 0,
    marginBottom: tokens.spacingVerticalM,
  },
  comparisonTable: {
    minWidth: '960px',
  },
  json: {
    margin: 0,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalM}`,
    backgroundColor: tokens.colorNeutralBackground2,
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
  },
  name: {
    ...mobileTouchTargetHeight,
    cursor: 'pointer',
    paddingBlock: tokens.spacingVerticalS,
    overflowWrap: 'anywhere',
    justifyContent: 'flex-start',
    textAlign: 'left',
  },
  nameColumn: {
    width: '20%',
  },
  typeColumn: {
    width: '22%',
  },
  scorableColumn: {
    width: '11%',
  },
  binaryMetricColumn: {
    width: '9.5%',
  },
  scaleMetricColumn: {
    width: '19%',
  },
  timeColumn: {
    width: '9%',
  },
  sortButton: {
    ...mobileTouchTargetHeight,
    minWidth: 0,
  },
  numeric: {
    display: 'block',
    width: '100%',
    textAlign: 'right',
    fontVariantNumeric: 'tabular-nums',
    whiteSpace: 'nowrap',
  },
  type: {
    overflowWrap: 'anywhere',
  },
  subtitle: {
    color: tokens.colorNeutralForeground3,
  },
})
