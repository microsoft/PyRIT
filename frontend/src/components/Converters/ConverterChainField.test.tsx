import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi } from '@/services/api'
import type { ConverterInstance, ConverterSelection, SourceInstanceSpec } from '@/types'

import ConverterChainField from './ConverterChainField'

jest.mock('@/services/api', () => ({ convertersApi: { createConverter: jest.fn() } }))
jest.mock('@/components/Registry/CreateConverterDialog', () => ({ __esModule: true, default: () => null }))
let mockGeneration = ''
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: () => ({ ready: true, generation: mockGeneration }) }))

const converter: ConverterInstance = {
  converter_id: 'caesar', reconstructable: true, identifier: { class_name: 'CaesarConverter', hash: 'source' },
}
const saved: ConverterInstance = { ...converter, converter_id: 'saved', identifier: { ...converter.identifier, hash: 'effective' } }
const source: SourceInstanceSpec = {
  source_name: 'caesar', source_hash: 'source', params: { caesar_offset: 7 }, effective_hash: 'effective',
}

function Harness({ value, disabled = false }: { value?: ConverterSelection[]; disabled?: boolean }) {
  const [externalValue, setExternalValue] = useState(value)
  const [selection, setSelection] = useState<ConverterSelection[]>(value ?? [source])
  const [converters, setConverters] = useState<ConverterInstance[]>([converter])
  if (value !== externalValue) {
    setExternalValue(value)
    setSelection(value ?? [source])
  }
  return <FluentProvider theme={webLightTheme}>
    <ConverterChainField label="Request converters" converters={converters} value={selection}
      onChange={setSelection} onConvertersChanged={setConverters} disabled={disabled} />
  </FluentProvider>
}

describe('ConverterChainField registry save', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGeneration = ''
    jest.mocked(convertersApi.createConverter).mockResolvedValue(saved)
  })

  it('should disable the menu while the chain is disabled', () => {
    render(<Harness disabled />)
    expect(screen.getByRole('button', { name: /Settings for converter/ })).toBeDisabled()
  })

  it('should not replace another stage after the temporary stage is removed', async () => {
    const user = userEvent.setup()
    let finish: (value: ConverterInstance) => void = () => { throw new Error('Save has not started') }
    jest.mocked(convertersApi.createConverter).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const rendered = render(<Harness value={[source, 'caesar']} />)
    await user.click(screen.getByRole('button', { name: /Settings for converter caesar, Request converters stage 1/ }))
    await user.click(screen.getByRole('menuitem', { name: 'Save to registry' }))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    rendered.rerender(<Harness value={['caesar']} />)
    await act(async () => { finish(saved) })
    await screen.findByRole('button', { name: 'Settings for converter caesar, Request converters stage 1' })
    expect(screen.queryByText('Temporary')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Settings for converter saved/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Request converters' }))
    expect(screen.getByRole('option', { name: /^saved/ })).toBeInTheDocument()
  })

  it('should refresh the list but keep the temporary stage after canceling a pending save', async () => {
    const user = userEvent.setup()
    let finish: (value: ConverterInstance) => void = () => { throw new Error('Save has not started') }
    jest.mocked(convertersApi.createConverter).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    render(<Harness />)
    await user.click(screen.getByRole('button', { name: /Settings for converter/ }))
    await user.click(screen.getByRole('menuitem', { name: 'Save to registry' }))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await user.click(await screen.findByRole('button', { name: /Settings for converter/ }))
    await user.click(screen.getByRole('menuitem', { name: 'Save to registry' }))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'new-name')
    await act(async () => { finish(saved) })
    expect(screen.getByRole('textbox', { name: /Registry name/ })).toHaveValue('new-name')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('Temporary')).toBeInTheDocument()
    await user.click(await screen.findByRole('combobox', { name: 'Request converters' }))
    expect(screen.getByRole('option', { name: /^saved/ })).toBeInTheDocument()
  })

  it('should ignore a completed save from an earlier runtime', async () => {
    const user = userEvent.setup()
    let finish: (value: ConverterInstance) => void = () => { throw new Error('Save has not started') }
    jest.mocked(convertersApi.createConverter).mockImplementation(() => new Promise((resolve) => { finish = resolve }))
    const rendered = render(<Harness />)
    await user.click(screen.getByRole('button', { name: /Settings for converter/ }))
    await user.click(screen.getByRole('menuitem', { name: 'Save to registry' }))
    await user.type(screen.getByRole('textbox', { name: /Registry name/ }), 'saved')
    await user.click(screen.getByRole('button', { name: 'Save', exact: true }))
    mockGeneration = 'new-runtime'
    rendered.rerender(<Harness />)
    await act(async () => { finish(saved) })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(await screen.findByRole('combobox', { name: 'Request converters' }))
    expect(screen.queryByRole('option', { name: /^saved/ })).not.toBeInTheDocument()
  })
})
import { useState } from 'react'
