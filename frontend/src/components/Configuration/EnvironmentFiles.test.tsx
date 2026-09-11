import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi } from '@/services/api'

import EnvironmentFiles from './EnvironmentFiles'

jest.mock('@/services/api', () => ({
  configurationApi: {
    listEnvironmentFiles: jest.fn(),
    getEnvironmentFile: jest.fn(),
    updateEnvironmentFile: jest.fn(),
  },
}))

const mockedConfigurationApi = jest.mocked(configurationApi)

function renderFiles(): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <EnvironmentFiles />
    </FluentProvider>,
  )
}

describe('EnvironmentFiles', () => {
  beforeEach(() => {
    jest.clearAllMocks()
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
    expect(await screen.findByText(/restart PyRIT/i)).toBeInTheDocument()
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
})
