import { useState } from 'react'
import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { runtimeApi, savedEntriesApi } from '@/services/api'
import type { RestoreFailure } from '@/types'

import { RuntimeBanner, RuntimeProvider, useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({
  runtimeApi: { getReadiness: jest.fn(), getRestoreFailures: jest.fn() },
  savedEntriesApi: { get: jest.fn(), delete: jest.fn() },
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

function failedRuntime(count: number) {
  return { ready: false, state: 'failed', generation: 'first', restore_failures: count }
}

describe('RuntimeBanner', () => {
  beforeEach(() => { jest.clearAllMocks() })
  afterEach(() => { jest.useRealTimers() })

  it('should list the saved entries that could not be restored while the runtime is not ready', async () => {
    const user = userEvent.setup()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(3))
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
          kind: 'targets',
          name: 'named',
          reason: 'missing_dependency',
          message: "Uses target 'gone', which doesn't exist.",
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

    expect(await screen.findByText("Some saved entries couldn't be restored")).toBeInTheDocument()
    expect(screen.getByText(new RegExp(
      "3 saved entries couldn't be restored, so new runs and sends are unavailable until they're fixed or deleted\\. "
      + 'Drafts are preserved\\. Whoever saved an entry, or an administrator, can delete it from the details, and '
      + 'an administrator can fix its file\\. Then apply the configuration or restart the backend\\.',
    ))).toBeInTheDocument()
    expect(screen.queryByText(/PyRIT runtime: failed/)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: "Saved entries that couldn't be restored" })).not.toBeInTheDocument()
    const toggle = screen.getByRole('button', { name: 'Show details' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await user.click(toggle)

    const region = screen.getByRole('region', { name: "Saved entries that couldn't be restored" })
    expect(await within(region).findByText('Target pool: It uses an entry that does not exist. (target gone)')).toBeInTheDocument()
    expect(within(region).getByText("Target named: Uses target 'gone', which doesn't exist.")).toBeInTheDocument()
    expect(within(region).getByText('Saved converters: The folder could not be read.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Hide details' })).toHaveAttribute('aria-expanded', 'true')
    // A folder that couldn't be read isn't one saved entry, so it has nothing to delete.
    expect(within(region).getAllByRole('button').map((button) => button.getAttribute('aria-label') ?? button.textContent)).toEqual([
      'Delete saved target pool',
      'Delete saved target named',
    ])
  })

  it('should delete a saved entry from the list and drop it there at once', async () => {
    const user = userEvent.setup()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(2))
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost'), brokenTarget('kept')] })
    jest.mocked(savedEntriesApi.get).mockResolvedValue({
      kind: 'targets', name: 'ghost', version: 'v1', problem: 'unknown_class',
      saved_by: null, saved_by_me: false, can_change: true,
    })
    jest.mocked(savedEntriesApi.delete).mockResolvedValue()

    renderBanner()
    await user.click(await screen.findByRole('button', { name: 'Show details' }))
    const list = screen.getByRole('region', { name: "Saved entries that couldn't be restored" })
    await user.click(await within(list).findByRole('button', { name: 'Delete saved target ghost' }))
    const dialog = await screen.findByRole('dialog', { name: "Delete saved target 'ghost'?" })
    await within(dialog).findByText(/deleted for everyone/)
    // Later refreshes never answer, so only the delete itself can take the entry off the list.
    jest.mocked(runtimeApi.getRestoreFailures).mockReturnValue(new Promise(() => {}))
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(savedEntriesApi.delete).toHaveBeenCalledWith('targets', 'ghost', 'v1')
    await waitFor(() => expect(within(list).queryByRole('button', { name: 'Delete saved target ghost' })).not.toBeInTheDocument())
    expect(within(list).getByRole('button', { name: 'Delete saved target kept' })).toBeInTheDocument()
  })

  it('should speak of one entry when one could not be restored', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(1))
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()

    expect(await screen.findByText(
      /1 saved entry couldn't be restored, so new runs and sends are unavailable until it's fixed or deleted\./,
    )).toBeInTheDocument()
  })

  it('should say to apply or restart once every entry that failed is deleted', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(0))

    renderBanner()

    expect(await screen.findByText(
      /PyRIT isn't ready.*An administrator can apply the configuration or restart the backend\./,
    )).toBeInTheDocument()
    expect(screen.queryByText("Some saved entries couldn't be restored")).not.toBeInTheDocument()
    expect(runtimeApi.getRestoreFailures).not.toHaveBeenCalled()
  })

  it('should show nothing and ask for no list while the runtime is ready', async () => {
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue({
      ready: true,
      state: 'ready',
      generation: 'first',
      restore_failures: 0,
    })

    renderBanner()

    await waitFor(() => expect(screen.queryByText(/PyRIT runtime/)).not.toBeInTheDocument())
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
    expect(runtimeApi.getRestoreFailures).not.toHaveBeenCalled()
  })

  it('should show the general runtime bar and ask for no list when restore did not fail', async () => {
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

  it('should read the list on every poll while the runtime stays failed', async () => {
    jest.useFakeTimers()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(1))
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()
    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()

    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('other')] })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByText('Target other: It is broken.')).toBeInTheDocument()
    expect(screen.queryByText('Target ghost: It is broken.')).not.toBeInTheDocument()
  })

  it('should stop reading the list once the runtime is ready', async () => {
    jest.useFakeTimers()
    const getReadiness = jest.mocked(runtimeApi.getReadiness)
    getReadiness.mockResolvedValue(failedRuntime(1))
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()
    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()
    getReadiness.mockResolvedValue({ ready: true, state: 'ready', generation: 'second', restore_failures: 0 })
    await act(async () => { jest.advanceTimersByTime(2_000) })

    await waitFor(() => expect(screen.queryByText("Some saved entries couldn't be restored")).not.toBeInTheDocument())
    const calls = jest.mocked(runtimeApi.getRestoreFailures).mock.calls.length
    await act(async () => { jest.advanceTimersByTime(4_000) })
    expect(runtimeApi.getRestoreFailures).toHaveBeenCalledTimes(calls)
  })

  it('should keep the last list and try again on the next poll when the list could not be loaded', async () => {
    jest.useFakeTimers()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(1))
    jest.mocked(runtimeApi.getRestoreFailures)
      .mockResolvedValueOnce({ items: [brokenTarget('ghost')] })
      .mockRejectedValueOnce(new Error('Network Error'))
      .mockResolvedValue({ items: [brokenTarget('ghost')] })

    renderBanner()
    expect(await screen.findByText('Target ghost: It is broken.')).toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(2_000) })

    expect(await screen.findByText(/list of saved entries couldn't be loaded/)).toBeInTheDocument()
    expect(screen.getByText('Target ghost: It is broken.')).toBeInTheDocument()
    await act(async () => { jest.advanceTimersByTime(2_000) })

    await waitFor(() => expect(screen.queryByText(/list of saved entries couldn't be loaded/)).not.toBeInTheDocument())
    expect(screen.getByText('Target ghost: It is broken.')).toBeInTheDocument()
  })

  it('should let a long list scroll on its own, from the keyboard too', async () => {
    const user = userEvent.setup()
    jest.mocked(runtimeApi.getReadiness).mockResolvedValue(failedRuntime(40))
    jest.mocked(runtimeApi.getRestoreFailures).mockResolvedValue({
      items: Array.from({ length: 40 }, (_, index) => brokenTarget(`target_${index}`)),
    })

    renderBanner()
    await user.click(await screen.findByRole('button', { name: 'Show details' }))

    const region = screen.getByRole('region', { name: "Saved entries that couldn't be restored" })
    expect(region).toHaveAttribute('tabindex', '0')
    await waitFor(() => expect(within(region).getAllByRole('listitem')).toHaveLength(40))
  })
})
