import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { usePieceConverters } from '@/hooks/useChatConverters'
import { convertersApi } from '@/services/api'
import type { ChatConverterController, ConverterInstance, ConverterPipelineStage, SourceInstanceSpec } from '@/types'

import ConverterPanel from './ConverterPanel'

jest.mock('@/services/api', () => ({
  convertersApi: { listConverters: jest.fn(), createConverter: jest.fn(), previewConversion: jest.fn() },
}))
jest.mock('@/components/Registry/CreateConverterDialog', () => ({ __esModule: true, default: () => null }))
let mockGeneration = ''
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: () => ({ ready: true, generation: mockGeneration }) }))

const converter: ConverterInstance = {
  converter_id: 'caesar', reconstructable: true, identifier: {
    class_name: 'CaesarConverter', hash: 'source', supported_input_types: ['text'], supported_output_types: ['text'],
  },
}
const saved: ConverterInstance = {
  ...converter, converter_id: 'saved', identifier: { ...converter.identifier, hash: 'effective' },
}
const source: SourceInstanceSpec = {
  source_name: 'caesar', source_hash: 'source', params: { caesar_offset: 7 }, effective_hash: 'effective',
}
const initialStages: ConverterPipelineStage[] = [
  { id: 'first', converterId: 'caesar' },
  { id: 'second', converterId: 'caesar', temporary: source, identifier: saved.identifier },
]
const inputs = [{ id: 'text', pieceType: 'text', dataType: 'text', name: 'Text', value: 'hello' }]

function Harness({ capture }: { capture?: (controller: ChatConverterController) => void }) {
  const controller = usePieceConverters(inputs, undefined, { initialSnapshot: {
    generation: '', pipelines: { text: initialStages }, workingInputs: {}, stageResults: {}, errors: {}, applied: {},
  } })
  capture?.(controller)
  return <FluentProvider theme={webLightTheme}>
    <ConverterPanel onClose={jest.fn()} controller={controller} />
    <output aria-label="Pipeline">{JSON.stringify(controller.pipelines.text)}</output>
    <output aria-label="Applied conversions">{JSON.stringify(controller.applied)}</output>
  </FluentProvider>
}

describe('ConverterPanel registry save', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGeneration = ''
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [converter] })
    jest.mocked(convertersApi.createConverter).mockResolvedValue(saved)
  })

  async function openSave(user: ReturnType<typeof userEvent.setup>): Promise<void> {
    await user.click(await screen.findByRole('button', { name: 'Settings for converter caesar, stage 2 of 2' }))
    await user.click(screen.getByRole('menuitem', { name: 'Save to registry' }))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
  }

  it('should save one duplicate without losing converted output, manual edits, or provenance', async () => {
    const user = userEvent.setup()
    jest.mocked(convertersApi.previewConversion).mockResolvedValue({
      original_value: 'hello', original_value_data_type: 'text',
      converted_value: 'pmttw', converted_value_data_type: 'text',
      steps: [
        { converter_id: 'caesar', converter_type: 'CaesarConverter', input_value: 'hello', input_data_type: 'text',
          output_value: 'ifmmp', output_data_type: 'text', provenance: 'first-proof' },
        { converter_id: 'caesar', converter_type: 'CaesarConverter', input_value: 'ifmmp', input_data_type: 'text',
          output_value: 'pmttw', output_data_type: 'text', provenance: 'second-proof', source, identifier: saved.identifier },
      ],
    })
    render(<Harness />)
    await user.click(await screen.findByRole('button', { name: 'Convert', exact: true }))
    const output = await screen.findByRole('textbox', { name: 'Stage 2 output - Text' })
    await user.clear(output)
    await user.type(output, 'manual result')
    const applied = {
      text: {
        pieceId: 'text', pieceType: 'text', converterInstanceIds: ['caesar', 'caesar'],
        converterProvenance: ['first-proof', 'second-proof'], originalValue: 'hello',
        convertedValue: 'manual result', convertedDataType: 'text', manuallyEdited: true,
      },
    }
    expect(JSON.parse(screen.getByRole('status', { name: 'Applied conversions' }).textContent ?? '{}')).toEqual(applied)
    await openSave(user)
    await screen.findByRole('button', { name: 'Settings for converter saved' })
    expect(JSON.parse(screen.getByRole('status', { name: 'Pipeline' }).textContent ?? '[]')).toEqual([
      initialStages[0], { id: 'second', converterId: 'saved' },
    ])
    expect(screen.getByRole('textbox', { name: 'Stage 1 output - Text' })).toHaveValue('ifmmp')
    expect(screen.getByRole('textbox', { name: 'Stage 2 output - Text' })).toHaveValue('manual result')
    expect(JSON.parse(screen.getByRole('status', { name: 'Applied conversions' }).textContent ?? '{}')).toEqual(applied)
    expect(convertersApi.previewConversion).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Temporary')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(convertersApi.createConverter).toHaveBeenCalledWith({ name: 'saved', type: 'CaesarConverter', source })
  })

  it.each(['reorder', 'remove', 'reset'])(
    'should not change another stage after a concurrent %s', async (change: string) => {
      const user = userEvent.setup()
      let finish: (value: ConverterInstance) => void = () => { throw new Error('Save has not started') }
      jest.mocked(convertersApi.createConverter).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
      let controller!: ChatConverterController
      render(<Harness capture={(value: ChatConverterController) => { controller = value }} />)
      await openSave(user)
      act(() => {
        controller.setPipeline('text', (stages: ConverterPipelineStage[]) => {
          if (change === 'reorder') return [...stages].reverse()
          if (change === 'remove') return [stages[0]]
          return stages.map((stage: ConverterPipelineStage) => ({ id: stage.id, converterId: stage.converterId }))
        })
      })
      await act(async () => { finish(saved) })
      const expected = change === 'reorder'
        ? [{ id: 'second', converterId: 'saved' }, initialStages[0]]
        : change === 'remove' ? [initialStages[0]]
          : initialStages.map((stage: ConverterPipelineStage) => ({ id: stage.id, converterId: stage.converterId }))
      expect(controller.pipelines.text).toEqual(expected)
    },
  )

  it('should ignore a completed save from an earlier runtime', async () => {
    const user = userEvent.setup()
    let finish: (value: ConverterInstance) => void = () => { throw new Error('Save has not started') }
    jest.mocked(convertersApi.createConverter).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const capture = jest.fn()
    const rendered = render(<Harness capture={capture} />)
    await openSave(user)
    mockGeneration = 'new-runtime'
    rendered.rerender(<Harness capture={capture} />)
    await act(async () => { finish(saved) })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Settings for converter saved' })).not.toBeInTheDocument()
    expect(await screen.findByRole('status', { name: 'Pipeline' })).not.toHaveTextContent('saved')
  })
})
