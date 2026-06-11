// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Styles for the per-kind node card components.
 *
 * Uses Fluent UI `makeStyles` + design tokens so the cards auto-adapt
 * between webLightTheme and webDarkTheme. State-badge colors map to
 * Fluent palette tokens (Background2 + Foreground2 pairs), which are
 * the documented "soft surface" pair the rest of the codebase uses
 * for status-style chips.
 *
 * Lives in a companion `.styles.ts` per the existing convention
 * (Sidebar/Navigation.styles.ts, Chat/TargetBadge.styles.ts, etc.).
 */

import { makeStyles, tokens } from '@fluentui/react-components'

import type { NodeState } from '../../runner/treeTypes'

export const useNodeCardStyles = makeStyles({
  frame: {
    backgroundColor: tokens.colorNeutralBackground1,
    color: tokens.colorNeutralForeground1,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    minWidth: '220px',
    maxWidth: '320px',
    fontFamily: tokens.fontFamilyBase,
    fontSize: tokens.fontSizeBase200,
  },
  frameSelected: {
    // Selection visual: a brand-color outline so PR5c's selection state
    // is visible on every card without per-card opt-in. Read from the
    // `selected` prop that react-flow passes to every node component.
    // Griffel rejects the `borderColor` shorthand — use the four
    // longhand properties.
    borderTopColor: tokens.colorBrandStroke1,
    borderRightColor: tokens.colorBrandStroke1,
    borderBottomColor: tokens.colorBrandStroke1,
    borderLeftColor: tokens.colorBrandStroke1,
    boxShadow: `0 0 0 1px ${tokens.colorBrandStroke1}`,
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: tokens.spacingVerticalXS,
  },
  kindLabel: {
    fontWeight: tokens.fontWeightSemibold,
    fontSize: tokens.fontSizeBase100,
    color: tokens.colorNeutralForeground3,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  stateBadge: {
    padding: `1px ${tokens.spacingHorizontalXS}`,
    borderRadius: tokens.borderRadiusSmall,
    fontSize: tokens.fontSizeBase100,
    fontWeight: tokens.fontWeightMedium,
    textTransform: 'lowercase',
  },
  body: {
    display: '-webkit-box',
    WebkitLineClamp: 4,
    WebkitBoxOrient: 'vertical',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'pre-wrap',
    lineHeight: tokens.lineHeightBase300,
  },
  metaRow: {
    display: 'flex',
    gap: tokens.spacingHorizontalXS,
    marginTop: tokens.spacingVerticalXXS,
    fontSize: tokens.fontSizeBase100,
    color: tokens.colorNeutralForeground2,
  },
  metaLabel: {
    color: tokens.colorNeutralForeground3,
  },
  metaValue: {
    fontFamily: tokens.fontFamilyMonospace,
  },
  errorPanel: {
    marginTop: tokens.spacingVerticalXS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalXS}`,
    backgroundColor: tokens.colorPaletteRedBackground2,
    color: tokens.colorPaletteRedForeground2,
    borderRadius: tokens.borderRadiusSmall,
    fontSize: tokens.fontSizeBase100,
  },
  // Score-card V1.0 muted footer (replaces the pre-PR5b.1 operator-facing
  // V1.0/V1.1 text). The italic + reduced opacity signals "supplementary
  // info, not actionable" without naming an internal version label.
  mutedFooter: {
    marginTop: tokens.spacingVerticalXXS,
    fontSize: tokens.fontSizeBase100,
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic',
  },
  // Fan-Children Stack summary body — shown inside the FanCard when the
  // fan is in the collapsed state. The two-row layout (kind ×count on
  // top, status counts below) gives operators the at-a-glance view per
  // the design.
  stackSummary: {
    marginTop: tokens.spacingVerticalXS,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    backgroundColor: tokens.colorNeutralBackground2,
    borderRadius: tokens.borderRadiusSmall,
    border: `1px dashed ${tokens.colorNeutralStroke2}`,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  stackKindLabel: {
    fontWeight: tokens.fontWeightSemibold,
    fontSize: tokens.fontSizeBase200,
    textTransform: 'lowercase',
  },
  stackStatusLine: {
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase100,
    color: tokens.colorNeutralForeground3,
  },
})

// ============================================================================
// State badge color tokens — one token pair per lifecycle state
// ============================================================================

export interface StateBadgeTokens {
  background: string
  foreground: string
}

/**
 * Lifecycle state → Fluent palette tokens. Both light and dark themes get
 * a soft surface + readable foreground. Selecting Background2/Foreground2
 * pairs matches the rest of the codebase's status-chip convention.
 */
export const STATE_BADGE_TOKENS: Record<NodeState, StateBadgeTokens> = {
  draft: {
    background: tokens.colorNeutralBackground2,
    foreground: tokens.colorNeutralForeground3,
  },
  clean: {
    background: tokens.colorPaletteGreenBackground2,
    foreground: tokens.colorPaletteGreenForeground2,
  },
  edited: {
    background: tokens.colorPaletteYellowBackground2,
    foreground: tokens.colorPaletteYellowForeground2,
  },
  // No "Orange" palette in Fluent — Marigold is the closest semantic
  // ("attention but not danger"), distinct from yellow (edited) and red
  // (failed).
  stale: {
    background: tokens.colorPaletteMarigoldBackground2,
    foreground: tokens.colorPaletteMarigoldForeground2,
  },
  running: {
    background: tokens.colorPaletteBlueBackground2,
    foreground: tokens.colorPaletteBlueForeground2,
  },
  failed: {
    background: tokens.colorPaletteRedBackground2,
    foreground: tokens.colorPaletteRedForeground2,
  },
  cancelled: {
    background: tokens.colorNeutralBackground3,
    foreground: tokens.colorNeutralForeground3,
  },
}
