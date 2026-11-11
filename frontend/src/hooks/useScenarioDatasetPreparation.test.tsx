import { StrictMode, useState } from 'react'

import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { scenariosApi } from '@/services/api'
import type { DatasetInfo, ScenarioDatasetLoadResponse } from '@/types'

import { useScenarioDatasetPreparation } from './useScenarioDatasetPreparation'

jest.mock('@/services/api', () => ({ scenariosApi: { loadDatasets: jest.fn() } }))
let generation = 'first'
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready: true, generation }) }))
const load = jest.mocked(scenariosApi.loadDatasets)
const prepared = jest.fn()
const MISSING: DatasetInfo = {
  name: 'selected', selection_key: 'dataset:named:selected', loaded: false, provider_available: true,
  logical_examples: null, origins: [], modalities: [], harm_categories: [], has_unlabeled_harm_categories: false,
}
const RESPONSE: ScenarioDatasetLoadResponse = {
  settings: { sources: [], max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {} },
  items: [{ ...MISSING, loaded: true }],
}

function Harness({ names = ['selected'], stored = false }: { names?: string[]; stored?: boolean }) {
  const [limit, setLimit] = useState('5')
  const result = useScenarioDatasetPreparation({
    scenarioName: 'test', datasetNames: names,
    request: { datasets: { sources: names.map((name) => ({ name })), max_total: Number(limit) } },
    metadata: [{ ...MISSING, loaded: stored }], metadataLoading: false, metadataError: null,
    enabled: true, onPrepared: prepared,
  })
  return <>
    <input aria-label="Limit" value={limit} onChange={(event) => setLimit(event.target.value)} />
    <span role="status">{result.preparing ? 'Preparing' : result.error ?? 'Ready'}</span>
    <button onClick={result.retry}>Retry loading datasets</button>
  </>
}

describe('useScenarioDatasetPreparation', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    load.mockReset()
    generation = 'first'
  })

  it('starts once under StrictMode, keeps edits usable, and refreshes metadata without refetching', async () => {
    const user = userEvent.setup()
    let finish: (value: ScenarioDatasetLoadResponse) => void = () => {}
    load.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    render(<StrictMode><Harness /></StrictMode>)
    expect(screen.getByRole('status')).toHaveTextContent('Preparing')
    await user.clear(screen.getByRole('textbox', { name: 'Limit' }))
    await user.type(screen.getByRole('textbox', { name: 'Limit' }), '12')
    expect(load).toHaveBeenCalledTimes(1)
    await act(async () => { finish(RESPONSE) })
    expect(screen.getByRole('status')).toHaveTextContent('Ready')
    expect(screen.getByRole('textbox')).toHaveValue('12')
    expect(prepared).toHaveBeenCalledTimes(1)
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('makes no preparation request for known stored selections', () => {
    render(<Harness stored />)
    expect(screen.getByRole('status')).toHaveTextContent('Ready')
    expect(load).not.toHaveBeenCalled()
  })

  it('retains failures through edits and retries only on explicit action', async () => {
    const user = userEvent.setup()
    load.mockRejectedValueOnce(new Error('Provider failed')).mockResolvedValueOnce(RESPONSE)
    render(<Harness />)
    await screen.findByText('Provider failed')
    await user.type(screen.getByRole('textbox', { name: 'Limit' }), '1')
    expect(load).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: 'Retry loading datasets' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Ready'))
    expect(load).toHaveBeenLastCalledWith('test', expect.any(Object), true)
  })

  it('ignores stale selection and runtime completions', async () => {
    let finishOld: (value: ScenarioDatasetLoadResponse) => void = () => {}
    load.mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve }))
    load.mockResolvedValue(RESPONSE)
    const view = render(<Harness names={['old']} />)
    generation = 'second'
    view.rerender(<Harness />)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Ready'))
    expect(prepared).toHaveBeenCalledTimes(1)
    await act(async () => { finishOld(RESPONSE) })
    expect(prepared).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('Ready')
  })
})
