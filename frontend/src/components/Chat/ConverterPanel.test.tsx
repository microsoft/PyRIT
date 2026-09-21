import { useMemo } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi } from '@/services/api'
import { useChatConverters } from '@/hooks/useChatConverters'
import type { ConverterInstance, ConverterPreviewResponse, MessageAttachment } from '@/types'

import ConverterPanel from './ConverterPanel'

jest.mock('@/services/api', () => ({
  convertersApi: {
    listConverters: jest.fn(),
    createConverter: jest.fn(),
    previewConversion: jest.fn(),
  },
}))

jest.mock('@/components/Registry/CreateConverterDialog', () => ({
  __esModule: true,
  default: ({
    open,
    onCreated,
  }: {
    open: boolean
    onCreated: (converterId: string) => void
  }) => open
    ? <button onClick={() => onCreated('new-converter')}>Complete converter creation</button>
    : null,
}))

const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>

function makeConverter(
  converterId: string,
  className = 'Base64Converter',
  inputTypes = ['text'],
  outputTypes = ['text'],
  isLlmBased = false,
  description = `Description for ${className}.`,
): ConverterInstance {
  return {
    converter_id: converterId,
    identifier: {
      class_name: className,
      class_module: `pyrit.converter.${className}`,
      hash: `${converterId}-hash`,
      pyrit_version: '0.0.0',
      supported_input_types: inputTypes,
      supported_output_types: outputTypes,
    },
    is_llm_based: isLlmBased,
    description,
  }
}

const textConverter = makeConverter('base64-default')
const imageConverter = makeConverter(
  'image-compressor',
  'ImageCompressionConverter',
  ['image_path'],
  ['image_path'],
)

interface PanelHarnessProps {
  previewText?: string
  attachmentData?: Record<string, string>
  activeInputTypes?: string[]
  attachments?: MessageAttachment[]
  onClose?: () => void
  open?: boolean
}

function PanelHarness({
  previewText = '',
  attachmentData,
  activeInputTypes,
  attachments,
  onClose = jest.fn(),
  open = true,
}: PanelHarnessProps) {
  const media = useMemo(() => attachments ?? (activeInputTypes ?? [])
    .filter((type: string) => type !== 'text')
    .map((type: string): MessageAttachment => ({
      draftId: type,
      type: type as MessageAttachment['type'],
      name: `${type} input`,
      url: attachmentData?.[type] ?? '',
      sourceValue: attachmentData?.[type] ?? '',
      sourceDataType: type === 'file' ? 'binary_path' : `${type}_path`,
      mimeType: `${type}/example`,
    })), [attachments, attachmentData, activeInputTypes])
  const controller = useChatConverters(previewText, media)
  return (
    <FluentProvider theme={webLightTheme}>
      {open && <ConverterPanel onClose={onClose} controller={controller} />}
      <output data-testid="applied-conversions">{JSON.stringify(controller.applied)}</output>
    </FluentProvider>
  )
}

function renderPanel(props: PanelHarnessProps = {}) {
  const rendered = render(<PanelHarness {...props} />)
  return { ...rendered, rerender: (next: PanelHarnessProps) => rendered.rerender(<PanelHarness {...next} />) }
}

async function selectConverter(converterId: string) {
  const user = userEvent.setup()
  await user.click(screen.getByRole('combobox'))
  await user.click(await screen.findByTestId(`converter-option-${converterId}`))
}

function makePreviewResponse(
  converterIds: string[],
  outputs: string[],
  originalValue = 'hello',
) {
  const steps = converterIds.map((converterId, index) => ({
    converter_id: converterId,
    converter_type: index === 0 ? 'Base64Converter' : 'SuffixAppendConverter',
    input_value: index === 0 ? originalValue : outputs[index - 1],
    input_data_type: 'text',
    output_value: outputs[index],
    output_data_type: 'text',
  }))
  return {
    original_value: originalValue,
    original_value_data_type: 'text',
    converted_value: outputs.at(-1) ?? '',
    converted_value_data_type: 'text',
    steps,
  }
}

