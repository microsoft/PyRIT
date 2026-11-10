import { useState } from 'react'
import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { runtimeApi } from '@/services/api'
import type { RestoreFailure } from '@/types'

import { RuntimeBanner, RuntimeProvider, useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({
  runtimeApi: { getReadiness: jest.fn(), getRestoreFailures: jest.fn() },
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

function renderBanner() {
  render(<TestWrapper><RuntimeProvider><RuntimeBanner /></RuntimeProvider></TestWrapper>)
}

describe('RuntimeBanner', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('lists the saved entries that could not be restored while the runtime is ready', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 2,
    })
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

    renderBanner()

    const list = await screen.findByRole('list', { name: "Saved entries that couldn't be restored" })
    expect(await within(list).findByText('Target pool: It uses an entry that does not exist. (target gone)')).toBeInTheDocument()
    expect(within(list).getByText('Saved converters: The folder could not be read.')).toBeInTheDocument()
    expect(screen.getByText(/fixes or\s+deletes their saved files, then applies the configuration/)).toBeInTheDocument()
    expect(screen.queryByText(/has to name its techniques/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says a scan has to name its techniques while a saved technique is broken', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 1,
    })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({
      items: [{ ...brokenTarget('formal'), kind: 'techniques' }],
    })

    renderBanner()

    expect(await screen.findByText(/a scan has to name its techniques/)).toBeInTheDocument()
  })

  it('shows nothing and asks for no list when every saved entry was restored', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 0,
    })

    renderBanner()

    await waitFor(() => expect(runtimeApi.getReadiness).toHaveBeenCalled())
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(runtimeApi.getRestoreFailures).not.toHaveBeenCalled()
  })

  it('does not ask for restore failures while the runtime is not ready', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: false,
      state: 'restart-required',
      generation: '',
      restore_failures: 1,
    })

    renderBanner()

    expect(await screen.findByText(/PyRIT runtime: restart-required/)).toBeInTheDocument()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(runtimeApi.getRestoreFailures).not.toHaveBeenCalled()
  })

  it('reads the list again only when the count or the generation changes', async () => {
    jest.useFakeTimers()
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'first', restore_failures: 1 })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()
    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(4_000) })
    expect(runtimeApi.getRestoreFailures).toHaveBeenCalledTimes(1)

    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('other')] })
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'second', restore_failures: 1 })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByText('Target other: It is broken.')).toBeInTheDocument()
    expect(runtimeApi.getRestoreFailures).toHaveBeenCalledTimes(2)
    jest.useRealTimers()
  })

  it('hides the list once nothing fails any more', async () => {
    jest.useFakeTimers()
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'first', restore_failures: 1 })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()
    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'second', restore_failures: 0 })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    await waitFor(() => expect(screen.queryByText('Target ghost: It is broken.')).not.toBeInTheDocument())
    jest.useRealTimers()
  })

  it('tries again when the list could not be loaded, keeping the last list', async () => {
    jest.useFakeTimers()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 1,
    })
    jest.mocked(runtimeApi.getRestoreFailures)
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()
    expect(await screen.findByText(/list of saved entries couldn't be loaded/)).toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()
    expect(screen.queryByText(/list of saved entries couldn't be loaded/)).not.toBeInTheDocument()
    jest.useRealTimers()
  })

  it('lets a long list scroll on its own', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 40,
    })
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({
      items: Array.from({ length: 40 }, (_, index) => brokenTarget(`target_${index}`)),
    })

    renderBanner()

    const list = await screen.findByRole('list', { name: "Saved entries that couldn't be restored" })
    expect(list).toHaveAttribute('tabindex', '0')
    expect(within(list).getAllByRole('listitem')).toHaveLength(40)
  })
})
