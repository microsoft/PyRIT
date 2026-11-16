import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { TargetInstance } from '@/types'

import TargetKeyDialog from './TargetKeyDialog'

jest.mock('@/services/api', () => ({
  targetsApi: { setTargetCredential: jest.fn() },
}))

const setTargetCredential = jest.mocked(targetsApi.setTargetCredential)

const needsKey = makeTarget({
  target_registry_name: 'team/gpt', requires_caller_credential: true, caller_credential_configured: false,
})
const keySet = makeTarget({
  target_registry_name: 'team/gpt', requires_caller_credential: true, caller_credential_configured: true,
})

function renderDialog(target: TargetInstance = needsKey) {
  const onClose = jest.fn()
  const onSaved = jest.fn()
  render(
    <FluentProvider theme={webLightTheme}>
      <TargetKeyDialog target={target} onClose={onClose} onSaved={onSaved} />
    </FluentProvider>,
  )
  return { onClose, onSaved }
}

describe('TargetKeyDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should ask for the key in a password field and save only once one is entered', async () => {
    const user = userEvent.setup()
    setTargetCredential.mockResolvedValue({ ...needsKey, caller_credential_configured: true })
    const { onSaved, onClose } = renderDialog()

    expect(screen.getByRole('dialog')).toHaveTextContent('Set your key')
    expect(screen.getByRole('dialog')).toHaveTextContent('Your requests to team/gpt will use this key.')
    const input = screen.getByLabelText(/Your key/)
    expect(input).toHaveAttribute('type', 'password')
    const save = screen.getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    await user.type(input, '   ')
    expect(save).toBeDisabled()
    await user.clear(input)
    await user.type(input, 'sk-mine')
    expect(save).toBeEnabled()

    await user.click(save)

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(setTargetCredential).toHaveBeenCalledWith('team/gpt', 'sk-mine')
    expect(onClose).not.toHaveBeenCalled()
  })

  it('should save with Enter from the key field', async () => {
    const user = userEvent.setup()
    setTargetCredential.mockResolvedValue(keySet)
    const { onSaved } = renderDialog()

    await user.type(screen.getByLabelText(/Your key/), 'sk-mine{Enter}')

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(setTargetCredential).toHaveBeenCalledWith('team/gpt', 'sk-mine')
  })

  it('should offer to replace a key that is already set', () => {
    renderDialog(keySet)

    expect(screen.getByRole('dialog')).toHaveTextContent('Replace your key')
  })

  it('should show the API error, keep the dialog open, and allow a retry', async () => {
    const user = userEvent.setup()
    setTargetCredential.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 403, data: { detail: 'A verified caller identity is required to manage your own key.' } },
    })
    const { onSaved, onClose } = renderDialog()

    await user.type(screen.getByLabelText(/Your key/), 'sk-mine')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A verified caller identity is required to manage your own key.')
    await waitFor(() => expect(alert).toHaveFocus())
    expect(onSaved).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()

    setTargetCredential.mockResolvedValueOnce(keySet)
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('should close without saving when cancelled', async () => {
    const user = userEvent.setup()
    const { onClose, onSaved } = renderDialog()

    await user.type(screen.getByLabelText(/Your key/), 'sk-mine')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onSaved).not.toHaveBeenCalled()
    expect(setTargetCredential).not.toHaveBeenCalled()
  })

  it('should not close while the key is being saved', async () => {
    const user = userEvent.setup()
    let finish: (target: TargetInstance) => void = () => {}
    setTargetCredential.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const { onClose, onSaved } = renderDialog()

    await user.type(screen.getByLabelText(/Your key/), 'sk-mine')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(onClose).not.toHaveBeenCalled()

    await act(async () => { finish(keySet) })
    expect(onSaved).toHaveBeenCalledTimes(1)
  })
})
