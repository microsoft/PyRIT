import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi } from '@/services/api'
import type { RuntimeStatus } from '@/types'

import Reinitialize from './Reinitialize'

jest.mock('@/services/api', () => ({
  configurationApi: {
    getRuntimeStatus: jest.fn(),
    reinitialize: jest.fn(),
    cancelPendingApply: jest.fn(),
  },
}))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const ready: RuntimeStatus = {
  state: 'ready', generation: 'old', version: 'saved-v1', enabled: true, applying: false,
  outcome: 'success', message: 'PyRIT is ready.', work_revision: 4,
  active_work: { scenario_ids: [], preparing: 0, sends: 0, requests: 0, estimates: 0 },
}
const api = jest.mocked(configurationApi)

describe('Reinitialize', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.getRuntimeStatus.mockResolvedValue(ready)
    api.reinitialize.mockResolvedValue({ ...ready, state: 'stopping', applying: true })
  })

  it('protects unsaved edits without implicitly saving or applying', async () => {
    render(<TestWrapper><Reinitialize version="saved-v1" hasUnsavedChanges /></TestWrapper>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled())
    expect(api.reinitialize).not.toHaveBeenCalled()
    expect(screen.getByText(/Save or explicitly discard/)).toBeInTheDocument()
    expect(screen.queryByText(/Apply saved files/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Runtime: ready/)).not.toBeInTheDocument()
  })

  it('enables reinitialization without a configuration opt-in', async () => {
    render(<TestWrapper><Reinitialize version="saved-v1" hasUnsavedChanges={false} /></TestWrapper>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
    expect(screen.queryByText(/opt-in/)).not.toBeInTheDocument()
  })

  it('explains the multi-worker safety restriction', async () => {
    api.getRuntimeStatus.mockResolvedValue({ ...ready, enabled: false })
    render(<TestWrapper><Reinitialize version="saved-v1" hasUnsavedChanges={false} /></TestWrapper>)
    await screen.findByText(/Reinitialization requires one backend worker and one replica/)
    expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeDisabled()
  })

  it('requires explicit stop confirmation and sends the displayed version and work revision', async () => {
    const user = userEvent.setup()
    api.getRuntimeStatus.mockResolvedValue({
      ...ready, active_work: { ...ready.active_work, scenario_ids: ['run-42'], preparing: 1, sends: 2 },
    })
    render(<TestWrapper><Reinitialize version="saved-v1" hasUnsavedChanges={false} /></TestWrapper>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Reinitialize PyRIT' }))
    const dialog = await screen.findByRole('dialog')
    const table = within(dialog).getByRole('table', { name: 'Active runtime work' })
    expect(within(table).getByText('Scenarios')).toBeInTheDocument()
    expect(within(table).getByText('run-42')).toBeInTheDocument()
    expect(within(table).getByText('Preparing')).toBeInTheDocument()
    expect(within(dialog).queryByText(/Preparation threads/)).not.toBeInTheDocument()
    expect(api.reinitialize).not.toHaveBeenCalled()
    await user.click(within(dialog).getByRole('button', { name: 'Stop scenarios and reinitialize' }))
    await waitFor(() => expect(api.reinitialize).toHaveBeenCalledWith('saved-v1', true, 4))
    expect(await screen.findByText(/Runtime: stopping/)).toBeInTheDocument()
  })

  it('keeps retry and pending cancellation available after a drain timeout', async () => {
    const user = userEvent.setup()
    api.getRuntimeStatus.mockResolvedValue({
      ...ready, state: 'blocked', outcome: 'stop-timeout', message: 'Waiting for preparation.',
    })
    api.cancelPendingApply.mockResolvedValue(ready)
    render(<TestWrapper><Reinitialize version="saved-v1" hasUnsavedChanges={false} /></TestWrapper>)
    expect(await screen.findByRole('button', { name: 'Retry reinitialization' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Cancel pending apply' }))
    await waitFor(() => expect(api.cancelPendingApply).toHaveBeenCalledTimes(1))
    expect(api.reinitialize).not.toHaveBeenCalled()
  })

  it('does not stop work when the warning is dismissed', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><Reinitialize version="saved-v1" hasUnsavedChanges={false} /></TestWrapper>)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reinitialize PyRIT' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Reinitialize PyRIT' }))
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(api.reinitialize).not.toHaveBeenCalled()
  })
})
