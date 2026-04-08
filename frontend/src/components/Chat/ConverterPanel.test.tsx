import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import ConverterPanel from './ConverterPanel'
import { convertersApi } from '../../services/api'
import type { ConverterCatalogResponse } from '../../types'

jest.setTimeout(60000)

jest.mock('../../services/api', () => ({
  convertersApi: {
    listConverterCatalog: jest.fn(),
    createConverter: jest.fn(),
    previewConversion: jest.fn(),
  },
}))

const mockedConvertersApi = convertersApi as jest.Mocked<typeof convertersApi>

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const MOCK_CATALOG = {
  items: [
    {
      converter_type: 'Base64Converter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [],
      is_llm_based: false,
      description: 'Encodes text to base64.',
    },
    {
      converter_type: 'CaesarConverter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [
        { name: 'caesar_offset', type_name: 'int', required: true, default_value: null, choices: null, description: 'Offset value.' },
      ],
      is_llm_based: false,
      description: 'Caesar cipher encoding.',
    },
    {
      converter_type: 'TranslationConverter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [],
      is_llm_based: true,
      description: 'Translates text using LLM.',
    },
    {
      converter_type: 'ImageCompressor',
      supported_input_types: ['image_path'],
      supported_output_types: ['image_path'],
      parameters: [],
      is_llm_based: false,
      description: 'Compresses images.',
    },
    {
      converter_type: 'BoolConverter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [
        { name: 'uppercase', type_name: 'bool', required: false, default_value: 'false', choices: null, description: 'Use uppercase.' },
      ],
      is_llm_based: false,
      description: 'Bool param test.',
    },
    {
      converter_type: 'ChoiceConverter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [
        { name: 'mode', type_name: 'str', required: false, default_value: 'fast', choices: ['fast', 'slow'], description: 'Speed mode.' },
      ],
      is_llm_based: false,
      description: 'Choice param test.',
    },
    {
      converter_type: 'FileParamConverter',
      supported_input_types: ['text'],
      supported_output_types: ['text'],
      parameters: [
        { name: 'template_file_path', type_name: 'str', required: false, default_value: null, choices: null, description: 'Path to template.' },
      ],
      is_llm_based: false,
      description: 'File path param test.',
    },
  ],
}

function renderPanel(props: Partial<React.ComponentProps<typeof ConverterPanel>> = {}) {
  const defaultProps = {
    onClose: jest.fn(),
    previewText: '',
    attachmentData: {},
    activeInputTypes: ['text'],
    onUseConvertedValue: jest.fn(),
  }
  return render(
    <TestWrapper>
      <ConverterPanel {...defaultProps} {...props} />
    </TestWrapper>,
  )
}

async function waitForList() {
  await waitFor(() => expect(screen.getByTestId('converter-panel-list')).toBeInTheDocument())
}

function getComboboxInput(): HTMLElement {
  const combobox = screen.getByTestId('converter-panel-select')
  const input = combobox.querySelector('input')
  return input ?? combobox
}

async function openComboboxAndSelect(converterType: string) {
  fireEvent.click(getComboboxInput())
  await waitFor(() => expect(screen.getByTestId(`converter-option-${converterType}`)).toBeInTheDocument())
  fireEvent.click(screen.getByTestId(`converter-option-${converterType}`))
  await waitFor(() => expect(screen.getByTestId(`converter-item-${converterType}`)).toBeInTheDocument())
}

beforeEach(() => {
  jest.clearAllMocks()
  mockedConvertersApi.listConverterCatalog.mockResolvedValue(MOCK_CATALOG as ConverterCatalogResponse)
})

// ─── Loading & Error ──────────────────────────────────────────────

describe('ConverterPanel loading', () => {
  it('shows loading spinner then renders converter list on success', async () => {
    renderPanel()
    expect(screen.getByTestId('converter-panel-loading')).toBeVisible()
    await waitForList()
    expect(screen.queryByTestId('converter-panel-loading')).not.toBeInTheDocument()
  })

  it('shows error message when catalog load fails', async () => {
    mockedConvertersApi.listConverterCatalog.mockRejectedValueOnce(new Error('Network fail'))
    renderPanel()
    await waitFor(() => expect(screen.getByTestId('converter-panel-error')).toBeVisible())
  })

  it('shows empty state when catalog returns no items', async () => {
    mockedConvertersApi.listConverterCatalog.mockResolvedValueOnce({ items: [] })
    renderPanel()
    await waitFor(() => expect(screen.getByTestId('converter-panel-empty')).toBeInTheDocument())
  })
})

