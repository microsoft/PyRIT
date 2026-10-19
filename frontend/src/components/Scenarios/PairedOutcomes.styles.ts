import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const usePairedOutcomesStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingHorizontalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
    minWidth: 0,
  },
  controls: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'end',
    gap: tokens.spacingHorizontalM,
    '& select, & button': { minHeight: MINIMUM_TOUCH_TARGET_SIZE },
    [NARROW_VIEWPORT_QUERY]: { flexDirection: 'column', alignItems: 'stretch' },
  },
  selector: {
    flex: '1 1 16rem',
    minWidth: 0,
    '& select': { width: '100%', textOverflow: 'ellipsis' },
    [NARROW_VIEWPORT_QUERY]: { flexBasis: 'auto' },
  },
  report: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    minWidth: 0,
  },
  metrics: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))',
    gap: tokens.spacingHorizontalL,
  },
})
