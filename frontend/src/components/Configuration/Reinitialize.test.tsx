import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi } from '@/services/api'
import type { RuntimeStatus } from '@/types'

import Reinitialize from './Reinitialize'

jest.mock('@/services/api', () => ({
  configurationApi: {
    getRuntimeStatus: jest.fn(),
    reinitialize: jest.fn(),
  },
}))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const ready: RuntimeStatus = {
  state: 'ready',
  generation: 'old',
  version: 'saved-v1',
  enabled: true,
  applying: false,
  outcome: 'success',
  message: 'PyRIT is ready.',
}
const POLL_INTERVAL_MS = 1_000
const SLOW_RESPONSE_MS = 2_500
const api = jest.mocked(configurationApi)

describe('Reinitialize', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.getRuntimeStatus.mockResolvedValue(ready)
    api.reinitialize.mockResolvedValue({ ...ready, state: 'initializing', applying: true })
  })

  it('protects unsaved edits without applying', async () => {
    render(
      <TestWrapper>
        <Reinitialize version="saved-v1" hasUnsavedChanges liveReinitializationEnabled />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled())
    expect(api.reinitialize).not.toHaveBeenCalled()
    expect(screen.getByText(/Save or explicitly discard/)).toBeInTheDocument()
  })

  it('requires an explicit saved configuration opt-in', async () => {
    render(
      <TestWrapper>
        <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled={false} />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled())
    expect(screen.getByText(/enable_live_reinitialization: true/)).toBeInTheDocument()
  })

  it('explains the multi-worker safety restriction', async () => {
    api.getRuntimeStatus.mockResolvedValue({ ...ready, enabled: false })
    render(
      <TestWrapper>
        <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
      </TestWrapper>,
    )
    await screen.findByText(/Reinitialization requires one backend worker and one replica/)
    expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled()
  })

  it('confirms the idle-only contract and sends only the saved version', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Reinitialize PyRIT' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/runtime must be idle/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/restart the backend/i)).toBeInTheDocument()
    expect(within(dialog).queryByRole('table')).not.toBeInTheDocument()
    expect(api.reinitialize).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Reinitialize PyRIT' }))
    await waitFor(() => expect(api.reinitialize).toHaveBeenCalledWith('saved-v1'))
  })

  it('reports active work without stopping it', async () => {
    const user = userEvent.setup()
    api.reinitialize.mockResolvedValue({
      ...ready,
      outcome: 'busy',
      message: 'Wait for active work to finish, then retry.',
    })
    render(
      <TestWrapper>
        <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Reinitialize PyRIT' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reinitialize PyRIT' }))
    expect(await screen.findByText(/Wait for active work to finish, then retry/)).toBeInTheDocument()
  })

  it('requires a backend restart after a mutation failure', async () => {
    api.getRuntimeStatus.mockResolvedValue({
      ...ready,
      state: 'restart-required',
      outcome: 'restart-required',
      message: 'Restart the backend.',
    })
    render(
      <TestWrapper>
        <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
      </TestWrapper>,
    )
    expect(await screen.findByText(/Restart the backend/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled()
  })

  describe('runtime status polling', () => {
    beforeEach(() => {
      jest.useFakeTimers()
    })

    afterEach(() => {
      cleanup()
      jest.runOnlyPendingTimers()
      jest.useRealTimers()
      api.getRuntimeStatus.mockReset()
      api.reinitialize.mockReset()
    })

    it('should clear a mounted status-request error after three successful polls', async () => {
      const detail = 'Runtime status temporarily unavailable.'
      render(
        <TestWrapper>
          <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
        </TestWrapper>,
      )
      await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
      api.getRuntimeStatus.mockRejectedValueOnce({
        isAxiosError: true,
        response: { status: 503, data: { detail } },
      })

      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      expect(screen.getByText(detail)).toBeInTheDocument()

      for (let poll = 0; poll < 3; poll++) {
        await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      }

      expect(api.getRuntimeStatus).toHaveBeenCalledTimes(5)
      expect(screen.queryByText(detail)).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled()
    })

    it('should preserve an apply error while a simultaneous poll error recovers', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      const applyDetail = 'The saved configuration could not be applied.'
      const pollDetail = 'Runtime status temporarily unavailable.'
      api.reinitialize.mockRejectedValueOnce(new Error(applyDetail))
      render(
        <TestWrapper>
          <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
        </TestWrapper>,
      )
      await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
      await user.click(screen.getByRole('button', { name: 'Reinitialize PyRIT' }))
      await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reinitialize PyRIT' }))
      expect(await screen.findByText(applyDetail)).toBeInTheDocument()
      api.getRuntimeStatus.mockRejectedValueOnce(new Error(pollDetail))

      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      expect(screen.getByText(pollDetail)).toBeInTheDocument()
      expect(screen.getByText(applyDetail)).toBeInTheDocument()

      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      expect(screen.queryByText(pollDetail)).not.toBeInTheDocument()
      expect(screen.getByText(applyDetail)).toBeInTheDocument()
    })

    it('should preserve a poll error when applying is accepted', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      const detail = 'Runtime status temporarily unavailable.'
      render(
        <TestWrapper>
          <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
        </TestWrapper>,
      )
      await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
      api.getRuntimeStatus.mockRejectedValueOnce(new Error(detail))
      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      expect(screen.getByText(detail)).toBeInTheDocument()
      api.getRuntimeStatus.mockImplementation(() => new Promise<RuntimeStatus>((resolve: (status: RuntimeStatus) => void) => {
        setTimeout(() => { resolve(ready) }, 10_000)
      }))

      await user.click(screen.getByRole('button', { name: 'Reinitialize PyRIT' }))
      await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Reinitialize PyRIT' }))

      expect(await screen.findByText(/Runtime: initializing/)).toBeInTheDocument()
      expect(screen.getByText(detail)).toBeInTheDocument()
    })

    it('should retain a real restart-required status after a poll error recovers', async () => {
      const detail = 'Runtime status temporarily unavailable.'
      api.getRuntimeStatus.mockResolvedValue({
        ...ready,
        state: 'restart-required',
        outcome: 'restart-required',
        message: 'Restart the backend.',
      })
      render(
        <TestWrapper>
          <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
        </TestWrapper>,
      )
      expect(await screen.findByText(/Restart the backend/)).toBeInTheDocument()
      api.getRuntimeStatus.mockRejectedValueOnce(new Error(detail))
      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      expect(screen.getByText(detail)).toBeInTheDocument()

      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })

      expect(screen.queryByText(detail)).not.toBeInTheDocument()
      expect(screen.getByText(/Runtime: restart-required/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled()
    })

    it('should accept a slow poll before starting another and keep the next failure visible', async () => {
      const detail = 'The next runtime status request failed.'
      api.getRuntimeStatus.mockImplementationOnce(() => new Promise<RuntimeStatus>((resolve: (status: RuntimeStatus) => void) => {
        setTimeout(() => { resolve(ready) }, SLOW_RESPONSE_MS)
      }))
      render(
        <TestWrapper>
          <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
        </TestWrapper>,
      )

      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })
      expect(api.getRuntimeStatus).toHaveBeenCalledTimes(1)
      expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled()

      await act(async () => { jest.advanceTimersByTime(1_500) })
      expect(api.getRuntimeStatus).toHaveBeenCalledTimes(1)
      expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled()
      api.getRuntimeStatus.mockRejectedValueOnce(new Error(detail))

      await act(async () => { jest.advanceTimersByTime(500) })
      expect(api.getRuntimeStatus).toHaveBeenCalledTimes(2)
      expect(screen.getByText(detail)).toBeInTheDocument()
    })

    it('should stop polling after unmount while a slow request settles', async () => {
      api.getRuntimeStatus.mockImplementationOnce(() => new Promise<RuntimeStatus>((resolve: (status: RuntimeStatus) => void) => {
        setTimeout(() => { resolve(ready) }, SLOW_RESPONSE_MS)
      }))
      const { unmount } = render(
        <TestWrapper>
          <Reinitialize version="saved-v1" hasUnsavedChanges={false} liveReinitializationEnabled />
        </TestWrapper>,
      )
      await act(async () => { jest.advanceTimersByTime(POLL_INTERVAL_MS) })

      unmount()
      await act(async () => { jest.advanceTimersByTime(5_000) })

      expect(api.getRuntimeStatus).toHaveBeenCalledTimes(1)
      expect(screen.queryByRole('region', { name: 'Reinitialize PyRIT' })).not.toBeInTheDocument()
    })
  })
})