// ─── Close button ────────────────────────────────────────────────

describe('ConverterPanel close', () => {
  it('calls onClose when close button is clicked', async () => {
    const onClose = jest.fn()
    renderPanel({ onClose })
    await waitForList()
    fireEvent.click(screen.getByTestId('close-converter-panel-btn'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

// ─── Tabs ─────────────────────────────────────────────────────────

describe('ConverterPanel tabs', () => {
  it('does not show tabs when only text type', async () => {
    renderPanel({ activeInputTypes: ['text'] })
    await waitForList()
    expect(screen.queryByTestId('converter-piece-tabs')).not.toBeInTheDocument()
  })

  it('shows tabs for multiple input types', async () => {
    renderPanel({ activeInputTypes: ['text', 'image'] })
    await waitForList()
    expect(screen.getByTestId('converter-piece-tabs')).toBeInTheDocument()
    expect(screen.getByTestId('converter-tab-text')).toBeInTheDocument()
    expect(screen.getByTestId('converter-tab-image')).toBeInTheDocument()
  })

  it('switches tab and resets state', async () => {
    renderPanel({
      previewText: 'hello',
      activeInputTypes: ['text', 'image'],
      attachmentData: { image: '/path/to/img.png' },
    })
    await waitForList()
    fireEvent.click(screen.getByTestId('converter-tab-image'))
    await waitFor(() => expect(screen.getByTestId('converter-panel-list')).toBeInTheDocument())
  })

  it('resets to text tab when active tab is removed', async () => {
    const onClose = jest.fn()
    const { rerender } = render(
      <TestWrapper>
        <ConverterPanel
          onClose={onClose}
          previewText=""
          activeInputTypes={['text', 'image']}
          onUseConvertedValue={jest.fn()}
        />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.getByTestId('converter-piece-tabs')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('converter-tab-image'))

    rerender(
      <TestWrapper>
        <ConverterPanel
          onClose={onClose}
          previewText=""
          activeInputTypes={['text']}
          onUseConvertedValue={jest.fn()}
        />
      </TestWrapper>,
    )
    await waitFor(() => expect(screen.queryByTestId('converter-piece-tabs')).not.toBeInTheDocument())
  })
})

// ─── Converter selection & filtering ─────────────────────────────

describe('ConverterPanel converter selection', () => {
  it('shows converter card when a converter is selected', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')
    expect(screen.getByTestId('converter-item-Base64Converter')).toBeInTheDocument()
  })

  it('filters converters by query string typed into combobox', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()

    const input = getComboboxInput()
    fireEvent.click(input)
    fireEvent.change(input, { target: { value: 'Caesar' } })

    await waitFor(() => {
      expect(screen.getByTestId('converter-option-CaesarConverter')).toBeInTheDocument()
      expect(screen.queryByTestId('converter-option-Base64Converter')).not.toBeInTheDocument()
    })
  })
})

// ─── Parameters ──────────────────────────────────────────────────

describe('ConverterPanel parameters', () => {
  it('renders text input for regular params and shows required validation', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('CaesarConverter')

    expect(screen.getByTestId('converter-params')).toBeInTheDocument()
    const paramEl = screen.getByTestId('param-caesar_offset')
    expect(paramEl).toBeInTheDocument()

    // Find the actual input — may be the element itself or a child
    const paramInput = paramEl.tagName === 'INPUT' ? paramEl : paramEl.querySelector('input')
    if (paramInput) {
      fireEvent.change(paramInput, { target: { value: '3' } })
      fireEvent.change(paramInput, { target: { value: '' } })
    }

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByText('Required')).toBeInTheDocument())
  })

  it('renders bool switch param and toggles', async () => {
    const user = userEvent.setup()
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('BoolConverter')

    const switchEl = screen.getByTestId('param-uppercase')
    expect(switchEl).toBeInTheDocument()
    // Toggle the switch on — use userEvent to properly trigger Fluent UI onChange (line 414)
    await user.click(switchEl)
    await waitFor(() => expect(screen.getByText('True')).toBeInTheDocument())
  })

  it('renders select dropdown for choice params', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('ChoiceConverter')

    const select = screen.getByTestId('param-mode')
    expect(select).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'slow' } })
  })

  it('renders file picker for path params and allows text input', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('FileParamConverter')

    expect(screen.getByTestId('param-template_file_path')).toBeInTheDocument()
    expect(screen.getByTestId('param-template_file_path-browse')).toBeInTheDocument()
    // Type into the file path input to trigger onChange (line 439)
    const paramEl = screen.getByTestId('param-template_file_path')
    const paramInput = paramEl.tagName === 'INPUT' ? paramEl : paramEl.querySelector('input')
    if (paramInput) {
      fireEvent.change(paramInput, { target: { value: '/some/path.txt' } })
    }
  })

  it('collapses/expands params section', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('CaesarConverter')

    expect(screen.getByTestId('converter-params')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('toggle-params-btn'))
    expect(screen.queryByTestId('param-caesar_offset')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('toggle-params-btn'))
    expect(screen.getByTestId('param-caesar_offset')).toBeInTheDocument()
  })
})

