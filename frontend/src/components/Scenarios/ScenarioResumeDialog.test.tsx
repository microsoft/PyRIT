import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import ScenarioResumeDialog from './ScenarioResumeDialog'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('ScenarioResumeDialog', () => {
  const defaultProps = {
    scenarioResultId: 'legacy-run',
    pending: false,
    error: null,
    onConfirm: jest.fn(),
    onCancel: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('requires explicit confirmation even when the safe initial values are unchanged', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ScenarioResumeDialog {...defaultProps} /></TestWrapper>)

    const concurrency = screen.getByRole('spinbutton', { name: 'Max concurrency' })
    const retries = screen.getByRole('spinbutton', { name: 'Max retries' })
    expect(concurrency).toHaveValue('1')
    expect(concurrency).toHaveAttribute('aria-valuemin', '1')
    expect(concurrency).toHaveAttribute('aria-valuemax', '100')
    expect(retries).toHaveValue('0')
    expect(retries).toHaveAttribute('aria-valuemin', '0')
    expect(retries).toHaveAttribute('aria-valuemax', '20')
    expect(defaultProps.onConfirm).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Resume', exact: true }))

    expect(defaultProps.onConfirm).toHaveBeenCalledWith({ max_concurrency: 1, max_retries: 0 })
  })

  it('accepts explicit execution limits at the supported upper bounds', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ScenarioResumeDialog {...defaultProps} /></TestWrapper>)
    await user.clear(screen.getByRole('spinbutton', { name: 'Max concurrency' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Max concurrency' }), '100')
    await user.tab()
    await user.clear(screen.getByRole('spinbutton', { name: 'Max retries' }))
    await user.type(screen.getByRole('spinbutton', { name: 'Max retries' }), '20')
    await user.tab()
    await user.click(screen.getByRole('button', { name: 'Resume', exact: true }))

    expect(defaultProps.onConfirm).toHaveBeenCalledWith({ max_concurrency: 100, max_retries: 20 })
  })

  it('does not accept a missing concurrency value', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ScenarioResumeDialog {...defaultProps} /></TestWrapper>)
    await user.clear(screen.getByRole('spinbutton', { name: 'Max concurrency' }))
    await user.tab()

    expect(screen.getByRole('button', { name: 'Resume', exact: true })).toBeDisabled()
    expect(screen.getByText('Enter a whole number from 1 to 100.')).toBeInTheDocument()
    expect(defaultProps.onConfirm).not.toHaveBeenCalled()
  })

  it('shows submit errors within the dialog and allows cancellation without submitting', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <ScenarioResumeDialog {...defaultProps} error="The saved target registration has changed." />
      </TestWrapper>,
    )
    expect(screen.getByText('The saved target registration has changed.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(defaultProps.onCancel).toHaveBeenCalledTimes(1)
    expect(defaultProps.onConfirm).not.toHaveBeenCalled()
  })

  it('disables confirmation, cancellation, and inputs while submitting', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><ScenarioResumeDialog {...defaultProps} pending /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Resuming...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('spinbutton', { name: 'Max concurrency' })).toBeDisabled()
    expect(screen.getByRole('spinbutton', { name: 'Max retries' })).toBeDisabled()
    await user.keyboard('{Escape}')

    expect(defaultProps.onCancel).not.toHaveBeenCalled()
    expect(defaultProps.onConfirm).not.toHaveBeenCalled()
  })
})
