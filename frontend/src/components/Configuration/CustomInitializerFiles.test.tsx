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
})