// ─── Preview ─────────────────────────────────────────────────────

describe('ConverterPanel preview', () => {
  it('runs preview and shows text output', async () => {
    mockedConvertersApi.createConverter.mockResolvedValue({ converter_id: 'conv-1', converter_type: 'Base64Converter' })
    mockedConvertersApi.previewConversion.mockResolvedValue({ converted_value: 'aGVsbG8=' })

    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByTestId('converter-preview-result')).toHaveTextContent('aGVsbG8='))
    expect(screen.getByTestId('use-converted-btn')).toBeInTheDocument()
  })

  it('shows error when preview fails', async () => {
    mockedConvertersApi.createConverter.mockRejectedValue(new Error('Server error'))

    renderPanel({ previewText: 'hello' })
    await waitForList()
    // Use LLM-based converter to avoid auto-preview consuming the mock
    await openComboboxAndSelect('TranslationConverter')

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByTestId('converter-preview-error')).toBeInTheDocument())
  })

  it('preview button is disabled when previewText is empty', async () => {
    renderPanel({ previewText: '' })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('handlePreview returns early when preview value is empty', async () => {
    renderPanel({ previewText: '  ' })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')
    // Button should be disabled since trimmed text is empty
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('preview button is disabled when no converter is selected', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('blocks preview with missing required params and shows validation', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('CaesarConverter')

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByText('Required')).toBeInTheDocument())
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
  })

  it('shows hint when no preview text on text tab', async () => {
    renderPanel({ previewText: '' })
    await waitForList()
    expect(screen.getByText('Type in the chat input box to preview a conversion.')).toBeInTheDocument()
  })

  it('shows hint when no attachment on non-text tab', async () => {
    renderPanel({ previewText: '', activeInputTypes: ['text', 'image'], attachmentData: {} })
    await waitForList()
    fireEvent.click(screen.getByTestId('converter-tab-image'))
    await waitFor(() =>
      expect(screen.getByText('Attach a image file to preview a conversion.')).toBeInTheDocument()
    )
  })
})

// ─── Use Converted Value ─────────────────────────────────────────

describe('ConverterPanel use converted value', () => {
  it('calls onUseConvertedValue with correct data', async () => {
    const onUseConvertedValue = jest.fn()
    mockedConvertersApi.createConverter.mockResolvedValue({ converter_id: 'conv-1', converter_type: 'Base64Converter' })
    mockedConvertersApi.previewConversion.mockResolvedValue({ converted_value: 'aGVsbG8=' })

    renderPanel({ previewText: 'hello', onUseConvertedValue })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByTestId('converter-preview-result')).toBeInTheDocument())

    fireEvent.click(screen.getByTestId('use-converted-btn'))
    expect(onUseConvertedValue).toHaveBeenCalledWith({
      pieceType: 'text',
      converterInstanceId: 'conv-1',
      convertedValue: 'aGVsbG8=',
      originalValue: 'hello',
    })
  })
})

// ─── Auto-preview ────────────────────────────────────────────────

