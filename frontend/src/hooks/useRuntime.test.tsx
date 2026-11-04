import { useState } from 'react'
import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { runtimeApi } from '@/services/api'
import type { RestoreFailure } from '@/types'

import { RuntimeBanner, RuntimeProvider, useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({ runtimeApi: { getReadiness: jest.fn(), getRestoreFailures: jest.fn() } }))

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
    expect(screen.getByText(/fix or delete these saved files, then restart the backend/)).toBeInTheDocument()
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
})
