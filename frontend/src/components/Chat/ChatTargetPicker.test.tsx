import type { ComponentProps, ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeTarget } from '@/test-utils/targetFixtures'

import ChatTargetPicker from './ChatTargetPicker'

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const target = makeTarget({ target_registry_name: 'objective', model_name: 'model' })
const otherTarget = makeTarget({ target_registry_name: 'other' })
const defaultProps = {
  target,
  targets: [target, otherTarget],
  loading: false,
  error: null,
  disabled: false,
  onSelect: jest.fn(),
}

describe('ChatTargetPicker', () => {
  beforeEach(() => jest.clearAllMocks())

  it('keeps the badge and provides a keyboard-focusable target selector', async () => {
    const user = userEvent.setup()
    render(<ChatTargetPicker {...defaultProps} />, { wrapper: TestWrapper })
    const selector = screen.getByRole('combobox', { name: 'Chat target' })

    expect(screen.getByTestId('target-badge')).toHaveTextContent('model')
    expect(selector).toHaveValue('objective')
    await user.tab()
    expect(selector).toHaveFocus()
    await user.selectOptions(selector, 'other')
    expect(defaultProps.onSelect).toHaveBeenCalledWith(otherTarget)
    expect(screen.queryByRole('button', { name: 'Refresh targets' })).not.toBeInTheDocument()
  })

  it('offers targets in the same place when no target is selected', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<ChatTargetPicker {...defaultProps} target={null} />, { wrapper: TestWrapper })
    const selector = screen.getByRole('combobox', { name: 'Chat target' })

    expect(selector).toHaveValue('')
    await user.tab()
    await user.selectOptions(selector, 'objective')
    expect(defaultProps.onSelect).toHaveBeenCalledWith(target)
    rerender(<ChatTargetPicker {...defaultProps} />)
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveFocus()
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toHaveValue('objective')
  })

  it.each([
    { loading: true },
    { disabled: true },
    { error: 'Registry unavailable' },
    { targets: [] },
  ])('blocks selection when the registry or composer is unavailable: %o', (
    overrides: Partial<ComponentProps<typeof ChatTargetPicker>>,
  ) => {
    render(<ChatTargetPicker {...defaultProps} {...overrides} />, { wrapper: TestWrapper })
    expect(screen.getByRole('combobox', { name: 'Chat target' })).toBeDisabled()
  })
})