describe('ConverterPanel auto-preview', () => {
  it('auto-previews for non-LLM converters after delay', async () => {
    jest.useFakeTimers()
    mockedConvertersApi.createConverter.mockResolvedValue({ converter_id: 'conv-auto', converter_type: 'Base64Converter' })
    mockedConvertersApi.previewConversion.mockResolvedValue({ converted_value: 'auto-result' })

    renderPanel({ previewText: 'hello' })
    // flush the initial load
    await act(async () => { jest.advanceTimersByTime(100) })

    fireEvent.click(getComboboxInput())
    await act(async () => { jest.advanceTimersByTime(100) })
    fireEvent.click(screen.getByTestId('converter-option-Base64Converter'))

    // Auto-preview fires after 300ms timer
    await act(async () => { jest.advanceTimersByTime(350) })

    expect(mockedConvertersApi.createConverter).toHaveBeenCalled()
    jest.useRealTimers()
  })

  it('does NOT auto-preview for LLM-based converters', async () => {
    jest.useFakeTimers()
    renderPanel({ previewText: 'hello' })
    await act(async () => { jest.advanceTimersByTime(100) })

    fireEvent.click(getComboboxInput())
    await act(async () => { jest.advanceTimersByTime(100) })
    fireEvent.click(screen.getByTestId('converter-option-TranslationConverter'))
    await act(async () => { jest.advanceTimersByTime(500) })

    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
    jest.useRealTimers()
  })

  it('clears auto-preview timer on unmount', async () => {
    jest.useFakeTimers()
    const { unmount } = renderPanel({ previewText: 'hello' })
    await act(async () => { jest.advanceTimersByTime(100) })

    fireEvent.click(getComboboxInput())
    await act(async () => { jest.advanceTimersByTime(100) })
    fireEvent.click(screen.getByTestId('converter-option-Base64Converter'))

    unmount()
    // Should not throw — timer was cleaned up
    await act(async () => { jest.advanceTimersByTime(500) })
    jest.useRealTimers()
  })
})

// ─── Grouped converters ──────────────────────────────────────────

describe('ConverterPanel grouped converters', () => {
  it('groups converters by output type in the dropdown', async () => {
    renderPanel({ activeInputTypes: ['text', 'image'] })
    await waitForList()

    fireEvent.click(screen.getByTestId('converter-tab-image'))
    await waitFor(() => expect(screen.getByTestId('converter-panel-list')).toBeInTheDocument())

    fireEvent.click(getComboboxInput())
    await waitFor(() =>
      expect(screen.getByTestId('converter-option-ImageCompressor')).toBeInTheDocument()
    )
  })
})

// ─── Preview output types (image, audio, video) ─────────────────

describe('ConverterPanel output rendering', () => {
  async function previewWithOutput(output: string) {
    mockedConvertersApi.createConverter.mockResolvedValue({ converter_id: 'conv-1', converter_type: 'Base64Converter' })
    mockedConvertersApi.previewConversion.mockResolvedValue({ converted_value: output })

    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByTestId('converter-preview-result')).toBeInTheDocument())
  }

  it('renders image output for image file paths', async () => {
    await previewWithOutput('/output/result.png')
    expect((screen.getByTestId('converter-preview-result') as HTMLElement).tagName).toBe('IMG')
  })

  it('renders audio output for audio file paths', async () => {
    await previewWithOutput('/output/result.wav')
    expect((screen.getByTestId('converter-preview-result') as HTMLElement).tagName).toBe('AUDIO')
  })

  it('renders video output for video file paths', async () => {
    await previewWithOutput('/output/result.mp4')
    expect((screen.getByTestId('converter-preview-result') as HTMLElement).tagName).toBe('VIDEO')
  })

  it('renders text output for plain text', async () => {
    await previewWithOutput('plain text output')
    expect((screen.getByTestId('converter-preview-result') as HTMLElement).tagName).toBe('PRE')
  })
})

// ─── Resize handle ───────────────────────────────────────────────

describe('ConverterPanel resize handle', () => {
  it('renders the resize handle', async () => {
    renderPanel()
    await waitForList()
    expect(screen.getByTestId('converter-panel-resize')).toBeInTheDocument()
  })

  it('sets cursor on mousedown and clears on mouseup', async () => {
    renderPanel()
    await waitForList()
    const handle = screen.getByTestId('converter-panel-resize')
    fireEvent.mouseDown(handle)
    expect(document.body.style.cursor).toBe('col-resize')
    // Simulate mousemove during drag
    fireEvent.mouseMove(document, { clientX: 400 })
    fireEvent.mouseUp(document)
    expect(document.body.style.cursor).toBe('')
  })

  it('does not resize when not dragging', async () => {
    renderPanel()
    await waitForList()
    // Mousemove without prior mousedown should be no-op
    fireEvent.mouseMove(document, { clientX: 400 })
    fireEvent.mouseUp(document)
  })
})

// ─── Attachment tab preview ──────────────────────────────────────

