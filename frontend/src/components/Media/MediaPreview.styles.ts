import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useMediaPreviewStyles = makeStyles({
  imageContainer: {
    position: 'relative',
    display: 'inline-block',
    width: 'fit-content',
    maxWidth: 'min(400px, 100%)',
    minWidth: 'min(100px, 100%)',
    minHeight: '60px',
  },
  spinner: {
    position: 'absolute',
    top: '50%',
    left: '50%',
    transform: 'translate(-50%, -50%)',
  },
  image: {
    display: 'block',
    width: 'auto',
    height: 'auto',
    maxWidth: '100%',
    maxHeight: '400px',
    borderRadius: tokens.borderRadiusMedium,
    objectFit: 'contain',
    border: `1px solid ${tokens.colorNeutralStroke1}`,
  },
  imageHidden: {
    maxWidth: '100%',
    maxHeight: '400px',
    borderRadius: tokens.borderRadiusMedium,
    objectFit: 'contain',
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    visibility: 'hidden',
    width: 0,
    height: 0,
  },
  video: {
    display: 'block',
    width: 'auto',
    height: 'auto',
    maxWidth: 'min(400px, 100%)',
    maxHeight: '300px',
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
  },
  audio: {
    width: '300px',
    maxWidth: '100%',
  },
  recovery: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
    maxWidth: '100%',
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    minWidth: 0,
  },
  button: {
    ...mobileTouchTargetHeight,
    maxWidth: '100%',
  },
  download: {
    ...mobileTouchTargetHeight,
    display: 'inline-flex',
    alignItems: 'center',
    maxWidth: '100%',
    color: tokens.colorBrandForeground1,
  },
})
