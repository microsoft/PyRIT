import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError, AxiosHeaders } from 'axios'

import { savedEntriesApi } from '@/services/api'
import type { SavedEntry } from '@/types'

import DeleteSavedEntryButton from './DeleteSavedEntryButton'

jest.mock('@/services/api', () => ({
  savedEntriesApi: { get: jest.fn(), delete: jest.fn() },
}))

const mockedApi = savedEntriesApi as jest.Mocked<typeof savedEntriesApi>

const ENTRY: SavedEntry = {
  kind: 'converters',
  name: 'cat_replace',
  version: 'v1',
  problem: null,
  can_change: true,
}

function apiError(status: number, detail: string): AxiosError {
  const headers = new AxiosHeaders()
  return new AxiosError('Request failed', 'ERR_BAD_REQUEST', { headers }, null, {
    status, statusText: '', headers: {}, config: { headers }, data: { detail },
  })
}

async function openDialog(onDeleted = jest.fn()) {
  const user = userEvent.setup()
  render(
    <FluentProvider theme={webLightTheme}>
      <DeleteSavedEntryButton kind="converters" name="cat_replace" onDeleted={onDeleted} />
    </FluentProvider>,
  )
  await user.click(screen.getByRole('button', { name: 'Delete saved converter cat_replace' }))
  const dialog = await screen.findByRole('dialog', { name: "Delete saved converter 'cat_replace'?" })
  return { user, dialog, onDeleted }
}

describe('DeleteSavedEntryButton', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('deletes the version it showed', async () => {
    mockedApi.get.mockResolvedValue(ENTRY)
    mockedApi.delete.mockResolvedValue()
    const { user, dialog, onDeleted } = await openDialog()

    expect(await within(dialog).findByText(/deleted for everyone and doesn't come back after the backend restarts/))
      .toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(mockedApi.delete).toHaveBeenCalledWith('converters', 'cat_replace', 'v1')
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it("says that only an administrator can delete it when the person asking isn't one", async () => {
    mockedApi.get.mockResolvedValue({ ...ENTRY, can_change: false })
    const { dialog } = await openDialog()

    expect(await within(dialog).findByText('Only an administrator can delete it.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeDisabled()
  })

  it("deletes a file that can't be read without a version", async () => {
    mockedApi.get.mockResolvedValue({ ...ENTRY, version: null, problem: 'unreadable_recipe' })
    mockedApi.delete.mockResolvedValue()
    const { user, dialog } = await openDialog()

    expect(await within(dialog).findByText("Its saved file can't be used, so it's deleted as it is.")).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(mockedApi.delete).toHaveBeenCalledWith('converters', 'cat_replace', null)
  })

  it('shows why a delete was refused and keeps the dialog open', async () => {
    mockedApi.get.mockResolvedValue(ENTRY)
    const detail = "Saved converter 'cat_replace' is used by saved technique 'formal_send'. Delete or change it first."
    mockedApi.delete.mockRejectedValue(apiError(409, detail))
    const { user, dialog, onDeleted } = await openDialog()
    await within(dialog).findByText(/deleted for everyone/)

    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(detail)
    expect(onDeleted).not.toHaveBeenCalled()
    expect(within(dialog).queryByRole('button', { name: 'Look again' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeEnabled()
  })

  it('looks at a file again after it changed, and deletes its new version', async () => {
    mockedApi.get.mockResolvedValueOnce(ENTRY).mockResolvedValueOnce({ ...ENTRY, version: 'v2' })
    mockedApi.delete
      .mockRejectedValueOnce(apiError(409, "Saved converter 'cat_replace' changed since it was read. Look at it again before deleting it."))
      .mockResolvedValueOnce()
    const { user, dialog, onDeleted } = await openDialog()
    await within(dialog).findByText(/deleted for everyone/)

    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
    const lookAgain = await within(dialog).findByRole('button', { name: 'Look again' })
    // The version it showed is stale, so it can't be deleted until it's looked at again.
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeDisabled()
    await user.click(lookAgain)
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeEnabled())
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(mockedApi.delete).toHaveBeenLastCalledWith('converters', 'cat_replace', 'v2')
    await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1))
  })

  it("can't delete what it couldn't look up", async () => {
    mockedApi.get.mockRejectedValue(apiError(404, "No saved converter is named 'cat_replace'."))
    const { dialog } = await openDialog()

    expect(await within(dialog).findByRole('alert')).toHaveTextContent("No saved converter is named 'cat_replace'.")
    expect(within(dialog).getByRole('button', { name: 'Delete' })).toBeDisabled()
  })
})
