import { useState } from 'react'

import { FluentProvider, webLightTheme, type SpinButtonProps } from '@fluentui/react-components'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import SingleStepSpinButton from './SingleStepSpinButton'

const TestWrapper = ({ children }: { children: React.ReactNode }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function ControlledSpinButton(props: SpinButtonProps) {
  const [value, setValue] = useState(1)
  return (
    <SingleStepSpinButton
      {...props}
      value={value}
      aria-label="Quantity"
      onChange={(event, data) => {
        setValue(data.value ?? Number(data.displayValue))
        props.onChange?.(event, data)
      }}
    />
  )
}

describe('SingleStepSpinButton', () => {
  beforeEach(() => jest.clearAllMocks())
  afterEach(() => jest.useRealTimers())

  it('commits one change per click, not on mousedown or while holding', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const onChange = jest.fn()
    render(<TestWrapper><ControlledSpinButton onChange={onChange} /></TestWrapper>)

    await user.pointer({ keys: '[MouseLeft>]', target: screen.getByRole('button', { name: 'Increment value' }) })
    act(() => jest.advanceTimersByTime(1_000))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('spinbutton')).toHaveValue('1')
    await user.pointer({ keys: '[/MouseLeft]' })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('spinbutton')).toHaveValue('2')

    await user.click(screen.getByRole('button', { name: 'Decrement value' }))
    expect(onChange).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('spinbutton')).toHaveValue('1')
  })

  it('preserves fractional steps, precision, bounds, and keyboard stepping', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <ControlledSpinButton step={0.1} min={0.9} max={1.1} onChange={onChange} />
      </TestWrapper>,
    )
    const input = screen.getByRole('spinbutton')
    const increment = screen.getByRole('button', { name: 'Increment value' })
    const decrement = screen.getByRole('button', { name: 'Decrement value' })
    await user.click(increment)
    expect(input).toHaveValue('1.1')
    expect(increment).toBeDisabled()
    await user.click(increment)
    expect(onChange).toHaveBeenCalledTimes(1)
    await user.click(decrement)
    expect(input).toHaveValue('1')
    await user.click(input)
    await user.keyboard('{ArrowDown}')
    expect(input).toHaveValue('0.9')
    expect(decrement).toBeDisabled()
    await user.keyboard('{ArrowUp}')
    expect(input).toHaveValue('1')
    expect(input).toHaveFocus()
  })

  it('counts rapid separate clicks without debouncing and ignores a cancelled press', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<TestWrapper><ControlledSpinButton onChange={onChange} /></TestWrapper>)
    const increment = screen.getByRole('button', { name: 'Increment value' })
    await user.dblClick(increment)
    expect(screen.getByRole('spinbutton')).toHaveValue('3')
    expect(onChange).toHaveBeenCalledTimes(2)
    await user.pointer({ keys: '[MouseLeft>]', target: increment })
    await user.pointer({ target: screen.getByRole('spinbutton') })
    await user.pointer({ keys: '[/MouseLeft]' })
    expect(onChange).toHaveBeenCalledTimes(2)
  })

  it('steps from newly typed text and preserves Enter, Escape, and blur commits', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ControlledSpinButton /></TestWrapper>)
    const input = screen.getByRole('spinbutton')
    await user.clear(input)
    await user.type(input, '7')
    await user.click(screen.getByRole('button', { name: 'Increment value' }))
    expect(input).toHaveValue('8')
    await user.clear(input)
    await user.type(input, '12{Enter}')
    expect(input).toHaveValue('12')
    await user.clear(input)
    await user.type(input, '99{Escape}')
    expect(input).toHaveValue('12')
    await user.clear(input)
    await user.type(input, '20')
    await user.tab()
    expect(input).toHaveValue('20')
  })

  it.each(['disabled', 'readOnly'] as const)('does not step when %s', async (mode) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<TestWrapper><ControlledSpinButton {...{ [mode]: true }} onChange={onChange} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Increment value' }))
    await user.click(screen.getByRole('button', { name: 'Decrement value' }))
    expect(screen.getByRole('spinbutton')).toHaveValue('1')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('uses a reset controlled value for the next step', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const { rerender } = render(
      <TestWrapper><SingleStepSpinButton value={5} onChange={onChange} /></TestWrapper>,
    )
    rerender(<TestWrapper><SingleStepSpinButton value={0} onChange={onChange} /></TestWrapper>)
    expect(screen.getByRole('spinbutton')).toHaveValue('0')
    await user.click(screen.getByRole('button', { name: 'Increment value' }))
    expect(onChange).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({ value: 1 }))
  })
})
