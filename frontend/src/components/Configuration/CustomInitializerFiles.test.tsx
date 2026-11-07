import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { initializersApi } from '@/services/api'

import CustomInitializerFiles from './CustomInitializerFiles'

let mockRuntimeGeneration = 'generation-1'

jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ ready: true, state: 'ready', generation: mockRuntimeGeneration }),
}))

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

const replacementInitializer = {
  initializer_name: 'replacement_target',
  script_content: 'class ReplacementTarget: pass',
  source: 'C:/custom/replacement_target.py',
}

function catalog(items = [customInitializer]) {
  return { source: 'C:/custom', items }
}

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T | PromiseLike<T>) => void
  reject: (reason?: unknown) => void
} {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function renderFiles() {
  return render(
    <FluentProvider theme={webLightTheme}>
      <CustomInitializerFiles />
    </FluentProvider>,
  )
}

describe('CustomInitializerFiles', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockRuntimeGeneration = 'generation-1'
    mockedInitializersApi.listCustom.mockResolvedValue(catalog())
    mockedInitializersApi.register.mockResolvedValue(undefined)
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
    const retryResponse = deferred<ReturnType<typeof catalog>>()
    mockedInitializersApi.listCustom
      .mockResolvedValueOnce(catalog())
      .mockRejectedValueOnce(new Error('Catalog refresh failed: 503'))
      .mockReturnValueOnce(retryResponse.promise)

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
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Add custom initializer' })).not.toBeInTheDocument()
    })

    // Warning message should indicate registration succeeded but refresh failed
    expect(
      await screen.findByText(
        'Added and registered new_target, but failed to refresh catalog: Catalog refresh failed: 503',
      ),
    ).toBeInTheDocument()

    // A Retry refresh button is available
    const retryButton = await screen.findByRole('button', { name: 'Retry refresh' })
    expect(retryButton).toBeInTheDocument()

    // Clicking retry calls listCustom again WITHOUT repeating register POST
    await user.click(retryButton)
    expect(retryButton).toBeDisabled()
    await user.click(retryButton)
    expect(mockedInitializersApi.register).toHaveBeenCalledTimes(1)
    expect(mockedInitializersApi.listCustom).toHaveBeenCalledTimes(3)
    await act(async () => {
      retryResponse.resolve(catalog([customInitializer, {
        initializer_name: 'new_target',
        script_content: 'class NewTarget: pass',
        source: 'C:/custom/new_target.py',
      }]))
      await retryResponse.promise
    })
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

  it('shows success for a normal registration when catalog refresh succeeds', async () => {
    const user = userEvent.setup()
    mockedInitializersApi.listCustom
      .mockResolvedValueOnce(catalog())
      .mockResolvedValueOnce(catalog([customInitializer, replacementInitializer]))
    renderFiles()

    expect(await screen.findByText(customInitializer.source, { selector: 'label' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const dialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(dialog).getByRole('textbox', { name: /Initializer name/ }), 'replacement_target')
    await user.type(within(dialog).getByRole('textbox', { name: 'Python source' }), 'class ReplacementTarget: pass')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    expect(mockedInitializersApi.register).toHaveBeenCalledWith({
      name: 'replacement_target',
      script_content: 'class ReplacementTarget: pass',
    })
    expect(await screen.findByText('Added and registered replacement_target.')).toBeInTheDocument()
    expect(await screen.findByText('replacement_target')).toBeInTheDocument()
  })

  it('ignores a delayed retry response after a successful delete', async () => {
    const user = userEvent.setup()
    const staleRetryResponse = deferred<ReturnType<typeof catalog>>()
    mockedInitializersApi.listCustom
      .mockResolvedValueOnce(catalog())
      .mockRejectedValueOnce(new Error('Catalog refresh failed: 503'))
      .mockReturnValueOnce(staleRetryResponse.promise)
      .mockResolvedValueOnce(catalog([]))
    renderFiles()

    expect(await screen.findByText(customInitializer.source, { selector: 'label' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const addDialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(addDialog).getByRole('textbox', { name: /Initializer name/ }), 'new_target')
    await user.type(within(addDialog).getByRole('textbox', { name: 'Python source' }), 'class NewTarget: pass')
    await user.click(within(addDialog).getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('button', { name: 'Retry refresh' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry refresh' }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    const removeDialog = screen.getByRole('dialog', { name: 'Remove custom initializer' })
    await user.click(within(removeDialog).getByRole('button', { name: 'Remove' }))

    expect(mockedInitializersApi.unregister).toHaveBeenCalledWith('custom_target')
    expect(await screen.findByText('Removed custom_target.')).toBeInTheDocument()
    expect(screen.queryByText(customInitializer.source, { selector: 'label' })).not.toBeInTheDocument()

    await act(async () => {
      staleRetryResponse.resolve(catalog())
      await staleRetryResponse.promise
    })
    await waitFor(() => {
      expect(screen.queryByText(customInitializer.source, { selector: 'label' })).not.toBeInTheDocument()
    })
  })

  it('keeps the newest runtime catalog when an older GET resolves last', async () => {
    const oldResponse = deferred<ReturnType<typeof catalog>>()
    const newResponse = deferred<ReturnType<typeof catalog>>()
    mockedInitializersApi.listCustom
      .mockReturnValueOnce(oldResponse.promise)
      .mockReturnValueOnce(newResponse.promise)
    const view = renderFiles()

    mockRuntimeGeneration = 'generation-2'
    view.rerender(
      <FluentProvider theme={webLightTheme}>
        <CustomInitializerFiles />
      </FluentProvider>,
    )
    await waitFor(() => expect(mockedInitializersApi.listCustom).toHaveBeenCalledTimes(2))

    await act(async () => {
      newResponse.resolve(catalog([replacementInitializer]))
      await newResponse.promise
    })
    expect(await screen.findByText(replacementInitializer.source, { selector: 'label' })).toBeInTheDocument()

    await act(async () => {
      oldResponse.resolve(catalog())
      await oldResponse.promise
    })
    await waitFor(() => {
      expect(screen.queryByText(replacementInitializer.source, { selector: 'label' })).toBeInTheDocument()
      expect(screen.queryByText(customInitializer.source, { selector: 'label' })).not.toBeInTheDocument()
    })
  })

  it('does not update state when a refresh resolves after unmount', async () => {
    const user = userEvent.setup()
    const refreshResponse = deferred<ReturnType<typeof catalog>>()
    mockedInitializersApi.listCustom
      .mockResolvedValueOnce(catalog())
      .mockRejectedValueOnce(new Error('Catalog refresh failed: 503'))
      .mockReturnValueOnce(refreshResponse.promise)
    const view = renderFiles()

    expect(await screen.findByText(customInitializer.source, { selector: 'label' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const dialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(dialog).getByRole('textbox', { name: /Initializer name/ }), 'new_target')
    await user.type(within(dialog).getByRole('textbox', { name: 'Python source' }), 'class NewTarget: pass')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))
    expect(await screen.findByRole('button', { name: 'Retry refresh' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry refresh' }))
    view.unmount()
    await act(async () => {
      refreshResponse.resolve(catalog([]))
      await refreshResponse.promise
    })
  })
})
