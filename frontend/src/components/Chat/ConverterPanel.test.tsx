import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi } from '@/services/api'
import type { ConverterInstance } from '@/types'

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

function renderPanel(
  props: Partial<React.ComponentProps<typeof ConverterPanel>> = {},
) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <ConverterPanel
        onClose={jest.fn()}
        previewText=""
        attachmentData={{}}
        activeInputTypes={['text']}
        onUseConvertedValues={jest.fn()}
        {...props}
      />
    </FluentProvider>,
  )
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

  it('shows the input as a non-editable display and only add actions before selection', async () => {
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })

    await screen.findByTestId('converter-panel-list')

    expect(screen.getByTestId('converter-input-value')).toHaveTextContent('hello')
    expect(screen.getByTestId('converter-input-value').querySelector('textarea')).toBeNull()
    expect(screen.getByText('Input - Text')).toBeInTheDocument()
    expect(screen.getByTestId('converter-panel-select')).toHaveTextContent('Add converter...')
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

    expect(await screen.findByTestId('converter-preview-result')).toHaveTextContent('aGVsbG8=')
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
    expect(screen.getByTestId('converter-stage-output-0')).toHaveTextContent('aGVsbG8=')
    expect(screen.getByTestId('converter-stage-output-1')).toHaveTextContent('aGVsbG8=-suffix')
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

    expect(await screen.findByTestId('converter-preview-result')).toHaveTextContent('converted')
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
    const onUseConvertedValues = jest.fn()
    mockedConvertersApi.previewConversion.mockResolvedValue(
      makePreviewResponse(['base64-default'], ['converted']),
    )
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello', onUseConvertedValues })
    await screen.findByTestId('converter-panel-list')
    await selectConverter('base64-default')
    await user.click(screen.getByTestId('converter-preview-btn'))
    expect(await screen.findByTestId('use-converted-btn')).toBeEnabled()
    await user.click(screen.getByTestId('use-converted-btn'))

    expect(onUseConvertedValues).toHaveBeenCalledWith([
      expect.objectContaining({
        converterInstanceIds: ['base64-default'],
        convertedValue: 'converted',
      }),
    ])
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
    const onUseConvertedValues = jest.fn()
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
      onUseConvertedValues,
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

    expect(mockedConvertersApi.previewConversion).toHaveBeenCalledTimes(2)
    await user.click(screen.getByTestId('converter-tab-image'))
    expect(await screen.findByTestId('converter-preview-result')).toContainElement(
      screen.getByRole('img', { name: 'Converted output preview' }),
    )
    await user.click(screen.getByTestId('use-converted-btn'))
    expect(onUseConvertedValues).toHaveBeenCalledWith(expect.arrayContaining([
      expect.objectContaining({ pieceType: 'text', convertedValue: 'aGVsbG8=' }),
      expect.objectContaining({ pieceType: 'image', convertedValue: outputImage }),
    ]))
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
})
