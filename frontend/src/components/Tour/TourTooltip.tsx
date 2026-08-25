import { Button, FluentProvider, Text, webDarkTheme, webLightTheme } from '@fluentui/react-components'
import { DismissRegular } from '@fluentui/react-icons'

import type { TooltipRenderProps } from 'react-joyride'

import { useTourTooltipStyles } from './TourTooltip.styles'

interface TourTooltipProps extends TooltipRenderProps {
  isDarkMode?: boolean
}

/**
 * Custom Joyride tooltip styled with Fluent UI components.
 *
 * Joyride renders tooltips in a React portal (outside the main FluentProvider).
 * We wrap content in its own FluentProvider so Fluent components and makeStyles
 * tokens resolve correctly regardless of where the portal appends.
 */
export default function TourTooltip({
  continuous,
  index,
  isLastStep,
  size,
  step,
  backProps,
  primaryProps,
  skipProps,
  closeProps,
  tooltipProps,
  isDarkMode = true,
}: TourTooltipProps) {
  const styles = useTourTooltipStyles()

  return (
    <div {...tooltipProps}>
      <FluentProvider theme={isDarkMode ? webDarkTheme : webLightTheme}>
        <div className={styles.wrapper} data-testid="tour-tooltip-card">
          <div className={styles.container}>
            {/* Close (X) button — top-right, hidden on last step */}
            <div className={styles.closeRow}>
              {!isLastStep && (
                <Button
                  {...closeProps}
                  appearance="subtle"
                  icon={<DismissRegular />}
                  size="small"
                  className={styles.closeButton}
                />
              )}
            </div>

            {/* Step content */}
            <Text className={styles.content} size={300}>
              {step.content}
            </Text>

            {/* Footer: step counter + buttons, offset right to leave room for mascot */}
            <div className={styles.footer}>
              <Text className={styles.stepCounter} size={200}>
                {index + 1} of {size}
              </Text>

              <div className={styles.actions}>
                {!isLastStep && (
                  <Button {...skipProps} appearance="subtle" size="small" className={styles.actionButton}>
                    Skip tour
                  </Button>
                )}

                {index > 0 && (
                  <Button {...backProps} appearance="outline" size="small" className={styles.actionButton}>
                    Back
                  </Button>
                )}

                {continuous && (
                  <Button {...primaryProps} appearance="primary" size="small" className={styles.actionButton}>
                    {isLastStep ? "Anchors Away!" : 'Next'}
                  </Button>
                )}
              </div>
            </div>
          </div>

          {/* Roakey presenting the content — overlaps bottom-left of the card */}
          <img src="/roakey_guide.png" alt="" className={styles.mascot} />
        </div>
      </FluentProvider>
    </div>
  )
}
