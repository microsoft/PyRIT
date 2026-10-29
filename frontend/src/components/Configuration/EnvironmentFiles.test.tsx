import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { configurationApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'

import EnvironmentFiles from './EnvironmentFiles'
import { advanceAutoRefresh, installAutoRefreshTimers } from '@/test-utils/autoRefresh'

installAutoRefreshTimers()

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
): ReturnType<typeof render> {
  return render(
    <FluentProvider theme={webLightTheme}>
      <EnvironmentFiles
        onUnsavedChangesChange={onUnsavedChangesChange}
      />
    </FluentProvider>,
  )
}

async function rejectEnvironmentSave(status = 409): Promise<{
  user: ReturnType<typeof userEvent.setup>
  editor: HTMLElement
  rendered: ReturnType<typeof render>
}> {
  const user = userEvent.setup()
  mockedConfigurationApi.updateEnvironmentFile.mockRejectedValueOnce({
    isAxiosError: true, response: { status, data: { detail: 'Environment save failed.' } },
  })
  const rendered = renderFiles()
  const editor = await screen.findByRole('textbox', { name: 'Environment file contents' })
  expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument()
  await user.type(editor, '# draft')
  await user.click(screen.getByRole('button', { name: 'Save' }))
  await screen.findByText('Environment save failed.')
  return { user, editor, rendered }
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
    await advanceAutoRefresh()
    expect(screen.getByText(/saved. Reinitialize PyRIT/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Dismiss notification' }))
    expect(screen.queryByText(/saved. Reinitialize PyRIT/i)).not.toBeInTheDocument()
    const reads = mockedConfigurationApi.listEnvironmentFiles.mock.calls.length
    await advanceAutoRefresh()
    expect(mockedConfigurationApi.listEnvironmentFiles).toHaveBeenCalledTimes(reads + 1)
    expect(screen.queryByText(/saved. Reinitialize PyRIT/i)).not.toBeInTheDocument()
  })

  it('should show an error when the file list cannot be loaded', async () => {
    mockedConfigurationApi.listEnvironmentFiles.mockRejectedValue(new Error('Environment unavailable'))
    renderFiles()

    expect(await screen.findByText('Environment unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Dismiss notification' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument()
    expect(screen.queryByText('No environment sources are enabled by the configuration.')).not.toBeInTheDocument()
  })

  it.each([400, 403, 500])('should not offer disk recovery for a non-conflict save error: %s', async (status: number) => {
    await rejectEnvironmentSave(status)
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument()
  })

  it('should retain the environment draft and version when recovery is cancelled', async () => {
    const { user, editor } = await rejectEnvironmentSave()
    await user.click(screen.getByRole('button', { name: 'Reload from disk' }))
    const confirmation = await screen.findByRole('dialog', { name: 'Reload from disk?' })
    await user.click(within(confirmation).getByRole('button', { name: 'Keep editing' }))
    await screen.findByRole('textbox', { name: 'Environment file contents' })
    expect(editor).toHaveValue('VALUE=before\n# draft')
    expect(mockedConfigurationApi.getEnvironmentFile).toHaveBeenCalledTimes(1)
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValueOnce({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=before\n# draft',
      exists: true, version: 'saved-v2',
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenLastCalledWith('0', {
      content: 'VALUE=before\n# draft', version: 'version-1',
    })
  })

  it('should adopt fresh environment content and version only after confirmed recovery succeeds', async () => {
    const { user, editor } = await rejectEnvironmentSave()
    const read = deferred<Awaited<ReturnType<typeof configurationApi.getEnvironmentFile>>>()
    mockedConfigurationApi.getEnvironmentFile.mockReturnValueOnce(read.promise)
    await user.click(screen.getByRole('button', { name: 'Reload from disk' }))
    await user.click(await screen.findByRole('button', { name: 'Discard and reload' }))
    await waitFor(() => expect(editor).toBeDisabled())
    await advanceAutoRefresh()
    expect(mockedConfigurationApi.getEnvironmentFile).toHaveBeenCalledTimes(2)
    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledTimes(1)
    await act(async () => { read.resolve({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=disk\n',
      exists: true, version: 'disk-v2',
    }) })
    await waitFor(() => expect(editor).toHaveValue('VALUE=disk\n'))
    expect(screen.getByRole('textbox', { name: 'Environment file contents' })).toBe(editor)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument()
    await user.type(editor, '# edit')
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValueOnce({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=disk\n# edit',
      exists: true, version: 'saved-v3',
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenLastCalledWith('0', {
      content: 'VALUE=disk\n# edit', version: 'disk-v2',
    })
  })

  it('should retain the environment draft and version when a confirmed recovery read fails', async () => {
    const { user, editor } = await rejectEnvironmentSave()
    mockedConfigurationApi.getEnvironmentFile.mockRejectedValueOnce(new Error('Recovery read failed.'))
    await user.click(screen.getByRole('button', { name: 'Reload from disk' }))
    await user.click(await screen.findByRole('button', { name: 'Discard and reload' }))
    expect(await screen.findByText('Recovery read failed.')).toBeInTheDocument()
    expect(editor).toHaveValue('VALUE=before\n# draft')
    expect(screen.getByRole('button', { name: 'Reload from disk' })).toBeEnabled()
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValueOnce({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=before\n# draft',
      exists: true, version: 'saved-v2',
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenLastCalledWith('0', {
      content: 'VALUE=before\n# draft', version: 'version-1',
    })
  })

  it('should scope conflict recovery to its file and preserve another file draft', async () => {
    mockedConfigurationApi.listEnvironmentFiles.mockResolvedValue({ items: [
      { id: '0', name: '.env', path: 'C:/config/.env', content: '', exists: true },
      { id: '1', name: '.env.local', path: 'C:/config/.env.local', content: '', exists: true },
    ] })
    mockedConfigurationApi.getEnvironmentFile.mockImplementation(async (id: string) => ({
      id, name: id === '0' ? '.env' : '.env.local', path: `C:/config/${id}`,
      content: 'VALUE=before\n', exists: true, version: 'version-1',
    }))
    const { user } = await rejectEnvironmentSave()
    await user.click(screen.getByRole('button', { name: /^\.env\.local/ }))
    const otherEditor = await screen.findByRole('textbox', { name: 'Environment file contents' })
    await user.type(otherEditor, '# other draft')
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '.env', exact: true }))
    await screen.findByRole('textbox', { name: 'Environment file contents' })
    mockedConfigurationApi.getEnvironmentFile.mockResolvedValueOnce({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=disk\n',
      exists: true, version: 'disk-v2',
    })
    await user.click(screen.getByRole('button', { name: 'Reload from disk' }))
    await user.click(await screen.findByRole('button', { name: 'Discard and reload' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Environment file contents' })).toHaveValue('VALUE=disk\n'))
    await user.click(screen.getByRole('button', { name: /^\.env\.local/ }))
    expect(await screen.findByRole('textbox', { name: 'Environment file contents' })).toHaveValue('VALUE=before\n# other draft')
  })

  it('should ignore a recovery response from a replaced runtime and keep the draft version', async () => {
    const { user, editor, rendered } = await rejectEnvironmentSave()
    const read = deferred<Awaited<ReturnType<typeof configurationApi.getEnvironmentFile>>>()
    mockedConfigurationApi.getEnvironmentFile.mockReturnValueOnce(read.promise)
    await user.click(screen.getByRole('button', { name: 'Reload from disk' }))
    await user.click(await screen.findByRole('button', { name: 'Discard and reload' }))
    await waitFor(() => expect(mockedConfigurationApi.getEnvironmentFile).toHaveBeenCalledTimes(2))
    mockGeneration = 'second'
    rendered.rerender(<FluentProvider theme={webLightTheme}>
      <EnvironmentFiles onUnsavedChangesChange={jest.fn()} />
    </FluentProvider>)
    await act(async () => { read.resolve({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=old-runtime\n',
      exists: true, version: 'old-runtime-version',
    }) })
    expect(editor).toHaveValue('VALUE=before\n# draft')
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValueOnce({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=before\n# draft',
      exists: true, version: 'saved-v2',
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenLastCalledWith('0', {
      content: 'VALUE=before\n# draft', version: 'version-1',
    })
  })

  it('should recover a failed selected-file read on the next automatic check', async () => {
    mockedConfigurationApi.getEnvironmentFile.mockRejectedValueOnce(new Error('File unavailable'))
    renderFiles()
    expect(await screen.findByText('File unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry|Reload/ })).not.toBeInTheDocument()
    await advanceAutoRefresh()
    expect(await screen.findByRole('textbox', { name: 'Environment file contents' })).toHaveValue('VALUE=before\n')
    expect(screen.queryByText('File unavailable')).not.toBeInTheDocument()
  })

  it('should not expose conflict recovery for a failed environment read with HTTP 409', async () => {
    mockedConfigurationApi.getEnvironmentFile.mockRejectedValueOnce({
      isAxiosError: true, response: { status: 409, data: { detail: 'Environment read conflict.' } },
    })
    renderFiles()
    expect(await screen.findByText('Environment read conflict.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument()
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

  it('should preserve a dirty draft across generations and timed checks until edits are discarded', async () => {
    const user = userEvent.setup()
    const props = { onUnsavedChangesChange: jest.fn() }
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
    await advanceAutoRefresh()
    expect(editor).toHaveValue('VALUE=before\n# draft')
    expect(mockedConfigurationApi.listEnvironmentFiles).toHaveBeenCalledTimes(1)
    await user.clear(editor)
    await user.type(editor, 'VALUE=before\n')
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'Environment file contents' }))
      .toHaveValue('VALUE=updated\n'))
  })

  it('should retain a new draft and its version when a background content read finishes late', async () => {
    const user = userEvent.setup()
    renderFiles()
    const editor = await screen.findByRole('textbox', { name: 'Environment file contents' })
    const pending = deferred<Awaited<ReturnType<typeof configurationApi.getEnvironmentFile>>>()
    mockedConfigurationApi.getEnvironmentFile.mockReturnValueOnce(pending.promise)
    await advanceAutoRefresh()
    await waitFor(() => expect(mockedConfigurationApi.getEnvironmentFile).toHaveBeenCalledTimes(2))
    await user.type(editor, '# keep draft')
    await act(async () => { pending.resolve({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=remote\n', exists: true, version: 'version-2',
    }) })
    expect(editor).toHaveValue('VALUE=before\n# keep draft')
    mockedConfigurationApi.updateEnvironmentFile.mockResolvedValueOnce({
      id: '0', name: '.env', path: 'C:/config/.env', content: 'VALUE=before\n# keep draft', exists: true, version: 'version-3',
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(mockedConfigurationApi.updateEnvironmentFile).toHaveBeenCalledWith('0', {
      content: 'VALUE=before\n# keep draft', version: 'version-1',
    })
  })

  it('should refresh clean sources when another client reinitializes', async () => {
    const props = { onUnsavedChangesChange: jest.fn() }
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
