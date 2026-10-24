import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { techniquesApi } from '@/services/api'
import type { TechniqueConfiguration } from '@/types'

import { useRunTechniqueEditor } from './useRunTechniqueEditor'

jest.mock('@/services/api', () => ({ techniquesApi: { getConfiguration: jest.fn() } }))
const api = jest.mocked(techniquesApi)

function configuration(name: string): TechniqueConfiguration {
  return {
    source_name: name, source_identity: name, attack_type: 'prompt_sending',
    settings: { description: name }, parameters: [], editable_fields: ['description'],
    supplied_parameters: [], read_only: {},
    supports_prepended_conversation: true,
  }
}

function EditorConsumer({ source, generation = 'first' }: { source: string; generation?: string }) {
  const editor = useRunTechniqueEditor(source, generation, true)
  return <>
    <span>{editor.original?.source_name ?? 'unresolved'}</span>
    {editor.error && <div role="alert">{editor.error}</div>}
    {editor.edited && <span>Edited</span>}
    <button disabled={editor.unresolved} onClick={() => editor.apply({
      configuration: configuration(source),
      override: { source_name: source, source_identity: source, changes: { description: 'edited' }, dependencies: {} },
    })}>Apply</button>
    <button onClick={editor.retry}>Retry</button>
  </>
}

describe('useRunTechniqueEditor', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    api.getConfiguration.mockReset()
  })

  it('should ignore obsolete loads after selection changes', async () => {
    let resolveFirst: (value: TechniqueConfiguration) => void = () => { throw new Error('Not initialized') }
    api.getConfiguration.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(configuration('second'))
    const view = render(<EditorConsumer source="first" />)
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    view.rerender(<EditorConsumer source="second" />)
    await screen.findByText('second')
    await act(async () => { resolveFirst(configuration('first')) })
    expect(screen.queryByText('first')).not.toBeInTheDocument()
    expect(screen.getByText('second')).toBeInTheDocument()
  })

  it('should drop applied edits on runtime change and reload the selected source', async () => {
    const user = userEvent.setup()
    api.getConfiguration.mockResolvedValue(configuration('first'))
    const view = render(<EditorConsumer source="first" />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Apply' }))
    expect(screen.getByText('Edited')).toBeInTheDocument()
    view.rerender(<EditorConsumer source="first" generation="second-runtime" />)
    await waitFor(() => expect(api.getConfiguration).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('Edited')).not.toBeInTheDocument()
  })

  it('should block unresolved settings and support an explicit retry', async () => {
    const user = userEvent.setup()
    api.getConfiguration.mockRejectedValueOnce(new Error('Settings unavailable'))
      .mockResolvedValueOnce(configuration('first'))
    render(<EditorConsumer source="first" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Settings unavailable')
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
