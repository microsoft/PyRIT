import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi } from '@/services/api'
import type { ConverterInstance, SourceInstanceSpec } from '@/types'

import SaveConverterDialog from './SaveConverterDialog'

jest.mock('@/services/api', () => ({ convertersApi: { createConverter: jest.fn() } }))

function axiosError(status: number, data: Record<string, unknown>): Error {
  return Object.assign(new Error('Request failed'), { isAxiosError: true, response: { status, data } })
}

const source: SourceInstanceSpec = {
  source_name: 'caesar', source_hash: 'source', params: { caesar_offset: 7 }, effective_hash: 'effective',
}
const saved: ConverterInstance = {
  converter_id: 'saved', identifier: { class_name: 'CaesarConverter', hash: 'effective' },
}

function tree(onSaved = jest.fn(), onClose = jest.fn()) {
  return <FluentProvider theme={webLightTheme}>
    <SaveConverterDialog converterType="CaesarConverter" source={source} onSaved={onSaved} onClose={onClose} />
  </FluentProvider>
}

describe('SaveConverterDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(convertersApi.createConverter).mockResolvedValue(saved)
  })

  it('should say the copy comes back after a restart, rebuilt from the original', () => {
    render(tree())

    expect(screen.getByText(/rebuilt from the original with these changes\./)).toBeInTheDocument()
    expect(screen.queryByText(/The new converter is Temporary/)).not.toBeInTheDocument()
  })

  it('should require a name before creating a registry entry', async () => {
    const user = userEvent.setup()
    render(tree())
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    expect(await screen.findByText('Enter a registry name')).toBeInTheDocument()
    expect(convertersApi.createConverter).not.toHaveBeenCalled()
  })

  it('should save the applied source specification under the trimmed name with Enter', async () => {
    const user = userEvent.setup()
    const onSaved = jest.fn()
    render(tree(onSaved))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), ' saved {Enter}')
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true))
    expect(convertersApi.createConverter).toHaveBeenCalledWith({
      name: 'saved', type: 'CaesarConverter', source, persist: true,
    })
    expect(screen.queryByLabelText(/caesar_offset/)).not.toBeInTheDocument()
  })

  it('should show a registration error, keep the name, and permit retry', async () => {
    const user = userEvent.setup()
    jest.mocked(convertersApi.createConverter).mockRejectedValueOnce(new Error('This name is already registered.'))
    const onSaved = jest.fn()
    render(tree(onSaved))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This name is already registered.')
    expect(screen.getByRole('alert')).toHaveFocus()
    expect(screen.getByRole('textbox', { name: /Registry name/ })).toHaveValue('saved')
    expect(onSaved).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true))
    expect(convertersApi.createConverter).toHaveBeenCalledTimes(2)
  })

  it('should cancel without making a request', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    render(tree(jest.fn(), onClose))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(convertersApi.createConverter).not.toHaveBeenCalled()
  })

  it('should prevent duplicate submissions and mark a success after cancellation as stale', async () => {
    const user = userEvent.setup()
    let finish: (value: ConverterInstance) => void = () => { throw new Error('Save has not started') }
    jest.mocked(convertersApi.createConverter).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const onSaved = jest.fn()
    const onClose = jest.fn()
    render(tree(onSaved, onClose))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.dblClick(screen.getByRole('button', { name: 'Save', exact: true }))
    expect(convertersApi.createConverter).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Saving...' })).toHaveAttribute('aria-disabled', 'true')
    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledTimes(1)
    await act(async () => { finish(saved) })
    expect(onSaved).toHaveBeenCalledWith(saved, false)
  })

  it('should keep a late failure out of a newer dialog', async () => {
    const user = userEvent.setup()
    let fail: (reason: Error) => void = () => { throw new Error('Save has not started') }
    jest.mocked(convertersApi.createConverter).mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }))
    const first = render(tree())
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'old')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    first.unmount()
    render(tree())
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'new')
    await act(async () => { fail(new Error('Old failure')) })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /Registry name/ })).toHaveValue('new')
  })

  it("should offer a Temporary copy with the reason when the copy can't be saved", async () => {
    const user = userEvent.setup()
    const onSaved = jest.fn()
    jest.mocked(convertersApi.createConverter).mockRejectedValueOnce(axiosError(400, {
      type: '/errors/not-saveable',
      detail: "Copies temporary converter 'caesar', which won't exist after a restart. It can still be created without saving it.",
    }))
    render(tree(onSaved))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))

    expect(await screen.findByText("This converter can't be saved")).toBeInTheDocument()
    expect(screen.getByText(/Copies temporary converter 'caesar'/)).toBeInTheDocument()
    expect(onSaved).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Create as Temporary' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved, true))
    expect(jest.mocked(convertersApi.createConverter).mock.calls[1][0]).toEqual({
      name: 'saved', type: 'CaesarConverter', source, persist: false,
    })
  })

  it('should show a taken name on the name field until the name changes', async () => {
    const user = userEvent.setup()
    jest.mocked(convertersApi.createConverter).mockRejectedValueOnce(
      axiosError(409, { detail: "A saved converter named 'saved' already exists." }),
    )
    render(tree())
    const name = screen.getByRole('textbox', { name: /Registry name/ })
    await user.type(name, 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))

    expect(await screen.findByText("A saved converter named 'saved' already exists.")).toBeInTheDocument()
    expect(screen.getAllByText("A saved converter named 'saved' already exists.")).toHaveLength(1)
    await user.type(name, '_2')
    expect(screen.queryByText("A saved converter named 'saved' already exists.")).not.toBeInTheDocument()
  })

  it('should need a lowercase name to save the copy', async () => {
    const user = userEvent.setup()
    render(tree())
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'Saved-Copy')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))

    expect(
      await screen.findByText('Use lowercase letters, digits and underscores, starting with a letter (max 64 characters).'),
    ).toBeInTheDocument()
    expect(convertersApi.createConverter).not.toHaveBeenCalled()
  })
})
