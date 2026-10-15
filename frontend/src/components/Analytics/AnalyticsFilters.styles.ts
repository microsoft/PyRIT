import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAnalyticsFiltersStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  row: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  secondaryControls: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  collapsed: { [NARROW_VIEWPORT_QUERY]: { display: 'none' } },
  disclosure: {
    display: 'none',
    [NARROW_VIEWPORT_QUERY]: { display: 'inline-flex' },
    ...mobileTouchTargetHeight,
  },
  button: { ...mobileTouchTargetHeight },
  iconButton: { ...mobileTouchTarget },
  chip: {
    display: 'inline-flex',
    alignItems: 'center',
    maxWidth: '100%',
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  chipLabel: {
    overflowWrap: 'anywhere',
    textAlign: 'start',
    minWidth: 0,
    ...mobileTouchTargetHeight,
  },
  chipText: {
    minWidth: 0,
    maxWidth: '32rem',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
  },
  editor: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    padding: tokens.spacingHorizontalL,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    minWidth: 0,
    [NARROW_VIEWPORT_QUERY]: { padding: tokens.spacingHorizontalS },
  },
  editorHeading: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
  },
  facet: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    maxWidth: '42rem',
  },
  options: {
    display: 'flex',
    flexDirection: 'column',
    overflowY: 'auto',
    maxHeight: '16rem',
    overflowWrap: 'anywhere',
  },
  checkbox: { ...mobileTouchTargetHeight },
  field: {
    minWidth: 0,
    flex: 1,
    maxWidth: '22rem',
  },
  input: { ...mobileTouchTargetHeight },
  popover: {
    display: 'flex',
    flexDirection: 'column',
    maxWidth: 'min(24rem, 80vw)',
    gap: tokens.spacingVerticalS,
  },
  note: { color: tokens.colorNeutralForeground2 },
})
