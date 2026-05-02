import { makeStyles, tokens } from '@fluentui/react-components'

export const useAttackHistoryStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  header: {
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalXXL}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: tokens.spacingVerticalS,
  },
  filters: {
    display: 'flex',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
    flexWrap: 'wrap',
  },
  filterDropdown: {
    minWidth: '160px',
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    overflowX: 'auto',
  },
  table: {
    minWidth: '100%',
    tableLayout: 'auto' as const,
  },
  colStatus: { minWidth: '100px', whiteSpace: 'nowrap' as const },
  colAttackType: { minWidth: '110px', whiteSpace: 'nowrap' as const },
  colTarget: { minWidth: '120px', whiteSpace: 'nowrap' as const },
  colOperator: { minWidth: '120px', whiteSpace: 'nowrap' as const },
  colOperation: { minWidth: '130px', whiteSpace: 'nowrap' as const },
  colMessages: { minWidth: '60px', whiteSpace: 'nowrap' as const },
  colConversations: { minWidth: '70px', whiteSpace: 'nowrap' as const },
  colConverters: { minWidth: '120px', whiteSpace: 'nowrap' as const },
  colLabels: { minWidth: '180px', whiteSpace: 'nowrap' as const },
  colDate: { minWidth: '100px', whiteSpace: 'nowrap' as const },
  colAction: { minWidth: '48px', whiteSpace: 'nowrap' as const },
  clickableRow: {
    cursor: 'pointer',
    ':hover': {
      backgroundColor: tokens.colorNeutralBackground1Hover,
    },
  },
  previewCell: {
    display: 'block',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    maxWidth: '400px',
  },
  nowrap: {
    whiteSpace: 'nowrap',
  },
  badgeGroup: {
    display: 'flex',
    gap: tokens.spacingHorizontalXXS,
    flexWrap: 'wrap',
  },
  matchModeToggle: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    paddingInline: tokens.spacingHorizontalXS,
  },
  matchModeLabel: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    userSelect: 'none',
  },
  matchModeLabelActive: {
    color: tokens.colorNeutralForeground1,
    fontWeight: tokens.fontWeightSemibold,
  },
  pagination: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalXXL}`,
    borderTop: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXXL,
    color: tokens.colorNeutralForeground3,
  },
})
