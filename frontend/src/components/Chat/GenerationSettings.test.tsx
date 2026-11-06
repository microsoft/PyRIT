import { useState } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { RequestGenerationDraft, TargetInstance } from '@/types'
import { parseGenerationConfig } from '@/utils/requestGeneration'

import GenerationSettings from './GenerationSettings'

const target = makeTarget({
  target_registry_name: 'chat',
  temperature: 0.2,
  temperature_range: [0, 2],
  capabilities: { supports_temperature: true },
})

const TestWrapper = ({ children }: { children: React.ReactNode }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function SettingsHarness({ selectedTarget = target, initialValue = {}, disabled = false, onChange = jest.fn() }: {
  selectedTarget?: TargetInstance
  initialValue?: RequestGenerationDraft
  disabled?: boolean
  onChange?: (value: RequestGenerationDraft) => void
}) {
  const [value, setValue] = useState(initialValue)
  return <>
    <GenerationSettings value={value} onChange={(next: RequestGenerationDraft) => { setValue(next); onChange(next) }}
      target={selectedTarget} disabled={disabled} />
    <button onClick={() => setValue({})}>Reset draft</button>
  </>
}

describe('GenerationSettings', () => {
  beforeEach(() => jest.clearAllMocks())

  it('should show a compact editable default, preserve zero, and omit a cleared override', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<TestWrapper><SettingsHarness onChange={onChange} /></TestWrapper>)
    const button = screen.getByRole('button', { name: 'Adjust temperature: target default' })
    expect(button).toHaveTextContent('')
    expect(within(button).getByTestId('temperature-chevron')).toBeInTheDocument()
    await user.click(button)
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    expect(input).toBeEnabled()
    expect(input).toHaveFocus()
    expect(input).toHaveValue('')
    expect(input).toHaveAttribute('placeholder', 'Default')
    await user.type(input, '0')
    expect(onChange).toHaveBeenLastCalledWith({ temperature: '0' })
    await user.keyboard('{Enter}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(button).toHaveTextContent('0')
    expect(button).toHaveAccessibleName('Adjust temperature: 0')
    await user.click(button)
    await user.clear(screen.getByRole('textbox', { name: 'Temperature' }))
    await user.click(screen.getByRole('button', { name: 'Apply temperature' }))
    expect(button).toHaveAccessibleName('Adjust temperature: target default')
    expect(onChange).toHaveBeenLastCalledWith({})
    expect(parseGenerationConfig(onChange.mock.lastCall[0], target)).toEqual({})
  })

  it('should show an override even when it equals the configured default', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    await user.type(screen.getByRole('textbox', { name: 'Temperature' }), '0.2')
    await user.click(screen.getByRole('button', { name: 'Apply temperature' }))
    expect(screen.getByRole('button', { name: 'Adjust temperature: 0.2' })).toHaveTextContent('0.2')
  })

  it.each(['Escape', 'Cancel'])('should restore the previous selection with %s', async (action: string) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<TestWrapper><SettingsHarness initialValue={{ temperature: '0' }} onChange={onChange} /></TestWrapper>)
    const button = screen.getByRole('button', { name: 'Adjust temperature: 0' })
    await user.click(button)
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    await user.clear(input)
    await user.type(input, '1.2')
    if (action === 'Escape') await user.keyboard('{Escape}')
    else await user.click(screen.getByRole('button', { name: 'Cancel temperature edit' }))
    expect(onChange).toHaveBeenLastCalledWith({ temperature: '0' })
    expect(button).toHaveAccessibleName('Adjust temperature: 0')
    expect(button).toHaveFocus()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('should clear to the default with the explicit action', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<TestWrapper><SettingsHarness initialValue={{ temperature: '0.8' }} onChange={onChange} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    await user.click(screen.getByRole('button', { name: 'Use target default' }))
    expect(onChange).toHaveBeenLastCalledWith({})
    expect(screen.getByRole('button', { name: 'Adjust temperature: target default' })).toHaveFocus()
  })

  it.each(['2.1', 'abc', '1e', '0x1'])('should retain invalid input %s for send validation', async (value: string) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<TestWrapper><SettingsHarness onChange={onChange} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    await user.type(input, value)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Temperature must be a finite number between 0 and 2.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeDisabled()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('dialog', { name: 'Temperature' })).toBeVisible()
    expect(onChange).toHaveBeenLastCalledWith({ temperature: value })
    expect(() => parseGenerationConfig(onChange.mock.lastCall[0], target)).toThrow(/finite number/)
    await user.keyboard('{Escape}')
    expect(onChange).toHaveBeenLastCalledWith({})
  })

  it('should describe and enforce the target-specific temperature range', async () => {
    const user = userEvent.setup()
    const mistral = makeTarget({
      model_name: 'Mistral-Large-3',
      capabilities: { supports_temperature: true },
      temperature_range: [0, 1],
    })
    render(<TestWrapper><SettingsHarness selectedTarget={mistral} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    expect(input).toHaveAccessibleDescription(expect.stringContaining('Enter 0 through 1.'))
    await user.type(input, '2')
    expect(screen.getByText('Temperature must be a finite number between 0 and 1.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeDisabled()
    await user.clear(input)
    await user.type(input, '1')
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Apply temperature' }))
    expect(screen.getByRole('button', { name: 'Adjust temperature: 1' })).toBeInTheDocument()
  })

  it('should explain unsupported temperature on focus without opening an unusable form', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness selectedTarget={{ ...target, capabilities: { supports_temperature: false } }} /></TestWrapper>)
    await user.tab()
    const button = screen.getByRole('button', { name: 'Temperature unavailable' })
    expect(button).toHaveFocus()
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(screen.queryByTestId('temperature-chevron')).not.toBeInTheDocument()
    expect(await screen.findByRole('tooltip')).toHaveTextContent('This target does not support runtime temperature.')
    await user.keyboard('{Enter}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Temperature' })).not.toBeInTheDocument()
  })

  it('should not promise zero is valid when the target range excludes it', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const positiveMinimumTarget = makeTarget({
      capabilities: { supports_temperature: true },
      temperature_range: [0.2, 1],
    })
    render(<TestWrapper><SettingsHarness selectedTarget={positiveMinimumTarget} onChange={onChange} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    expect(input).toHaveAccessibleDescription(expect.stringContaining('Enter 0.2 through 1.'))
    expect(input).not.toHaveAccessibleDescription(expect.stringContaining('Zero is valid'))
    await user.type(input, '0')
    expect(screen.getByText('Temperature must be a finite number between 0.2 and 1.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeDisabled()
    await user.clear(input)
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeEnabled()
    expect(parseGenerationConfig(onChange.mock.lastCall[0], positiveMinimumTarget)).toEqual({})
  })

  it.each([undefined, null])('should not invent bounds or disable supported temperature without a range (%s)', async (temperature_range: undefined | null) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const unknownRangeTarget = { ...target, temperature_range }
    render(<TestWrapper><SettingsHarness selectedTarget={unknownRangeTarget} onChange={onChange} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    expect(input).toHaveAccessibleDescription(expect.stringContaining('No temperature range is declared'))
    expect(input).not.toHaveAccessibleDescription(expect.stringContaining('Enter 0 through 2'))
    await user.type(input, '3')
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeEnabled()
    expect(parseGenerationConfig(onChange.mock.lastCall[0], unknownRangeTarget)).toEqual({ temperature: 3 })
    await user.clear(input)
    await user.type(input, 'Infinity')
    expect(screen.getByText('Temperature must be a finite number.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Apply temperature' })).toBeDisabled()
  })

  it.each([undefined, null])('should not invent an unspecified default (%s)', async (temperature: undefined | null) => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness selectedTarget={{
      ...target, identifier: { ...target.identifier, temperature },
    }} /></TestWrapper>)
    await user.hover(screen.getByRole('button', { name: /^Adjust temperature/ }))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('not specified; provider default unknown')
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toHaveValue('')
  })

  it('should close editing and allow clearing a retained override when support is lost', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const rendered = render(<TestWrapper><SettingsHarness onChange={onChange} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    await user.type(screen.getByRole('textbox', { name: 'Temperature' }), '0')
    rendered.rerender(<TestWrapper><SettingsHarness onChange={onChange} selectedTarget={{
      ...target, capabilities: { supports_temperature: false },
    }} /></TestWrapper>)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Temperature unavailable' })).toHaveTextContent('')
    await user.click(screen.getByRole('button', { name: 'Clear temperature override' }))
    expect(onChange).toHaveBeenLastCalledWith({})
    expect(screen.queryByRole('button', { name: 'Clear temperature override' })).not.toBeInTheDocument()
  })

  it('should disable editing while sending without losing the override or reopening stale edits', async () => {
    const user = userEvent.setup()
    const rendered = render(<TestWrapper><SettingsHarness initialValue={{ temperature: '0' }} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    rendered.rerender(<TestWrapper><SettingsHarness initialValue={{ temperature: '0' }} disabled /></TestWrapper>)
    const button = screen.getByRole('button', { name: 'Adjust temperature: 0' })
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('0')
    expect(screen.queryByTestId('temperature-chevron')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    rendered.rerender(<TestWrapper><SettingsHarness initialValue={{ temperature: '0' }} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Adjust temperature: 0' })).toBeEnabled()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it.each(['0.7', ''])('should discard an old cancel value on external reset, even when the edited value is %s', async (value: string) => {
    const user = userEvent.setup()
    let rendered: ReturnType<typeof render>
    const onChange = jest.fn((next: RequestGenerationDraft): void => { rendered.rerender(control(next)) })
    const control = (draft: RequestGenerationDraft) => <TestWrapper>
      <GenerationSettings value={draft} target={target} disabled={false} onChange={onChange} />
    </TestWrapper>
    rendered = render(control({ temperature: '0.8' }))
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    const input = screen.getByRole('textbox', { name: 'Temperature' })
    await user.clear(input)
    if (value) await user.type(input, value)
    rendered.rerender(control({}))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Adjust temperature: target default' }))
    await user.type(screen.getByRole('textbox', { name: 'Temperature' }), '0.7')
    await user.keyboard('{Escape}')
    expect(screen.getByRole('button', { name: 'Adjust temperature: target default' })).toHaveTextContent('')
    expect(onChange).toHaveBeenLastCalledWith({})
  })

  it('should keep optional help available without dismissing the temperature editor', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: /^Adjust temperature/ }))
    expect(screen.getByRole('textbox', { name: 'Temperature' })).toHaveAccessibleDescription(
      expect.stringContaining('Configured default: 0.2'),
    )
    await user.click(screen.getByRole('button', { name: 'Temperature information' }))
    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog', { name: 'Temperature' })).toBeVisible()
  })
})
