import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import ConverterPreview, { ConverterPreviewProps } from './ConverterPreview'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function renderPreview(overrides: Partial<ConverterPreviewProps> = {}) {
  const defaults: ConverterPreviewProps = {
    activeTab: 'text',
    previewText: '',
    attachmentData: {},
    selectedConverterType: '',
    isPreviewing: false,
    previewError: null,
    previewOutput: '',
    previewConverterInstanceId: null,
    onPreview: jest.fn(),
    onUseConvertedValue: jest.fn(),
  }
  return render(
    <TestWrapper>
      <ConverterPreview {...defaults} {...overrides} />
    </TestWrapper>,
  )
}

// ─── Preview button state ─────────────────────────────────────────

describe('Preview button', () => {
  it('is disabled when no text is entered and tab is text', () => {
    renderPreview({ previewText: '', selectedConverterType: 'Base64Converter' })
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('is disabled when no converter type is selected', () => {
    renderPreview({ previewText: 'hello', selectedConverterType: '' })
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('is enabled when text is entered and converter type is selected', () => {
    renderPreview({ previewText: 'hello', selectedConverterType: 'Base64Converter' })
    expect(screen.getByTestId('converter-preview-btn')).not.toBeDisabled()
  })

  it('is disabled while previewing', () => {
    renderPreview({ previewText: 'hello', selectedConverterType: 'Base64Converter', isPreviewing: true })
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('shows "Converting..." text while previewing', () => {
    renderPreview({ previewText: 'hello', selectedConverterType: 'Base64Converter', isPreviewing: true })
    expect(screen.getByTestId('converter-preview-btn')).toHaveTextContent('Converting...')
  })

  it('shows "Preview" text when not previewing', () => {
    renderPreview({ previewText: 'hello', selectedConverterType: 'Base64Converter' })
    expect(screen.getByTestId('converter-preview-btn')).toHaveTextContent('Preview')
  })

  it('calls onPreview when clicked', () => {
    const onPreview = jest.fn()
    renderPreview({ previewText: 'hello', selectedConverterType: 'Base64Converter', onPreview })
    fireEvent.click(screen.getByTestId('converter-preview-btn'))
    expect(onPreview).toHaveBeenCalledTimes(1)
  })
})

// ─── Media tab button state ──────────────────────────────────────

describe('Preview button with media tabs', () => {
  it('is disabled on image tab when no attachment data exists', () => {
    renderPreview({ activeTab: 'image', attachmentData: {}, selectedConverterType: 'ImageCompressor' })
    expect(screen.getByTestId('converter-preview-btn')).toBeDisabled()
  })

  it('is enabled on image tab when attachment data exists', () => {
    renderPreview({ activeTab: 'image', attachmentData: { image: 'data:image/png;base64,abc' }, selectedConverterType: 'ImageCompressor' })
    expect(screen.getByTestId('converter-preview-btn')).not.toBeDisabled()
  })
})

// ─── Hint text ───────────────────────────────────────────────────

describe('Hint text', () => {
  it('shows text input hint when text tab is active and no text entered', () => {
    renderPreview({ activeTab: 'text', previewText: '' })
    expect(screen.getByText('Type in the chat input box to preview a conversion.')).toBeInTheDocument()
  })

  it('does not show text hint when text is entered', () => {
    renderPreview({ activeTab: 'text', previewText: 'hello' })
    expect(screen.queryByText('Type in the chat input box to preview a conversion.')).not.toBeInTheDocument()
  })

  it('shows attachment hint when media tab has no data', () => {
    renderPreview({ activeTab: 'image', attachmentData: {} })
    expect(screen.getByText('Attach a image file to preview a conversion.')).toBeInTheDocument()
  })

  it('does not show attachment hint when media tab has data', () => {
    renderPreview({ activeTab: 'image', attachmentData: { image: 'data:...' } })
    expect(screen.queryByText('Attach a image file to preview a conversion.')).not.toBeInTheDocument()
  })

  it('shows default output hint when no preview output exists', () => {
    renderPreview()
    expect(screen.getByText('Converted output will appear here.')).toBeInTheDocument()
  })
})

// ─── Error display ───────────────────────────────────────────────

describe('Error display', () => {
  it('shows error message when previewError is set', () => {
    renderPreview({ previewError: 'Something went wrong' })
    expect(screen.getByTestId('converter-preview-error')).toBeInTheDocument()
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('does not show error when previewError is null', () => {
    renderPreview({ previewError: null })
    expect(screen.queryByTestId('converter-preview-error')).not.toBeInTheDocument()
  })
})

// ─── Output rendering ────────────────────────────────────────────

describe('Output rendering', () => {
  it('renders text output in a pre element', () => {
    renderPreview({ previewOutput: 'aGVsbG8=' })
    const result = screen.getByTestId('converter-preview-result')
    expect(result.tagName).toBe('PRE')
    expect(result).toHaveTextContent('aGVsbG8=')
  })

  it('renders image output as img element for .png file', () => {
    renderPreview({ previewOutput: '/path/to/output.png' })
    const result = screen.getByTestId('converter-preview-result')
    expect(result.tagName).toBe('IMG')
    expect(result).toHaveAttribute('src', '/api/media?path=%2Fpath%2Fto%2Foutput.png')
  })

  it('renders image output for .jpg extension', () => {
    renderPreview({ previewOutput: '/path/to/output.jpg' })
    const result = screen.getByTestId('converter-preview-result')
    expect(result.tagName).toBe('IMG')
  })

  it('renders audio output as audio element for .wav file', () => {
    renderPreview({ previewOutput: '/path/to/output.wav' })
    const result = screen.getByTestId('converter-preview-result')
    expect(result.tagName).toBe('AUDIO')
    expect(result).toHaveAttribute('src', '/api/media?path=%2Fpath%2Fto%2Foutput.wav')
  })

  it('renders video output as video element for .mp4 file', () => {
    renderPreview({ previewOutput: '/path/to/output.mp4' })
    const result = screen.getByTestId('converter-preview-result')
    expect(result.tagName).toBe('VIDEO')
    expect(result).toHaveAttribute('src', '/api/media?path=%2Fpath%2Fto%2Foutput.mp4')
  })
})

// ─── Use Converted Value button ─────────────────────────────────

describe('Use Converted Value button', () => {
  it('is not rendered when there is no preview output', () => {
    renderPreview({ previewOutput: '', previewConverterInstanceId: 'conv-1' })
    expect(screen.queryByTestId('use-converted-btn')).not.toBeInTheDocument()
  })

  it('is not rendered when there is no converter instance id', () => {
    renderPreview({ previewOutput: 'result', previewConverterInstanceId: null })
    expect(screen.queryByTestId('use-converted-btn')).not.toBeInTheDocument()
  })

  it('is rendered when output and instance id are both present', () => {
    renderPreview({ previewOutput: 'result', previewConverterInstanceId: 'conv-1' })
    expect(screen.getByTestId('use-converted-btn')).toBeInTheDocument()
  })

  it('calls onUseConvertedValue with correct PieceConversion for text tab', () => {
    const onUseConvertedValue = jest.fn()
    renderPreview({
      activeTab: 'text',
      previewText: 'hello',
      previewOutput: 'aGVsbG8=',
      previewConverterInstanceId: 'conv-1',
      onUseConvertedValue,
    })
    fireEvent.click(screen.getByTestId('use-converted-btn'))
    expect(onUseConvertedValue).toHaveBeenCalledWith({
      pieceType: 'text',
      converterInstanceId: 'conv-1',
      convertedValue: 'aGVsbG8=',
      originalValue: 'hello',
    })
  })

  it('calls onUseConvertedValue with attachment data for image tab', () => {
    const onUseConvertedValue = jest.fn()
    renderPreview({
      activeTab: 'image',
      previewText: '',
      attachmentData: { image: 'data:image/png;base64,abc' },
      previewOutput: '/path/to/output.png',
      previewConverterInstanceId: 'conv-2',
      onUseConvertedValue,
    })
    fireEvent.click(screen.getByTestId('use-converted-btn'))
    expect(onUseConvertedValue).toHaveBeenCalledWith({
      pieceType: 'image',
      converterInstanceId: 'conv-2',
      convertedValue: '/path/to/output.png',
      originalValue: 'data:image/png;base64,abc',
    })
  })

  it('uses empty string for originalValue when no attachment data for tab', () => {
    const onUseConvertedValue = jest.fn()
    renderPreview({
      activeTab: 'audio',
      previewText: '',
      attachmentData: {},
      previewOutput: '/path/to/output.wav',
      previewConverterInstanceId: 'conv-3',
      onUseConvertedValue,
    })
    fireEvent.click(screen.getByTestId('use-converted-btn'))
    expect(onUseConvertedValue).toHaveBeenCalledWith(
      expect.objectContaining({ originalValue: '' }),
    )
  })
})
