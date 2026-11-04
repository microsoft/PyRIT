import { useState } from 'react'
import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, AxiosHeaders } from 'axios'

import { runtimeApi } from '@/services/api'
import type { RestoreFailure, RestoreFailureList, RestoreRetryResult } from '@/types'

import { RuntimeBanner, RuntimeProvider, useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({
  runtimeApi: { getReadiness: jest.fn(), getRestoreFailures: jest.fn(), retryRestore: jest.fn() },
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

function brokenTarget(name: string): RestoreFailure {
  return { kind: 'targets', name, reason: 'unknown_class', message: 'It is broken.', version: 'v1', dependency: null }
}

describe('RuntimeBanner', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('lists the saved entries that could not be restored and why', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({
      items: [
        {
          kind: 'targets',
          name: 'pool',
          reason: 'missing_dependency',
          message: 'It uses an entry that does not exist.',
          version: 'v1',
          dependency: { kind: 'targets', name: 'gone' },
        },
        {
          kind: 'converters',
          name: null,
          reason: 'unreadable_storage',
          message: 'The folder could not be read.',
          version: null,
          dependency: null,
        },
      ],
    })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)

    const list = await screen.findByRole('list', { name: "Saved entries that couldn't be restored" })
    expect(await within(list).findByText('Target pool: It uses an entry that does not exist. (target gone)')).toBeInTheDocument()
    expect(within(list).getByText('Saved converters: The folder could not be read.')).toBeInTheDocument()
    expect(screen.getByText(/fix or delete these saved files, then select Retry/)).toBeInTheDocument()
  })

  it('does not ask for restore failures in other states', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'restart-required', generation: '' })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)

    expect(await screen.findByText(/PyRIT runtime: restart-required/)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(runtimeApi.getRestoreFailures).not.toHaveBeenCalled()
  })

  it('tries again when the list could not be loaded', async () => {
    jest.useFakeTimers()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures)
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValue({ items: [brokenTarget('ghost')] })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()
    jest.useRealTimers()
  })

  it('keeps the list current while the runtime stays failed', async () => {
    jest.useFakeTimers()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('first')] })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    expect(await screen.findByText('Target first: It is broken.')).toBeInTheDocument()
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('second')] })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByText('Target second: It is broken.')).toBeInTheDocument()
    expect(screen.queryByText('Target first: It is broken.')).not.toBeInTheDocument()
    jest.useRealTimers()
  })

  it('retries and shows what still could not be restored', async () => {
    const user = userEvent.setup()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({
      items: [
        { kind: 'targets', name: 'first', reason: 'unknown_class', message: 'First is broken.', version: 'v1', dependency: null },
        { kind: 'targets', name: 'second', reason: 'unknown_class', message: 'Second is broken.', version: 'v1', dependency: null },
      ],
    })
    jest.mocked(runtimeApi.retryRestore).mockResolvedValue({
      state: 'failed',
      outcome: 'restore-failed',
      items: [
        { kind: 'targets', name: 'second', reason: 'unknown_class', message: 'Second is broken.', version: 'v2', dependency: null },
      ],
    })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await screen.findByText('Target first: First is broken.')
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({
      items: [
        { kind: 'targets', name: 'second', reason: 'unknown_class', message: 'Second is broken.', version: 'v2', dependency: null },
      ],
    })
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Target second: Second is broken.')).toBeInTheDocument()
    expect(screen.queryByText('Target first: First is broken.')).not.toBeInTheDocument()
    expect(runtimeApi.retryRestore).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Retry' })).toBeEnabled()
  })

  it('shows why a retry was refused', async () => {
    const user = userEvent.setup()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [] })
    jest.mocked(runtimeApi.retryRestore).mockRejectedValue(
      new AxiosError('Conflict', 'ERR_BAD_REQUEST', undefined, undefined, {
        status: 409,
        statusText: 'Conflict',
        headers: {},
        config: { headers: new AxiosHeaders() },
        data: { detail: 'Retrying requires one backend worker and one replica. Restart the backend instead.' },
      }),
    )

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Retrying requires one backend worker and one replica.')
  })

  it('checks readiness right after a retry', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })
    jest.mocked(runtimeApi.retryRestore).mockResolvedValue({ state: 'ready', outcome: 'success', items: [] })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await screen.findByText('Target ghost: It is broken.')
    const checksBeforeRetry = getReadiness.mock.calls.length
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'restored' })
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    // Well before the next scheduled check, which would otherwise hide a missing refresh.
    await waitFor(() => expect(getReadiness.mock.calls.length).toBeGreaterThan(checksBeforeRetry), { timeout: 1_000 })
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Retry/ })).not.toBeInTheDocument())
    jest.useRealTimers()
  })

  it('keeps the retry result when an older list arrives later', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getRestoreFailures = jest.mocked(runtimeApi.getRestoreFailures)
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    getRestoreFailures.mockResolvedValue({ items: [brokenTarget('first'), brokenTarget('second')] })
    jest.mocked(runtimeApi.retryRestore).mockResolvedValue({
      state: 'failed',
      outcome: 'restore-failed',
      items: [brokenTarget('second')],
    })

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await screen.findByText('Target first: It is broken.')
    let finishOlderRequest: (list: RestoreFailureList) => void = () => {}
    getRestoreFailures.mockImplementation(() => new Promise((resolve) => { finishOlderRequest = resolve }))
    await act(async () => { jest.advanceTimersByTime(2_000) })
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.queryByText('Target first: It is broken.')).not.toBeInTheDocument())
    await act(async () => { finishOlderRequest({ items: [brokenTarget('first'), brokenTarget('second')] }) })

    expect(screen.queryByText('Target first: It is broken.')).not.toBeInTheDocument()
    expect(screen.getByText('Target second: It is broken.')).toBeInTheDocument()
    jest.useRealTimers()
  })

  it('forgets a refused retry once the runtime recovers', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })
    jest.mocked(runtimeApi.retryRestore).mockRejectedValue(new Error('Network Error'))

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'later' })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    jest.useRealTimers()
  })

  it('drops a retry answer that arrives after the runtime recovered', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })
    let failRetry: (error: Error) => void = () => {}
    jest.mocked(runtimeApi.retryRestore).mockImplementation(
      () => new Promise((_resolve, reject) => { failRetry = reject }),
    )

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'recovered' })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    await act(async () => { failRetry(new Error('Network Error')) })
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    jest.useRealTimers()
  })

  it('drops a retry result that arrives after the runtime recovered and failed again', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    const getRestoreFailures = jest.mocked(runtimeApi.getRestoreFailures)
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    getRestoreFailures.mockResolvedValue({ items: [brokenTarget('ghost')] })
    let finishRetry: (result: RestoreRetryResult) => void = () => {}
    jest.mocked(runtimeApi.retryRestore).mockImplementation(
      () => new Promise<RestoreRetryResult>((resolve) => { finishRetry = resolve }),
    )

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'recovered' })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: 'recovered' })
    getRestoreFailures.mockResolvedValue({ items: [brokenTarget('current')] })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    await act(async () => { finishRetry({ state: 'failed', outcome: 'restore-failed', items: [brokenTarget('ghost')] }) })

    expect(screen.queryByText('Target ghost: It is broken.')).not.toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(2_000) })
    expect(screen.getByText('Target current: It is broken.')).toBeInTheDocument()
    jest.useRealTimers()
  })

  it('drops a retry answer once a new generation shows the runtime recovered between polls', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })
    let failRetry: (error: Error) => void = () => {}
    jest.mocked(runtimeApi.retryRestore).mockImplementation(
      () => new Promise((_resolve, reject) => { failRetry = reject }),
    )

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    getReadiness.mockResolvedValue({ ready: false, state: 'failed', generation: 'applied' })
    await act(async () => { jest.advanceTimersByTime(2_000) })
    await act(async () => { failRetry(new Error('Network Error')) })

    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    jest.useRealTimers()
  })

  it('pauses list refreshes while a retry runs', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const getRestoreFailures = jest.mocked(runtimeApi.getRestoreFailures)
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({ ready: false, state: 'failed', generation: '' })
    getRestoreFailures.mockResolvedValue({ items: [brokenTarget('first'), brokenTarget('second')] })
    let finishRetry: (result: RestoreRetryResult) => void = () => {}
    jest.mocked(runtimeApi.retryRestore).mockImplementation(
      () => new Promise<RestoreRetryResult>((resolve) => { finishRetry = resolve }),
    )

    render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
    await screen.findByText('Target first: It is broken.')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    const listReadsBeforeRetryEnds = getRestoreFailures.mock.calls.length
    await act(async () => { jest.advanceTimersByTime(4_000) })
    getRestoreFailures.mockResolvedValue({ items: [brokenTarget('second')] })
    await act(async () => { finishRetry({ state: 'failed', outcome: 'restore-failed', items: [brokenTarget('second')] }) })

    expect(getRestoreFailures.mock.calls.length).toBe(listReadsBeforeRetryEnds)
    expect(screen.queryByText('Target first: It is broken.')).not.toBeInTheDocument()
    jest.useRealTimers()
  })
})
