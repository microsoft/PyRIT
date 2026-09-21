import type { ComponentProps, MouseEvent } from 'react'

import { SpinButton, type SpinButtonProps } from '@fluentui/react-components'

/**
 * Fluent starts repeating 150 ms after mousedown, even during an ordinary click.
 * Pair its start/stop handlers on click instead, retaining its parsing, rounding,
 * bounds, and keyboard behavior without pointer hold-to-repeat.
 */
function renderStepButton(
  Component: 'button',
  { onMouseDown, onMouseUp, ...props }: ComponentProps<'button'>,
) {
  return (
    <Component
      {...props}
      onClick={(event: MouseEvent<HTMLButtonElement>) => {
        onMouseDown?.(event)
        onMouseUp?.(event)
      }}
    />
  )
}

/** A Fluent numeric input whose up/down buttons apply one step per click. */
export default function SingleStepSpinButton(
  props: Omit<SpinButtonProps, 'incrementButton' | 'decrementButton'>,
) {
  return (
    <SpinButton
      {...props}
      incrementButton={{ children: renderStepButton }}
      decrementButton={{ children: renderStepButton }}
    />
  )
}
