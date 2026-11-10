import { useState } from 'react'
import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { runtimeApi } from '@/services/api'

import { RuntimeBanner, RuntimeProvider, useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({
  runtimeApi: { getReadiness: jest.fn() },
}))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function Consumer() {
  const runtime = useRuntime()
  const [draft, setDraft] = useState('')
  return (
    <>
      <input aria-label="Draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <button disabled={!runtime.ready}>Send</button>
      <output aria-label="Generation">{runtime.generation}</output>
    </>
  )
}

describe('RuntimeProvider', () => {
  beforeEach(() => { jest.clearAllMocks(); jest.useFakeTimers() })
  afterEach(() => { jest.useRealTimers() })

  it('blocks sends and observes another client apply without remounting drafts', async () => {
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'first' })
    render(<RuntimeProvider><Consumer /></RuntimeProvider>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled())
    await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'keep my draft')
    getReadiness.mockResolvedValue({ ready: false, state: 'stopping', generation: 'first' })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'second' })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    expect(screen.getByLabelText('Generation')).toHaveTextContent('second')
    expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue('keep my draft')
  })
})

function renderBanner() {
  render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
}

describe('RuntimeBanner', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should tell everyone that an administrator has to fix saved entries that could not be restored', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: false,
      state: 'failed',
      generation: 'first',
      restore_failures: 2,
    })

    renderBanner()

    expect(await screen.findByText(new RegExp(
      "Some saved entries couldn't be restored, so new runs and sends are unavailable\\. An administrator has to "
      + 'fix or delete them in Configuration, then reinitialize PyRIT or restart the backend\\. Drafts are preserved\\.',
    ))).toBeInTheDocument()
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByText(/PyRIT runtime: failed/)).not.toBeInTheDocument()
  })

  it('should show the general runtime bar while the runtime is not ready for another reason', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: false,
      state: 'restart-required',
      generation: '',
      restore_failures: 1,
    })

    renderBanner()

    expect(await screen.findByText(/PyRIT runtime: restart-required/)).toBeInTheDocument()
    expect(screen.queryByText(/couldn't be restored/)).not.toBeInTheDocument()
  })

  it('should show nothing while the runtime is ready', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 0,
    })

    renderBanner()

    await waitFor(() => expect(screen.queryByText(/PyRIT runtime/)).not.toBeInTheDocument())
    expect(screen.queryByText(/couldn't be restored/)).not.toBeInTheDocument()
  })

  it('should say to reinitialize or restart once every entry that failed to restore is deleted', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: false,
      state: 'failed',
      generation: 'first',
      restore_failures: 0,
    })

    renderBanner()

    expect(await screen.findByText(
      /PyRIT isn't ready.*An administrator can reinitialize PyRIT or restart the backend\./,
    )).toBeInTheDocument()
    expect(screen.queryByText(/couldn't be restored/)).not.toBeInTheDocument()
  })
})
