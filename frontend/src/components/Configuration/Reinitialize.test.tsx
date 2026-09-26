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
})
