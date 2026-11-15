import { makeStyles, tokens } from '@fluentui/react-components'

export const useFileDropZoneStyles = makeStyles({
  root: {
    position: 'relative',
  },
  feedback: {
    position: 'absolute',
    inset: 0,
    zIndex: 5,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingHorizontalS,
    padding: tokens.spacingVerticalS,
    border: `${tokens.strokeWidthThick} dashed ${tokens.colorBrandStroke1}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: `color-mix(in srgb, ${tokens.colorBrandBackground2} 45%, transparent)`,
    color: tokens.colorBrandForeground1,
    pointerEvents: 'none',
  },
  prompt: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorBrandBackground2,
    boxShadow: tokens.shadow4,
  },
})
