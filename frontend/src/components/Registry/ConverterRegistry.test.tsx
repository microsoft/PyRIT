import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi } from '@/services/api'

import ConverterRegistry from './ConverterRegistry'

jest.mock('@/services/api', () => ({
  convertersApi: {
    listConverters: jest.fn(),
    deleteConverter: jest.fn(),
  },
}))

// The real dialog loads converter type metadata of its own, but it stays a Fluent
// Dialog here so that focus restoration is exercised against real dialog behaviour.
jest.mock('./CreateConverterDialog', () => {
  const fluent = jest.requireActual('@fluentui/react-components')
  return {
    __esModule: true,
    default: ({ open, onClose, onCreated }: {
      open: boolean
      onClose: () => void
      onCreated: (converterId: string) => void
    }) => (
      <fluent.Dialog
        open={open}
        onOpenChange={(_: unknown, data: { open: boolean }) => { if (!data.open) onClose() }}
      >
        <fluent.DialogSurface>
          <fluent.DialogBody>
            <fluent.DialogTitle>Create converter</fluent.DialogTitle>
            <fluent.DialogActions>
              <fluent.Button onClick={onClose}>Cancel</fluent.Button>
              <fluent.Button onClick={() => onCreated('base64-default')}>Create</fluent.Button>
            </fluent.DialogActions>
          </fluent.DialogBody>
        </fluent.DialogSurface>
      </fluent.Dialog>
    ),
  }
})

const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>
const converter = {
  converter_id: 'base64-default',
  identifier: {
    class_name: 'Base64Converter',
    class_module: 'pyrit.converter.Base64Converter',
    hash: 'hash',
    pyrit_version: '0.0.0',
    supported_input_types: ['text'],
    supported_output_types: ['text'],
    encoding_func: 'b64encode',
  },
  is_llm_based: false,
}

function renderRegistry() {
  return render(
    <FluentProvider theme={webLightTheme}>
      <ConverterRegistry />
    </FluentProvider>,
  )
}

describe('ConverterRegistry', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [converter] })
    mockedConvertersApi.deleteConverter.mockResolvedValue()
  })

  it('lists registered converter instances and configuration', async () => {
    renderRegistry()

    expect(screen.getByText('Loading converters...')).toBeInTheDocument()
    expect(await screen.findByText('base64-default')).toBeInTheDocument()
    expect(screen.getByText('Base64Converter')).toBeInTheDocument()
    expect(screen.getByText('encoding_func: b64encode')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /set active/i })).not.toBeInTheDocument()
  })

  it('shows an empty state', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [] })
    renderRegistry()

    expect(await screen.findByText('No Converters Registered')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create First Converter' })).toBeInTheDocument()
  })

  it('shows an error and retries on refresh', async () => {
    mockedConvertersApi.listConverters
      .mockRejectedValueOnce(new Error('registry unavailable'))
      .mockResolvedValueOnce({ items: [converter] })
    const user = userEvent.setup()
    renderRegistry()

    expect(await screen.findByText(/registry unavailable/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    expect(await screen.findByText('base64-default')).toBeInTheDocument()
    expect(mockedConvertersApi.listConverters).toHaveBeenCalledTimes(2)
  })

  it('opens the shared create dialog', async () => {
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')

    await user.click(screen.getByRole('button', { name: 'New Converter' }))

    expect(screen.getByRole('dialog')).toHaveTextContent('Create converter')
  })

  it('confirms removal and refreshes the registry', async () => {
    mockedConvertersApi.listConverters
      .mockResolvedValueOnce({ items: [converter] })
      .mockResolvedValueOnce({ items: [] })
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')

    await user.click(screen.getByRole('button', { name: 'Remove base64-default' }))
    await user.click(screen.getByRole('button', { name: 'Remove' }))

    expect(mockedConvertersApi.deleteConverter).toHaveBeenCalledWith('base64-default')
    expect(await screen.findByText('No Converters Registered')).toBeInTheDocument()
  })

  it('should restore focus to New Converter after the add dialog is dismissed', async () => {
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')
    const trigger = screen.getByRole('button', { name: 'New Converter' })

    await user.click(trigger)
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('should restore focus to New Converter when the add dialog is dismissed with Escape', async () => {
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')
    const trigger = screen.getByRole('button', { name: 'New Converter' })

    await user.click(trigger)
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')

    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('should restore focus to Create First Converter after the add dialog is dismissed', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [] })
    const user = userEvent.setup()
    renderRegistry()
    const trigger = await screen.findByRole('button', { name: 'Create First Converter' })

    await user.click(trigger)
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it("should restore focus to the converter's Remove button after cancelling removal", async () => {
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')
    const trigger = screen.getByRole('button', { name: 'Remove base64-default' })

    await user.click(trigger)
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it("should restore focus to the converter's Remove button when removal is dismissed with Escape", async () => {
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')
    const trigger = screen.getByRole('button', { name: 'Remove base64-default' })

    await user.click(trigger)
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')

    await waitFor(() => expect(trigger).toHaveFocus())
  })

  it('should move focus to New Converter after a successful removal', async () => {
    mockedConvertersApi.listConverters
      .mockResolvedValueOnce({ items: [converter] })
      .mockResolvedValueOnce({ items: [] })
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')
    const newConverter = screen.getByRole('button', { name: 'New Converter' })

    await user.click(screen.getByRole('button', { name: 'Remove base64-default' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    expect(await screen.findByText('No Converters Registered')).toBeInTheDocument()
    await waitFor(() => expect(newConverter).toHaveFocus())
  })

  it('should move focus to New Converter after the first converter is created', async () => {
    mockedConvertersApi.listConverters
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ items: [converter] })
    const user = userEvent.setup()
    renderRegistry()
    const newConverter = screen.getByRole('button', { name: 'New Converter' })

    await user.click(await screen.findByRole('button', { name: 'Create First Converter' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    expect(await screen.findByText('base64-default')).toBeInTheDocument()
    await waitFor(() => expect(newConverter).toHaveFocus())
  })

  it('should leave focus alone when a dialog opens while the removal refresh is in flight', async () => {
    let releaseRefresh: (() => void) | undefined
    mockedConvertersApi.listConverters
      .mockResolvedValueOnce({ items: [converter] })
      .mockImplementationOnce(() => new Promise((resolve) => {
        releaseRefresh = () => resolve({ items: [] })
      }))
    const user = userEvent.setup()
    renderRegistry()
    await screen.findByText('base64-default')

    await user.click(screen.getByRole('button', { name: 'Remove base64-default' }))
    const removeDialog = await screen.findByRole('dialog')
    await user.click(within(removeDialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    // A restore is queued while the refresh runs; opening another dialog inside
    // that window must not hand focus back to the control behind it.
    await user.click(screen.getByRole('button', { name: 'New Converter' }))
    const addDialog = await screen.findByRole('dialog')
    await act(async () => { releaseRefresh?.() })
    await act(async () => {
      await new Promise<void>((resolve) => { requestAnimationFrame(() => resolve()) })
    })

    expect(addDialog).toContainElement(document.activeElement as HTMLElement)
  })
})