describe('ConverterPanel attachment preview', () => {
  it('uses attachment data for preview on non-text tab', async () => {
    mockedConvertersApi.createConverter.mockResolvedValue({ converter_id: 'conv-img', converter_type: 'ImageCompressor' })
    mockedConvertersApi.previewConversion.mockResolvedValue({ converted_value: '/out/compressed.png' })

    renderPanel({
      previewText: '',
      activeInputTypes: ['text', 'image'],
      attachmentData: { image: '/path/to/img.png' },
    })
    await waitForList()

    fireEvent.click(screen.getByTestId('converter-tab-image'))
    await waitFor(() => expect(screen.getByTestId('converter-panel-list')).toBeInTheDocument())

    await openComboboxAndSelect('ImageCompressor')

    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() =>
      expect(mockedConvertersApi.previewConversion).toHaveBeenCalledWith(
        expect.objectContaining({ original_value: '/path/to/img.png', original_value_data_type: 'image_path' })
      )
    )
  })
})

// ─── File picker browse button ───────────────────────────────────

describe('ConverterPanel file picker', () => {
  let createElementSpy: jest.SpyInstance | null = null

  afterEach(() => {
    createElementSpy?.mockRestore()
    createElementSpy = null
  })

  it('opens file dialog on browse click and reads file as data URI', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('FileParamConverter')

    const mockFile = new File(['content'], 'template.txt', { type: 'text/plain' })
    const mockClick = jest.fn()
    const mockInput = {
      type: '',
      onchange: null as (() => void) | null,
      click: mockClick,
      files: [mockFile],
    }
    const origCreateElement = document.createElement.bind(document)
    createElementSpy = jest.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'input') return mockInput as unknown as HTMLElement
      return origCreateElement(tag)
    })

    // Mock FileReader to call onload synchronously with a data URI
    const OrigFileReader = globalThis.FileReader
    const mockDataUri = 'data:text/plain;base64,Y29udGVudA=='
    globalThis.FileReader = class MockFileReader {
      result: string | null = null
      onload: (() => void) | null = null
      readAsDataURL() {
        this.result = mockDataUri
        this.onload?.()
      }
    } as unknown as typeof FileReader

    fireEvent.click(screen.getByTestId('param-template_file_path-browse'))
    expect(mockClick).toHaveBeenCalled()
    expect(mockInput.type).toBe('file')

    // Restore before the state update triggers a re-render
    createElementSpy.mockRestore()
    createElementSpy = null

    act(() => { mockInput.onchange?.() })

    expect(screen.getByTestId('param-template_file_path')).toHaveValue(mockDataUri)

    globalThis.FileReader = OrigFileReader
  })
})

// ─── LLM badge ───────────────────────────────────────────────────

describe('ConverterPanel LLM badge', () => {
  it('shows LLM badge for LLM-based converters in dropdown', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()

    fireEvent.click(getComboboxInput())
    await waitFor(() => expect(screen.getByTestId('converter-option-TranslationConverter')).toBeInTheDocument())
    expect(screen.getByText('LLM')).toBeInTheDocument()
  })
})

// ─── Default output hint ─────────────────────────────────────────

describe('ConverterPanel output hint', () => {
  it('shows default hint when no preview output', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    expect(screen.getByText('Converted output will appear here.')).toBeInTheDocument()
  })
})

// ─── Edge cases ──────────────────────────────────────────────────

