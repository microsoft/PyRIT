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
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=before\n', exists: true,
    })
  })

  it('should load, edit, and save an environment file', async () => {
    const user = userEvent.setup()
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValue({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=after\n', exists: true,
    })
    renderFiles()

    const editor = await screen.findByRole('textbox', { name: 'Environment file contents' })
    await user.clear(editor)
    await user.type(editor, 'VALUE=after\n')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledWith('0', {
      content: 'VALUE=after\n',
    })
    expect(await screen.findByText(/restart PyRIT/i)).toBeInTheDocument()
  })

  it('should show an error when the file list cannot be loaded', async () => {
    mockedConfigurationApi.listEnvironmentFiles.mockRejectedValue(new Error('Environment unavailable'))
    renderFiles()

    expect(await screen.findByText('Environment unavailable')).toBeInTheDocument()
  })
})