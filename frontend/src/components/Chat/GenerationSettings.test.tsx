import { useState } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { RequestGenerationDraft, TargetInstance } from '@/types'

import GenerationSettings from './GenerationSettings'

const target = makeTarget({
  target_registry_name: 'chat',
  temperature: 0.2,
  capabilities: { supports_temperature: true },
})

const TestWrapper = ({ children }: { children: React.ReactNode }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function SettingsHarness({ selectedTarget = target }: { selectedTarget?: TargetInstance }) {
  const [value, setValue] = useState<RequestGenerationDraft>({})
  return <GenerationSettings value={value} onChange={setValue} target={selectedTarget} disabled={false} />
}

describe('GenerationSettings', () => {
  beforeEach(() => jest.clearAllMocks())

  it('uses defaults until explicitly overridden, shows zero outside the popover, and clears back to defaults', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness /></TestWrapper>)
    const button = screen.getByRole('button', { name: 'Generation settings' })
    await user.click(button)
    const defaults = screen.getByRole('checkbox', { name: 'Use target default for temperature' })
    expect(defaults).toBeChecked()
    const input = screen.getByRole('spinbutton', { name: 'Temperature' })
    expect(input).toBeDisabled()
    expect(input).toHaveValue(null)
    expect(screen.getByText(/Configured default: 0.2/)).toBeVisible()
    await user.click(defaults)
    expect(screen.getByText('Temperature must be a finite number between 0 and 2.')).toBeVisible()
    await user.type(input, '0')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText('Temperature: 0')).toBeVisible()
    await user.click(button)
    expect(screen.getByRole('spinbutton', { name: 'Temperature' })).toHaveValue(0)
    await user.click(screen.getByRole('checkbox', { name: 'Use target default for temperature' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByText('Temperature: 0')).not.toBeInTheDocument()
  })

  it('explains unsupported fields inside a keyboard-accessible popover', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness selectedTarget={{ ...target, capabilities: { supports_temperature: false } }} /></TestWrapper>)
    await user.tab()
    expect(screen.getByRole('button', { name: 'Generation settings' })).toHaveFocus()
    await user.keyboard('{Enter}')
    const dialog = screen.getByRole('dialog', { name: 'Generation settings' })
    expect(within(dialog).getByText('This target does not support runtime temperature.')).toBeVisible()
    expect(within(dialog).getByRole('spinbutton', { name: 'Temperature' })).toBeDisabled()
    expect(within(dialog).getByRole('checkbox')).toBeDisabled()
  })

  it.each([undefined, null])('does not invent a default when it is %s', async (temperature: undefined | null) => {
    const user = userEvent.setup()
    render(<TestWrapper><SettingsHarness selectedTarget={{
      ...target, identifier: { ...target.identifier, temperature },
    }} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Generation settings' }))
    expect(screen.getByText(/Configured default: not specified/)).toBeVisible()
    expect(screen.getByRole('spinbutton', { name: 'Temperature' })).toHaveValue(null)
  })

  it('disables settings while sending without losing the visible override', () => {
    const onChange = jest.fn()
    const rendered = render(<TestWrapper><GenerationSettings value={{ temperature: '0' }}
      onChange={onChange} target={target} disabled /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Generation settings' })).toBeDisabled()
    expect(screen.getByText('Temperature: 0')).toBeVisible()
    rendered.rerender(<TestWrapper><GenerationSettings value={{ temperature: '0' }}
      onChange={onChange} target={target} disabled={false} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Generation settings' })).toBeEnabled()
    expect(onChange).not.toHaveBeenCalled()
  })
})
