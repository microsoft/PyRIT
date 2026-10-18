import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi } from '@/services/api'

import EnvironmentFiles from './EnvironmentFiles'

let mockGeneration = 'first'
jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ generation: mockGeneration }),
}))

jest.mock('@/services/api', () => ({
  configurationApi: {
    listEnvironmentFiles: jest.fn(),
    getEnvironmentFile: jest.fn(),
    updateEnvironmentFile: jest.fn(),
  },
}))

const mockedConfigurationApi = jest.mocked(configurationApi)

function renderFiles(
  onUnsavedChangesChange = jest.fn(),
  onRequestDiscardChanges = (discardChanges: () => void): void => discardChanges(),
): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <EnvironmentFiles
        onUnsavedChangesChange={onUnsavedChangesChange}
        onRequestDiscardChanges={onRequestDiscardChanges}
      />
    </FluentProvider>,
  )
}

describe('EnvironmentFiles', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGeneration = 'first'
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [{ id: '0', name: '.env', path: 'C:/config/.env', content: '', exists: true }],
    })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=before\n', exists: true, version: 'version-1',
    })
  })

  it('should load, edit, and save an environment file', async () => {
    const user = userEvent.setup()
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValue({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=after\n', exists: true, version: 'version-2',
    })
    renderFiles()

    const editor = await screen.findByRole('textbox', { name: 'Environment file contents' })
    await user.clear(editor)
    await user.type(editor, 'VALUE=after\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledWith('0', {
      content: 'VALUE=after\n',
      version: 'version-1',
    })
    expect(await screen.findByText(/saved. Reinitialize PyRIT/i)).toBeInTheDocument()
  })

  it('should show an error when the file list cannot be loaded', async () => {
    mockedConfigurationApi.listEnvironmentFiles.mockRejectedValue(new Error('Environment unavailable'))
    renderFiles()

    expect(await screen.findByText('Environment unavailable')).toBeInTheDocument()
  })

  it('should disable editing for an inline deployment-secret source', async () => {
    const reason = 'Update the deployment secret instead.'
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [{
        id: '0',
        name: '.env',
        path: '/home/vscode/.pyrit/.env',
        content: '',
        exists: true,
        read_only: true,
        read_only_reason: reason,
      }],
    })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: '0',
      name: '.env',
      path: '/home/vscode/.pyrit/.env',
      content: 'VALUE=before\n',
      exists: true,
      version: 'version-1',
      read_only: true,
      read_only_reason: reason,
    })

    renderFiles()

    expect(await screen.findByText(reason)).toBeInTheDocument()
    expect(await screen.findByRole('textbox', { name: 'Environment file contents' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })

  it('should report unsaved changes across environment file selection', async () => {
    const user = userEvent.setup()
    const onUnsavedChangesChange = jest.fn()
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({
      items: [
        { id: '0', name: '.env', path: 'C:/config/.env', content: '', exists: true },
        { id: '1', name: '.env.local', path: 'C:/config/.env.local', content: '', exists: true },
      ],
    })
    mockedConfigurationApi.getEnvironmentFile.mockImplementation(async (id: string) => ({
      id,
      name: id === '0' ? '.env' : '.env.local',
      path: id === '0' ? 'C:/config/.env' : 'C:/config/.env.local',
      content: id === '0' ? 'FIRST=saved\n' : 'SECOND=saved\n',
      exists: true,
      version: 'version-1',
    }))
    renderFiles(onUnsavedChangesChange)

    const firstEditor = await screen.findByRole('textbox', { name: 'Environment file contents' })
    await user.type(firstEditor, '# unsaved')
    expect(onUnsavedChangesChange).toHaveBeenLastCalledWith(true)

    await user.click(screen.getByRole('button', { name: /.env.local/i }))
    expect(await screen.findByRole('textbox', { name: 'Environment file contents' })).toHaveValue('SECOND=saved\n')
    expect(onUnsavedChangesChange).toHaveBeenLastCalledWith(true)
  })

  it('should preserve a dirty draft across generations and refresh sources after explicit reload', async () => {
    const user = userEvent.setup()
    const props = { onUnsavedChangesChange: jest.fn(), onRequestDiscardChanges: (discard: () => void) => discard() }
    const page = <FluentProvider theme={webLightTheme}><EnvironmentFiles {...props} /></FluentProvider>
    const { rerender } = render(page)
    const editor = await screen.findByRole('textbox', { name: 'Environment file contents' })
    await user.type(editor, '# draft')
    mockGeneration = 'second'
    rerender(<FluentProvider theme={webLightTheme}><EnvironmentFiles {...props} /></FluentProvider>)
    expect(editor).toHaveValue('VALUE=before\n# draft')
    expect(mockedConfigurationApi.listEnvironmentFiles).toHaveBeenCalledTimes(1)
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValue({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=updated\n', exists: true, version: 'version-2',
    })
    await user.click(screen.getByRole('button', { name: 'Reload file' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Environment file contents' }))
      .toHaveValue('VALUE=updated\n'))
  })

  it('should refresh clean sources when another client reinitializes', async () => {
    const props = { onUnsavedChangesChange: jest.fn(), onRequestDiscardChanges: jest.fn() }
    const { rerender } = render(
      <FluentProvider theme={webLightTheme}><EnvironmentFiles {...props} /></FluentProvider>,
    )
    await screen.findByRole('textbox', { name: 'Environment file contents' })
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({ items: [] })
    mockGeneration = 'second'
    rerender(<FluentProvider theme={webLightTheme}><EnvironmentFiles {...props} /></FluentProvider>)
    expect(await screen.findByText('No environment sources are enabled by the configuration.')).toBeInTheDocument()
  })
})