describe('ConverterPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [textConverter] })
  })

  it('loads registered converter instances', async () => {
    renderPanel()

    expect(screen.getByTestId('converter-panel-loading')).toBeInTheDocument()
    await screen.findByTestId('converter-panel-list')

    expect(mockedConvertersApi.listConverters).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId('converter-panel-loading')).not.toBeInTheDocument()
  })

  it('shows each converter name once and makes its full header draggable', async () => {
    const atbashConverter = makeConverter('AtBashConverter', 'AtBashConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [atbashConverter] })
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')

    await selectConverter('AtBashConverter')

    const card = screen.getByTestId('converter-item-AtBashConverter')
    expect(within(card).getAllByText('AtBashConverter')).toHaveLength(1)
    expect(screen.getByTestId('converter-drag-area-0')).toHaveAttribute('draggable', 'true')
    expect(screen.getByRole('button', { name: 'Convert' })).toBeInTheDocument()
  })

  it('shows the registry error', async () => {
    mockedConvertersApi.listConverters.mockRejectedValue(new Error('Registry unavailable'))
    renderPanel()

    expect(await screen.findByTestId('converter-panel-error')).toBeInTheDocument()
  })

  it('shows an editable working input and only add actions before selection', async () => {
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })

    await screen.findByTestId('converter-panel-list')

    expect(screen.getByRole('textbox', { name: 'Working input - Text' })).toHaveValue('hello')
    expect(screen.getByText('Input - Text')).toBeInTheDocument()
    expect(screen.getByTestId('converter-panel-select')).toHaveTextContent('Add converter...')
    expect(screen.getByTestId('converter-panel-select').compareDocumentPosition(
      screen.getByTestId('converter-input-value'),
    ) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByTestId('converter-preview-btn')).not.toBeInTheDocument()
    expect(screen.queryByTestId('converter-preview-result')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()

    await user.click(screen.getByRole('combobox', { name: 'Add converter' }))
    const options = screen.getAllByRole('option')
    expect(options[0]).toHaveTextContent('New converter')
    expect(screen.getByTestId('converter-option-base64-default')).toHaveTextContent(
      'Description for Base64Converter.',
    )
  })

  it('shows the create action first when no converters are registered', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [] })
    const user = userEvent.setup()
    renderPanel()

    await screen.findByTestId('converter-panel-list')
    await user.click(screen.getByRole('combobox', { name: 'Add converter' }))
    await user.click(screen.getByTestId('create-converter-option'))

    expect(screen.getByRole('button', { name: 'Complete converter creation' })).toBeInTheDocument()
  })

  it('applies an edited working input as a manual conversion without a converter', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [] })
    const user = userEvent.setup()
    renderPanel({ previewText: 'original' })
    await screen.findByTestId('converter-panel-list')
    const workingInput = screen.getByRole('textbox', { name: 'Working input - Text' })
    await user.clear(workingInput)
    await user.type(workingInput, 'manual result')

    expect(screen.queryByTestId('converter-preview-btn')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))

    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}').text).toEqual({
      pieceId: 'text',
      pieceType: 'text',
      converterInstanceIds: [],
      originalValue: 'original',
      convertedValue: 'manual result',
      convertedDataType: 'text',
    })
    expect(mockedConvertersApi.previewConversion).not.toHaveBeenCalled()
  })

  it('filters registered instances by active input type', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, imageConverter],
    })
    const user = userEvent.setup()
    renderPanel({ activeInputTypes: ['text', 'image'] })
    await screen.findByTestId('converter-panel-list')

    await user.click(screen.getByRole('combobox'))
    expect(screen.getByTestId('converter-option-base64-default')).toBeInTheDocument()
    expect(screen.queryByTestId('converter-option-image-compressor')).not.toBeInTheDocument()

    await user.keyboard('{Escape}')
    await user.click(screen.getByTestId('converter-tab-image'))
    await user.click(screen.getByRole('combobox'))
    expect(screen.getByTestId('converter-option-image-compressor')).toBeInTheDocument()
    expect(screen.queryByTestId('converter-option-base64-default')).not.toBeInTheDocument()
  })

  it('converts with the selected registry ID and does not create an instance', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(['base64-default'], ['aGVsbG8=']),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(await screen.findByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('aGVsbG8=')
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith({
      original_value: 'hello',
      converter_ids: ['base64-default'],
      original_value_data_type: 'text',
    })
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
  })

  it('keeps the picker available and converts an ordered converter chain', async () => {
    const secondConverter = makeConverter('suffix-default', 'SuffixAppendConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, secondConverter],
    })
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(
        ['base64-default', 'suffix-default'],
        ['aGVsbG8=', 'aGVsbG8=-suffix'],
      ),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')

    await selectConverter('base64-default')
    await selectConverter('suffix-default')
    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(screen.getByTestId('converter-item-base64-default')).toBeInTheDocument()
    expect(screen.getByTestId('converter-item-suffix-default')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('aGVsbG8=')
    expect(screen.getByRole('textbox', { name: 'Stage 2 output - Text' })).toHaveValue('aGVsbG8=-suffix')
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith({
      original_value: 'hello',
      converter_ids: ['base64-default', 'suffix-default'],
      original_value_data_type: 'text',
    })
  })

  it('removes one converter from the chain', async () => {
    const secondConverter = makeConverter('suffix-default', 'SuffixAppendConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, secondConverter],
    })
    const user = userEvent.setup()
    renderPanel()
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('suffix-default')

    await user.click(screen.getByRole('button', {
      name: 'Remove converter base64-default',
    }))

    expect(screen.queryByTestId('converter-item-base64-default')).not.toBeInTheDocument()
    expect(screen.getByTestId('converter-item-suffix-default')).toBeInTheDocument()
  })

  it('does not convert until Convert is pressed', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(['base64-default'], ['converted']),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')

    expect(mockedConvertersApi.previewConversion).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()

    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(await screen.findByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('converted')
  })

  it('surfaces a conversion failure for the active modality', async () => {
    mockedConvertersApi.previewConversion.mockRejectedValue(new Error('Conversion exploded'))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')

    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(await screen.findByTestId('converter-preview-error')).toHaveTextContent('Conversion exploded')
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
  })

  it('refreshes and selects a converter created from the shared dialog', async () => {
    mockedConvertersApi.listConverters
      .mockResolvedValueOnce({ items: [textConverter] })
      .mockResolvedValueOnce({
        items: [textConverter, makeConverter('new-converter', 'CaesarConverter')],
      })
    const user = userEvent.setup()
    renderPanel()
    await screen.findByTestId('converter-panel-list')

    await user.click(screen.getByRole('combobox', { name: 'Add converter' }))
    await user.click(screen.getByTestId('create-converter-option'))
    await user.click(screen.getByRole('button', { name: 'Complete converter creation' }))

    expect(await screen.findByTestId('converter-item-new-converter')).toBeInTheDocument()
    expect(mockedConvertersApi.listConverters).toHaveBeenCalledTimes(2)
  })

  it('returns the selected registry ID with the converted value', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(['base64-default'], ['converted']),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByTestId('converter-preview-btn'))
    expect(await screen.findByTestId('use-converted-btn')).toBeEnabled()
    await user.click(screen.getByTestId('use-converted-btn'))

    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}')).toEqual({
      text: expect.objectContaining({
        pieceId: 'text',
        converterInstanceIds: ['base64-default'],
        convertedValue: 'converted',
      }),
    })
  })

  it('reorders a pipeline with the keyboard before converting', async () => {
    const secondConverter = makeConverter('suffix-default', 'SuffixAppendConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, secondConverter],
    })
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(
        ['suffix-default', 'base64-default'],
        ['hello-suffix', 'aGVsbG8tc3VmZml4'],
      ),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('suffix-default')

    await user.click(screen.getByRole('button', { name: 'Reorder converter suffix-default' }))
    await user.keyboard('{ArrowUp}')
    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith(
      expect.objectContaining({ converter_ids: ['suffix-default', 'base64-default'] }),
    )
  })

  it.each([false, true])('keeps focus through consecutive keyboard moves (repeated converter: %s)', async (repeated: boolean) => {
    const secondConverter = makeConverter('suffix-default', 'SuffixAppendConverter')
    const thirdConverter = makeConverter('caesar-default', 'CaesarConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, secondConverter, thirdConverter],
    })
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(['base64-default'], ['converted']),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('suffix-default')
    await selectConverter(repeated ? 'base64-default' : 'caesar-default')
    const handle = screen.getByRole('button', {
      name: repeated
        ? 'Reorder converter base64-default, stage 2 of 2'
        : 'Reorder converter caesar-default',
    })
    await user.click(handle)
    await user.keyboard('{ArrowUp}')
    expect(handle).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(handle).toHaveFocus()
    expect(screen.getAllByRole('button', { name: /^Reorder converter/ })[0]).toBe(handle)
    await user.keyboard('{ArrowDown}{ArrowDown}')
    expect(handle).toHaveFocus()
    expect(screen.getAllByRole('button', { name: /^Reorder converter/ })[2]).toBe(handle)
    await user.keyboard('{ArrowUp}{ArrowUp}')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith(expect.objectContaining({
      converter_ids: [
        repeated ? 'base64-default' : 'caesar-default',
        'base64-default',
        'suffix-default',
      ],
    }))
  })

  it('ignores external file drops on converter cards', async () => {
    const secondConverter = makeConverter('suffix-default', 'SuffixAppendConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, secondConverter],
    })
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(
        ['base64-default', 'suffix-default'],
        ['aGVsbG8=', 'aGVsbG8=-suffix'],
      ),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('suffix-default')

    const fileTransfer = {
      files: [new File(['content'], 'input.txt', { type: 'text/plain' })],
      getData: () => '',
      types: ['Files'],
    }
    const targetCard = screen.getByTestId('converter-item-suffix-default')
    fireEvent.dragOver(targetCard, { dataTransfer: fileTransfer })
    fireEvent.drop(targetCard, { dataTransfer: fileTransfer })
    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith(
      expect.objectContaining({ converter_ids: ['base64-default', 'suffix-default'] }),
    )
  })

  it('distinguishes repeated converter stages for assistive technology', async () => {
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('base64-default')

    expect(screen.getByTestId('converter-item-base64-default')).toBeInTheDocument()
    expect(screen.getByTestId('converter-item-base64-default-2')).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Reorder converter base64-default, stage 1 of 2',
    })).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: 'Remove converter base64-default, stage 2 of 2',
    })).toBeInTheDocument()
  })

  it('preserves, converts, and applies text and image pipelines together', async () => {
    const inputImage = 'data:image/png;base64,aW5wdXQ='
    const outputImage = 'data:image/png;base64,b3V0cHV0'
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, imageConverter],
    })
    mockedConvertersApi.previewConversion.mockImplementation(async (request) => {
      if (request.original_value_data_type === 'image_path') {
        return {
          original_value: inputImage,
          original_value_data_type: 'image_path',
          converted_value: outputImage,
          converted_value_data_type: 'image_path',
          steps: [{
            converter_id: 'image-compressor',
            converter_type: 'ImageCompressionConverter',
            input_value: inputImage,
            input_data_type: 'image_path',
            output_value: outputImage,
            output_data_type: 'image_path',
          }],
        }
      }
      return makePreviewResponse(['base64-default'], ['aGVsbG8='])
    })
    const user = userEvent.setup()
    renderPanel({
      previewText: 'hello',
      attachmentData: { image: inputImage },
      activeInputTypes: ['text', 'image'],
    })
    await screen.findByTestId('converter-panel-list')

    await selectConverter('base64-default')
    await user.click(screen.getByTestId('converter-tab-image'))
    await selectConverter('image-compressor')
    expect(screen.getByTestId('converter-item-image-compressor')).toBeInTheDocument()
    expect(screen.getByTestId('converter-input-value').querySelector('img')).toHaveAttribute('src', inputImage)

    await user.click(screen.getByTestId('converter-tab-text'))
    expect(screen.getByTestId('converter-item-base64-default')).toBeInTheDocument()
    await user.click(screen.getByTestId('converter-preview-btn'))

    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1)
    await user.click(screen.getByTestId('converter-tab-image'))
    await user.click(screen.getByTestId('converter-preview-btn'))
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2)
    expect(await screen.findByTestId('converter-preview-result')).toContainElement(
      screen.getByRole('img', { name: 'Converted output preview' }),
    )
    await user.click(screen.getByTestId('use-converted-btn'))
    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}')).toEqual({
      text: expect.objectContaining({ pieceType: 'text', convertedValue: 'aGVsbG8=' }),
      image: expect.objectContaining({ pieceType: 'image', convertedValue: outputImage }),
    })
  })

  it.each([
    ['audio', 'audio_path', 'audio', 'data:audio/wav;base64,b3V0cHV0'],
    ['video', 'video_path', 'video', 'data:video/mp4;base64,b3V0cHV0'],
    ['file', 'binary_path', 'a', '/tmp/converted.bin'],
  ])(
    'renders %s inputs and outputs as media instead of paths',
    async (pieceType, dataType, mediaSelector, outputValue) => {
      const converter = makeConverter(
        `${pieceType}-converter`,
        `${pieceType}Converter`,
        [dataType],
        [dataType],
      )
      const inputValue = pieceType === 'file'
        ? '/tmp/input.bin'
        : `data:${pieceType}/example;base64,aW5wdXQ=`
      mockedConvertersApi.listConverters.mockResolvedValue({ items: [converter] })
      mockedConvertersApi.previewConversion.mockResolvedValue({
        original_value: inputValue,
        original_value_data_type: dataType,
        converted_value: outputValue,
        converted_value_data_type: dataType,
        steps: [{
          converter_id: converter.converter_id,
          converter_type: converter.identifier.class_name,
          input_value: inputValue,
          input_data_type: dataType,
          output_value: outputValue,
          output_data_type: dataType,
        }],
      })
      const user = userEvent.setup()
      renderPanel({
        activeInputTypes: ['text', pieceType],
        attachmentData: { [pieceType]: inputValue },
      })
      await screen.findByTestId('converter-panel-list')

      await user.click(screen.getByTestId(`converter-tab-${pieceType}`))
      await selectConverter(converter.converter_id)
      expect(screen.getByTestId('converter-input-value').querySelector(mediaSelector))
        .toBeInTheDocument()
      await user.click(screen.getByTestId('converter-preview-btn'))

      const output = await screen.findByTestId('converter-preview-result')
      expect(output.querySelector(mediaSelector)).toBeInTheDocument()
      expect(output).not.toHaveTextContent(outputValue)
    },
  )

  it('closes the panel', async () => {
    const onClose = jest.fn()
    const user = userEvent.setup()
    renderPanel({ onClose })
    await screen.findByTestId('converter-panel-list')

    await user.click(screen.getByRole('button', { name: 'Close converters' }))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('keeps the pipeline, outputs, and applied selection when the panel is reopened', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(makePreviewResponse(['base64-default'], ['converted']))
    const user = userEvent.setup()
    const panel = renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await screen.findByRole('textbox', { name: 'Stage 1 output - Text' })
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    const applied = screen.getByTestId('applied-conversions').textContent

    panel.rerender({ previewText: 'hello', open: false })
    expect(screen.queryByTestId('converter-panel')).not.toBeInTheDocument()
    panel.rerender({ previewText: 'hello' })

    expect(await screen.findByTestId('converter-item-base64-default')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('converted')
    expect(screen.getByTestId('applied-conversions')).toHaveTextContent(applied ?? '')
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1)
  })

  it('clears applied converters when their pipeline is removed', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(makePreviewResponse(['base64-default'], ['converted']))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(screen.getByTestId('applied-conversions')).toHaveTextContent('base64-default')

    await user.click(screen.getByRole('button', { name: 'Remove converter base64-default' }))

    expect(screen.getByTestId('applied-conversions')).toHaveTextContent('{}')
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
  })

  it.each(['input', 'pipeline'])('ignores a late response after the %s changes and changes back', async (changed: string) => {
    let finish: (response: ConverterPreviewResponse) => void = () => { throw new Error('Conversion not started') }
    mockedConvertersApi.previewConversion.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    const panel = renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()

    if (changed === 'input') {
      panel.rerender({ previewText: 'edited' })
      panel.rerender({ previewText: 'hello' })
    } else {
      await user.click(screen.getByRole('button', { name: 'Remove converter base64-default' }))
      await selectConverter('base64-default')
    }
    await act(async () => { finish(makePreviewResponse(['base64-default'], ['stale'])) })

    expect(screen.queryByText('stale')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
    expect(screen.getByTestId('applied-conversions')).toHaveTextContent('{}')
  })

  it('converts two images with the same filename independently and replaces old successes on partial failure', async () => {
    const attachments: MessageAttachment[] = ['first', 'second'].map((id: string) => ({
      draftId: id, type: 'image', name: 'same.png', mimeType: 'image/png',
      url: `data:image/png;base64,${id}`, sourceValue: `data:image/png;base64,${id}`,
    }))
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [imageConverter] })
    mockedConvertersApi.previewConversion.mockImplementation(async (request) => ({
      original_value: request.original_value,
      original_value_data_type: 'image_path',
      converted_value: `${request.original_value}-converted`,
      converted_value_data_type: 'image_path',
      steps: [{
        converter_id: 'image-compressor', converter_type: 'ImageCompressionConverter',
        input_value: request.original_value, input_data_type: 'image_path',
        output_value: `${request.original_value}-converted`, output_data_type: 'image_path',
      }],
    }))
    const user = userEvent.setup()
    renderPanel({ attachments })
    await screen.findByTestId('converter-panel-list')
    await user.click(screen.getByRole('tab', { name: 'Image' }))
    await selectConverter('image-compressor')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))

    expect(await screen.findAllByRole('img', { name: 'same.png preview' })).toHaveLength(2)
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(Object.keys(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}')))
      .toEqual(['first', 'second'])

    mockedConvertersApi.previewConversion.mockRejectedValueOnce(new Error('First image failed'))
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(await screen.findByTestId('converter-preview-error')).toHaveTextContent('First image failed')
    expect(screen.getByTestId('applied-conversions')).toHaveTextContent('{}')
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(Object.keys(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}')))
      .toEqual(['second'])
  })

  it('converts only inputs from the active tab', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [textConverter, imageConverter] })
    mockedConvertersApi.previewConversion.mockImplementation(async (request) => (
      makePreviewResponse(request.converter_ids, [`converted-${request.original_value}`], request.original_value)
    ))
    const attachments: MessageAttachment[] = [{
      draftId: 'image', type: 'image', name: 'image.png', mimeType: 'image/png',
      url: 'data:image/png;base64,aGVsbG8=', sourceValue: 'data:image/png;base64,aGVsbG8=',
    }]
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello', attachments })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1)
    expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith(expect.objectContaining({
      original_value: 'hello',
    }))

    await user.click(screen.getByRole('tab', { name: 'Image' }))
    await selectConverter('image-compressor')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2)
    expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith(expect.objectContaining({
      original_value: 'data:image/png;base64,aGVsbG8=',
    }))

    await user.click(screen.getByRole('tab', { name: 'Text (1)' }))
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' }))
      .toHaveValue('converted-hello')
  })

  it('preserves an unaffected piece when another input changes', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [textConverter, imageConverter] })
    mockedConvertersApi.previewConversion.mockImplementation(async (request) => (
      makePreviewResponse(request.converter_ids, ['converted'], request.original_value)
    ))
    const attachments: MessageAttachment[] = [{
      draftId: 'image', type: 'image', name: 'image.png', mimeType: 'image/png',
      url: 'data:image/png;base64,aGVsbG8=', sourceValue: 'data:image/png;base64,aGVsbG8=',
    }]
    const user = userEvent.setup()
    const panel = renderPanel({ previewText: 'hello', attachments })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('tab', { name: 'Image' }))
    await selectConverter('image-compressor')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))

    panel.rerender({ previewText: 'changed', attachments })
    await waitFor(() => expect(Object.keys(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}')))
      .toEqual(['image']))
  })

  it('converts a working input without changing the original applied value', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(makePreviewResponse(['base64-default'], ['output'], 'edited'))
    const user = userEvent.setup()
    const panel = renderPanel({ previewText: 'original' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    const workingInput = screen.getByRole('textbox', { name: 'Working input - Text' })
    await user.clear(workingInput)
    await user.type(workingInput, 'edited')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith(expect.objectContaining({ original_value: 'edited' }))
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}').text)
      .toEqual(expect.objectContaining({ originalValue: 'original', convertedValue: 'output' }))

    panel.rerender({ previewText: 'original', open: false })
    panel.rerender({ previewText: 'original' })
    await screen.findByTestId('converter-panel-list')
    expect(screen.getByRole('textbox', { name: 'Working input - Text' })).toHaveValue('edited')
    panel.rerender({ previewText: 'new chat input' })
    expect(screen.getByRole('textbox', { name: 'Working input - Text' })).toHaveValue('new chat input')
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
  })

  it('runs every remaining stage from an edited output without rerunning the prefix', async () => {
    const suffix = makeConverter('suffix', 'SuffixAppendConverter')
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [textConverter, suffix] })
    mockedConvertersApi.previewConversion
      .mockResolvedValueOnce(makePreviewResponse(['base64-default', 'suffix', 'base64-default'], ['one', 'two', 'three']))
      .mockResolvedValueOnce(makePreviewResponse(['suffix', 'base64-default'], ['edited-two', 'edited-three'], 'edited'))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('suffix')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))

    const firstOutput = screen.getByRole('textbox', { name: 'Stage 1 output - Text' })
    await user.clear(firstOutput)
    await user.type(firstOutput, 'edited')
    expect(screen.queryByRole('textbox', { name: 'Stage 2 output - Text' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Stage 3 output - Text' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
    expect(screen.getByTestId('applied-conversions')).toHaveTextContent('{}')
    await user.click(screen.getByRole('button', { name: 'Convert Text from stage 2 to end' }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith({
      original_value: 'edited', original_value_data_type: 'text', converter_ids: ['suffix', 'base64-default'],
    })
    expect(firstOutput).toHaveValue('edited')
    expect(screen.getByRole('textbox', { name: 'Stage 2 output - Text' })).toHaveValue('edited-two')
    expect(screen.getByRole('textbox', { name: 'Stage 3 output - Text' })).toHaveValue('edited-three')
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}').text)
      .toEqual(expect.objectContaining({
        originalValue: 'hello', convertedValue: 'edited-three',
        converterInstanceIds: ['base64-default', 'suffix', 'base64-default'],
      }))
  })

  it.each(['manual final', ''])('applies an edited final value %j without another conversion', async (value: string) => {
    mockedConvertersApi.previewConversion.mockResolvedValue(makePreviewResponse(['base64-default'], ['output']))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    const output = screen.getByRole('textbox', { name: 'Stage 1 output - Text' })
    await user.clear(output)
    if (value) await user.type(output, value)
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}').text.convertedValue).toBe(value)
    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: /from stage 2 to end/ })).not.toBeInTheDocument()
  })

  it('does not offer selection-only conversion for the final stage output', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(makePreviewResponse(['base64-default'], ['final output']))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('final output')
    expect(screen.queryByRole('button', { name: 'Convert selection only in Stage 1 output - Text' }))
      .not.toBeInTheDocument()
  })

  it('keeps the prefix when a stage is appended and clears outputs after a reorder', async () => {
    mockedConvertersApi.listConverters.mockResolvedValue({
      items: [textConverter, makeConverter('suffix', 'SuffixAppendConverter')],
    })
    mockedConvertersApi.previewConversion.mockResolvedValue(makePreviewResponse(['base64-default'], ['output']))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await selectConverter('suffix')
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('output')
    expect(screen.getByRole('button', { name: 'Convert selection only in Stage 1 output - Text' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Convert Text from stage 2 to end' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Reorder converter suffix' }))
    await user.keyboard('{ArrowUp}')
    expect(screen.queryByRole('textbox', { name: 'Stage 1 output - Text' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Convert Text from stage 2 to end' })).toBeDisabled()
  })

  it('ignores a remaining-chain response if its edited input changes during the run', async () => {
    let finish: (response: ConverterPreviewResponse) => void = () => { throw new Error('Not started') }
    mockedConvertersApi.previewConversion
      .mockResolvedValueOnce(makePreviewResponse(['base64-default', 'base64-default'], ['first', 'last']))
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Convert Text from stage 2 to end' }))
    await user.type(screen.getByRole('textbox', { name: 'Stage 1 output - Text' }), ' changed')
    await act(async () => { finish(makePreviewResponse(['base64-default'], ['stale'])) })
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('first changed')
    expect(screen.queryByRole('textbox', { name: 'Stage 2 output - Text' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
  })

  it('preserves editable upstream output when a remaining converter fails', async () => {
    mockedConvertersApi.previewConversion
      .mockResolvedValueOnce(makePreviewResponse(['base64-default', 'base64-default'], ['first', 'last']))
      .mockRejectedValueOnce(new Error('Target unavailable'))
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Convert Text from stage 2 to end' }))
    expect(await screen.findByTestId('converter-preview-error')).toHaveTextContent('Conversion from stage 2 failed')
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('first')
    expect(screen.queryByRole('textbox', { name: 'Stage 2 output - Text' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add converted value' })).toBeDisabled()
  })

  it('continues from editable text produced by a media converter using its actual output type', async () => {
    const captioner = makeConverter('caption', 'CaptionConverter', ['image_path'], ['text'])
    mockedConvertersApi.listConverters.mockResolvedValue({ items: [captioner, textConverter] })
    mockedConvertersApi.previewConversion
      .mockResolvedValueOnce(makePreviewResponse(['caption', 'base64-default'], ['caption', 'encoded'], 'photo.png'))
      .mockResolvedValueOnce(makePreviewResponse(['base64-default'], ['edited-encoded'], 'edited caption'))
    const user = userEvent.setup()
    renderPanel({ attachments: [{
      draftId: 'photo', name: 'photo.png', type: 'image', mimeType: 'image/png',
      sourceValue: 'photo.png', url: 'photo.png',
    }] })
    await screen.findByTestId('converter-panel-list')
    await user.click(screen.getByRole('tab', { name: 'Image' }))
    await selectConverter('caption')
    await selectConverter('base64-default')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    const caption = screen.getByRole('textbox', { name: 'Stage 1 output - photo.png' })
    await user.clear(caption)
    await user.type(caption, 'edited caption')
    await user.click(screen.getByRole('button', { name: 'Convert photo.png from stage 2 to end' }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith({
      original_value: 'edited caption', original_value_data_type: 'text', converter_ids: ['base64-default'],
    })
    await user.click(screen.getByRole('button', { name: 'Add converted value' }))
    expect(JSON.parse(screen.getByTestId('applied-conversions').textContent ?? '{}').photo)
      .toEqual(expect.objectContaining({
        originalValue: 'photo.png', convertedValue: 'edited-encoded', convertedDataType: 'text',
      }))
  })

  it('passes marked working text to the backend and restarts all stages from the top', async () => {
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(['base64-default', 'base64-default'], ['first', 'last']),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello world' })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await selectConverter('base64-default')
    const input = screen.getByRole('textbox', { name: 'Working input - Text' })
    await user.pointer([
      { target: input, offset: 0, keys: '[MouseLeft>]' },
      { target: input, offset: 5 },
      { keys: '[/MouseLeft]' },
    ])
    await user.click(screen.getByRole('button', { name: 'Convert selection only in Working input - Text' }))
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith({
      original_value: '\u27eahello\u27eb world', original_value_data_type: 'text',
      converter_ids: ['base64-default', 'base64-default'],
    })
    await user.type(screen.getByRole('textbox', { name: 'Stage 1 output - Text' }), ' edited')
    await user.click(screen.getByRole('button', { name: 'Convert', exact: true }))
    expect(mockedConvertersApi.previewConversion).toHaveBeenLastCalledWith({
      original_value: '\u27eahello\u27eb world', original_value_data_type: 'text',
      converter_ids: ['base64-default', 'base64-default'],
    })
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('first')
    expect(screen.getByRole('textbox', { name: 'Stage 2 output - Text' })).toHaveValue('last')
  })
})