describe('ConverterPanel edge cases', () => {
  it('unmount during successful catalog load triggers isMounted guard', async () => {
    let resolveLoad!: (value: ConverterCatalogResponse) => void
    mockedConvertersApi.listConverterCatalog.mockReturnValue(
      new Promise((resolve) => { resolveLoad = resolve })
    )

    const { unmount } = renderPanel()
    expect(screen.getByTestId('converter-panel-loading')).toBeInTheDocument()

    // Unmount before the load resolves
    unmount()

    // Now resolve — the !isMounted guard (line 53) should prevent state updates
    await act(async () => {
      resolveLoad(MOCK_CATALOG)
    })
  })

  it('unmount during failed catalog load triggers isMounted guard', async () => {
    let rejectLoad!: (reason: Error) => void
    mockedConvertersApi.listConverterCatalog.mockReturnValue(
      new Promise((_, reject) => { rejectLoad = reject })
    )

    const { unmount } = renderPanel()
    expect(screen.getByTestId('converter-panel-loading')).toBeInTheDocument()

    // Unmount before the load fails
    unmount()

    // Now reject — the !isMounted guard (line 58) should prevent state updates
    await act(async () => {
      rejectLoad(new Error('fail'))
    })
  })

  it('handlePreview returns early when non-text tab has whitespace-only value', async () => {
    renderPanel({
      previewText: '',
      activeInputTypes: ['text', 'image'],
      attachmentData: { image: '   ' },
    })
    await waitForList()

    fireEvent.click(screen.getByTestId('converter-tab-image'))
    await waitFor(() => expect(screen.getByTestId('converter-panel-list')).toBeInTheDocument())

    await openComboboxAndSelect('ImageCompressor')

    // The button may or may not be disabled for whitespace value —
    // if the click triggers handlePreview, line 143 early return is hit
    const btn = screen.getByTestId('converter-preview-btn')
    fireEvent.click(btn)

    // createConverter should NOT be called since preview value is whitespace-only
    expect(mockedConvertersApi.createConverter).not.toHaveBeenCalled()
  })

  it('handles duplicate input types in activeInputTypes', async () => {
    renderPanel({ activeInputTypes: ['text', 'image', 'image'] })
    await waitForList()
    // Tabs should deduplicate — only text + image
    expect(screen.getByTestId('converter-piece-tabs')).toBeInTheDocument()
    const tabs = screen.getAllByTestId(/^converter-tab-/)
    expect(tabs).toHaveLength(2)
  })

  it('uses fallback label for unknown tab types', async () => {
    renderPanel({ activeInputTypes: ['text', 'custom_type'] })
    await waitForList()
    // Unknown type falls back to the raw type string (line 280)
    expect(screen.getByTestId('converter-tab-custom_type')).toBeInTheDocument()
  })

  it('handles converters with Optional[bool] param type', async () => {
    const catalogWithOptionalBool = {
      items: [
        ...MOCK_CATALOG.items,
        {
          converter_type: 'OptBoolConverter',
          supported_input_types: ['text'],
          supported_output_types: ['text'],
          parameters: [
            { name: 'flag', type_name: 'Optional[bool]', required: false, default_value: 'true', choices: null, description: 'Optional bool.' },
          ],
          is_llm_based: false,
          description: 'Optional bool param test.',
        },
      ],
    }
    mockedConvertersApi.listConverterCatalog.mockResolvedValueOnce(catalogWithOptionalBool as ConverterCatalogResponse)

    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('OptBoolConverter')

    // Should render a Switch (not a text input) for Optional[bool] (line 410-416)
    const switchEl = screen.getByTestId('param-flag')
    expect(switchEl).toBeInTheDocument()
  })

  it('shows validation error styling for required file path params', async () => {
    const catalogWithRequiredFile = {
      items: [
        {
          converter_type: 'RequiredFileConverter',
          supported_input_types: ['text'],
          supported_output_types: ['text'],
          parameters: [
            { name: 'config_file_path', type_name: 'str', required: true, default_value: null, choices: null, description: 'Config file path.' },
          ],
          is_llm_based: true,
          description: 'Required file param test.',
        },
      ],
    }
    mockedConvertersApi.listConverterCatalog.mockResolvedValueOnce(catalogWithRequiredFile as ConverterCatalogResponse)

    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('RequiredFileConverter')

    // Click preview without filling required file param (line 441 - isMissing className)
    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    await waitFor(() => expect(screen.getByText('Required')).toBeInTheDocument())
  })

  it('handles converter with no description', async () => {
    const catalogNoDesc = {
      items: [
        {
          converter_type: 'NoDescConverter',
          supported_input_types: ['text'],
          supported_output_types: ['text'],
          parameters: [],
          is_llm_based: false,
          description: null,
        },
      ],
    }
    mockedConvertersApi.listConverterCatalog.mockResolvedValueOnce(catalogNoDesc as ConverterCatalogResponse)

    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('NoDescConverter')
    // Should render without description text
    expect(screen.getByTestId('converter-item-NoDescConverter')).toBeInTheDocument()
  })

  it('handles converter with no parameters', async () => {
    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('Base64Converter')
    // No params section should be shown
    expect(screen.queryByTestId('converter-params')).not.toBeInTheDocument()
  })

  it('handles converter with empty supported types', async () => {
    const catalogEmptyTypes = {
      items: [
        {
          converter_type: 'EmptyTypesConverter',
          supported_input_types: [],
          supported_output_types: [],
          parameters: [],
          is_llm_based: false,
          description: 'No type restrictions.',
        },
      ],
    }
    mockedConvertersApi.listConverterCatalog.mockResolvedValueOnce(catalogEmptyTypes as ConverterCatalogResponse)

    renderPanel({ previewText: 'hello' })
    await waitForList()
    await openComboboxAndSelect('EmptyTypesConverter')
    expect(screen.getByTestId('converter-item-EmptyTypesConverter')).toBeInTheDocument()
  })
})
