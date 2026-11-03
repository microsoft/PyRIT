import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { initializersApi } from '@/services/api'

import CustomInitializerFiles from './CustomInitializerFiles'

jest.mock('@/services/api', () => ({
  initializersApi: {
    listCustom: jest.fn(),
    register: jest.fn(),
    unregister: jest.fn(),
  },
}))

const mockedInitializersApi = jest.mocked(initializersApi)
const customInitializer = {
  initializer_name: 'custom_target',
  script_content: 'class CustomTarget: pass',
  source: 'C:/custom/custom_target.py',
}

function renderFiles(): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <CustomInitializerFiles />
    </FluentProvider>,
  )
}

describe('CustomInitializerFiles', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedInitializersApi.listCustom.mockResolvedValue({ source: 'C:/custom', items: [customInitializer] })
    mockedInitializersApi.unregister.mockResolvedValue()
  })

  it('should load and remove a custom initializer', async () => {
    const user = userEvent.setup()
    renderFiles()

    expect(await screen.findByText(customInitializer.source, { selector: 'label' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    const dialog = screen.getByRole('dialog', { name: 'Remove custom initializer' })
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    expect(mockedInitializersApi.unregister).toHaveBeenCalledWith('custom_target')
    expect(await screen.findByText('Removed custom_target.')).toBeInTheDocument()
  })

  it('should show an error when custom initializers cannot be loaded', async () => {
    mockedInitializersApi.listCustom.mockRejectedValue(new Error('Custom initializers unavailable'))
    renderFiles()

    expect(await screen.findByText('Custom initializers unavailable')).toBeInTheDocument()
  })

  it('should handle registration success followed by refresh failure and retry independently (#3873)', async () => {
    const user = userEvent.setup()
    mockedInitializersApi.register.mockResolvedValue({
      initializer_name: 'new_target',
      source: 'C:/custom/new_target.py',
    })
    mockedInitializersApi.listCustom
      .mockResolvedValueOnce({ source: 'C:/custom', items: [customInitializer] })
      .mockRejectedValueOnce(new Error('Catalog refresh failed: 503'))
      .mockResolvedValueOnce({
        source: 'C:/custom',
        items: [
          customInitializer,
          {
            initializer_name: 'new_target',
            script_content: 'class NewTarget: pass',
            source: 'C:/custom/new_target.py',
          },
        ],
      })

    renderFiles()

    expect(await screen.findByText(customInitializer.source, { selector: 'label' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const dialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(dialog).getByRole('textbox', { name: /Initializer name/ }), 'new_target')
    await user.type(within(dialog).getByRole('textbox', { name: 'Python source' }), 'class NewTarget: pass')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    // Confirmed 201 registration happened
    expect(mockedInitializersApi.register).toHaveBeenCalledWith({
      name: 'new_target',
      script_content: 'class NewTarget: pass',
    })

    // Dialog should be closed because registration succeeded
    expect(screen.queryByRole('dialog', { name: 'Add custom initializer' })).not.toBeInTheDocument()

    // Warning message should indicate registration succeeded but refresh failed
    expect(
      await screen.findByText(
        'Added and registered new_target, but failed to refresh catalog: Catalog refresh failed: 503',
      ),
    ).toBeInTheDocument()

    // A Retry refresh button is available
    const retryButton = screen.getByRole('button', { name: 'Retry refresh' })
    expect(retryButton).toBeInTheDocument()

    // Clicking retry calls listCustom again WITHOUT repeating register POST
    await user.click(retryButton)
    expect(mockedInitializersApi.register).toHaveBeenCalledTimes(1)
    expect(mockedInitializersApi.listCustom).toHaveBeenCalledTimes(3)
    expect(await screen.findByText('Refreshed custom initializers catalog.')).toBeInTheDocument()
  })

  it('should show error and keep dialog open if registration itself fails', async () => {
    const user = userEvent.setup()
    mockedInitializersApi.register.mockRejectedValue(new Error('Name already registered: 409'))

    renderFiles()

    expect(await screen.findByText(customInitializer.source, { selector: 'label' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const dialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(dialog).getByRole('textbox', { name: /Initializer name/ }), 'duplicate_target')
    await user.type(within(dialog).getByRole('textbox', { name: 'Python source' }), 'class Duplicate: pass')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    expect(mockedInitializersApi.register).toHaveBeenCalledWith({
      name: 'duplicate_target',
      script_content: 'class Duplicate: pass',
    })

    // Dialog remains open because register failed
    expect(screen.getByRole('dialog', { name: 'Add custom initializer' })).toBeInTheDocument()
    expect(await screen.findByText('Name already registered: 409')).toBeInTheDocument()
  })
})
