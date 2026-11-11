import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { useRuntime } from '@/hooks/useRuntime'
import { promptGenerationsApi, promptGeneratorsApi } from '@/services/api'
import {
  ASSERT_DESCRIPTOR,
  OBJECTIVE_DESCRIPTOR,
  makeGenerationDetail,
  makePreflight,
} from '@/test-utils/promptGenerationFixtures'
import { replaceText } from '@/test-utils/replaceText'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { PromptGeneratorDescriptor } from '@/types'

import NewPromptGenerationPage from './NewPromptGenerationPage'
import { newPromptGenerationRoutePath } from './promptGenerationRoutes'

jest.mock('@/services/api', () => ({
  promptGeneratorsApi: { list: jest.fn(), listAssertBehaviors: jest.fn(), listAssertSuites: jest.fn() },
  promptGenerationsApi: {
    get: jest.fn(),
    getRequestFromDataset: jest.fn(),
    preflight: jest.fn(),
    create: jest.fn(),
  },
}))

jest.mock('@/hooks/useRuntime', () => ({
  ...jest.requireActual('@/hooks/useRuntime'),
  useRuntime: jest.fn(() => ({ ready: true, state: 'ready', generation: 'first' })),
}))

const mockApi = jest.mocked(promptGenerationsApi)
const NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'Not generated.' } } }
const CUSTOM_DESCRIPTOR: PromptGeneratorDescriptor = {
  name: 'custom',
  display_name: 'Custom prompts',
  description: 'A generator this GUI has no form for.',
  stages: [{ name: 'write', label: 'Write prompts', reviewable: false }],
  target_roles: [{ role: 'generation', label: 'Generation target', description: 'Writes.', required: true }],
  follow_up_scenario: null,
}
const GPT = makeTarget({ target_registry_name: 'gpt' })

function LocationReadout() {
  const location = useLocation()
  return <output aria-label="Current URL">{`${location.pathname}${location.search}`}</output>
}

function pageTree(path: string) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/datasets/generate/new" element={(
            <NewPromptGenerationPage targets={[GPT]} targetsLoading={false} targetsError={null} defaultTarget={GPT} />
          )} />
          <Route path="/datasets/generate/:generationId" element={<p>Generation page</p>} />
        </Routes>
        <LocationReadout />
      </MemoryRouter>
    </FluentProvider>
  )
}

function renderPage(path: string) {
  return render(pageTree(path))
}

describe('NewPromptGenerationPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    for (const mock of Object.values(mockApi)) mock.mockReset()
    jest.mocked(promptGeneratorsApi.list).mockResolvedValue({ items: [ASSERT_DESCRIPTOR, OBJECTIVE_DESCRIPTOR, CUSTOM_DESCRIPTOR] })
    jest.mocked(promptGeneratorsApi.listAssertBehaviors).mockResolvedValue({ items: [] })
    jest.mocked(promptGeneratorsApi.listAssertSuites).mockResolvedValue({ items: [] })
    mockApi.preflight.mockResolvedValue(makePreflight({ estimate: null }))
    mockApi.create.mockResolvedValue(makeGenerationDetail({ id: 'gen-new', status: 'queued' }))
    jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'first' })
  })

  it('should keep unsaved input while generators reload after a runtime change', async () => {
    const user = userEvent.setup()
    const path = '/datasets/generate/new?generator=target_objective'
    const view = renderPage(path)
    await replaceText(user, await screen.findByTestId('objective-instructions'), 'Keep these instructions.')
    let finish: (response: { items: PromptGeneratorDescriptor[] }) => void = () => {}
    jest.mocked(promptGeneratorsApi.list).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
    jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'second' })

    view.rerender(pageTree(path))

    expect(await screen.findByTestId('generation-metadata-refreshing')).toBeInTheDocument()
    expect(screen.getByTestId('objective-instructions')).toHaveValue('Keep these instructions.')
    expect(screen.getByTestId('generate-btn')).toBeDisabled()
    await act(async () => { finish({ items: [ASSERT_DESCRIPTOR, OBJECTIVE_DESCRIPTOR] }) })
    expect(screen.queryByTestId('generation-metadata-refreshing')).not.toBeInTheDocument()
    expect(screen.getByTestId('objective-instructions')).toHaveValue('Keep these instructions.')
  })

  it('lets the user choose a generator', async () => {
    renderPage('/datasets/generate/new')

    const generators = await screen.findByRole('list', { name: 'Generators' })
    const assert = within(generators).getByRole('article', { name: 'ASSERT policy dataset' })
    expect(assert).toHaveTextContent('Research the behavior → Build the taxonomy')
    expect(within(assert).getByRole('link', { name: 'Start ASSERT policy dataset' }))
      .toHaveAttribute('href', '/datasets/generate/new?generator=assert')
  })

  it('generates objectives from instructions', async () => {
    const user = userEvent.setup()
    renderPage('/datasets/generate/new?generator=target_objective')

    expect(await screen.findByRole('heading', { level: 1, name: 'New Objective dataset' })).toBeInTheDocument()
    expect(screen.queryByTestId('generation-pause-for-review')).not.toBeInTheDocument()
    await replaceText(user, screen.getByTestId('objective-instructions'), 'Ways to bypass a refund policy.')
    await user.clear(screen.getByTestId('objective-count'))
    await user.type(screen.getByTestId('objective-count'), '12')
    await replaceText(user, screen.getByTestId('objective-harm-categories'), 'fraud, fraud, abuse')
    await replaceText(user, screen.getByTestId('generation-dataset-name'), 'refund_objectives')
    expect(await screen.findByText('No estimate is available for this request.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('generate-btn')).toBeEnabled())
    await user.click(screen.getByTestId('generate-btn'))

    expect(await screen.findByText('Generation page')).toBeInTheDocument()
    expect(mockApi.create).toHaveBeenCalledWith({
      generator: 'target_objective',
      dataset_name: 'refund_objectives',
      request: { instructions: 'Ways to bypass a refund policy.', count: 12, harm_categories: ['fraud', 'abuse'] },
      targets: { generation: 'gpt' },
      pause_for_review: false,
    })
  })

  it('checks objective inputs before sending them', async () => {
    const user = userEvent.setup()
    renderPage('/datasets/generate/new?generator=target_objective')

    const count = await screen.findByTestId('objective-count')
    await user.clear(count)
    await user.type(count, '5000')
    await replaceText(user, screen.getByTestId('objective-harm-categories'), 'x'.repeat(101))
    await replaceText(user, screen.getByTestId('generation-dataset-name'), '-bad')

    const errors = screen.getByTestId('generation-form-errors')
    expect(errors).toHaveTextContent('Enter instructions for the objectives.')
    expect(errors).toHaveTextContent('Objectives must be a whole number from 1 to 1,000.')
    expect(errors).toHaveTextContent('Each harm category must be at most 100 characters.')
    expect(errors).toHaveTextContent('Dataset names start with a letter or digit')
    expect(mockApi.preflight).not.toHaveBeenCalled()
  })

  it('pre-fills a new generation from an earlier one under a new dataset name', async () => {
    mockApi.get.mockResolvedValue(makeGenerationDetail({
      id: 'gen-1',
      generator: 'target_objective',
      status: 'completed',
      dataset_name: 'refunds',
      request: { instructions: 'Refund abuse.', count: 7, harm_categories: ['fraud'] },
      targets: { generation: 'gpt' },
    }))
    renderPage('/datasets/generate/new?from=gen-1')

    expect(await screen.findByTestId('generation-seed-source')).toHaveTextContent('Pre-filled from the generation of "refunds"')
    expect(screen.getByTestId('objective-instructions')).toHaveValue('Refund abuse.')
    expect(screen.getByTestId('objective-count')).toHaveValue(7)
    expect(screen.getByTestId('objective-harm-categories')).toHaveValue('fraud')
    expect(screen.getByTestId('generation-dataset-name')).toHaveValue('refunds_v2')
    expect(mockApi.get).toHaveBeenCalledWith('gen-1', expect.any(AbortSignal))
  })

  it('pre-fills from the request that generated a stored dataset', async () => {
    mockApi.getRequestFromDataset.mockResolvedValue({
      generator: 'assert',
      request: { behavior_name: 'returns', behavior_description: 'Explain returns.', group_count: 9 },
      source_dataset_name: 'returns_v2',
    })
    renderPage('/datasets/generate/new?generator=assert&selection_key=dataset%3Anamed%3Areturns_v2')

    expect(await screen.findByTestId('assert-behavior-name')).toHaveValue('returns')
    expect(screen.getByTestId('generation-seed-source')).toHaveTextContent('Pre-filled from the policy that generated "returns_v2"')
    expect(screen.getByTestId('generation-dataset-name')).toHaveValue('returns_v3')
    expect(mockApi.getRequestFromDataset).toHaveBeenCalledWith('dataset:named:returns_v2', expect.any(AbortSignal))
  })

  it('looks up the exact dataset a regenerate link names, including surrounding whitespace', async () => {
    mockApi.getRequestFromDataset.mockResolvedValue({
      generator: 'assert',
      request: { behavior_name: 'returns', behavior_description: 'Explain returns.' },
      source_dataset_name: ' returns ',
    })
    renderPage(newPromptGenerationRoutePath({ generator: 'assert', selectionKey: 'dataset:named: returns ' }))

    expect(await screen.findByTestId('assert-behavior-name')).toHaveValue('returns')
    expect(mockApi.getRequestFromDataset).toHaveBeenCalledWith('dataset:named: returns ', expect.any(AbortSignal))
  })

  it('explains a dataset no generator made and retries', async () => {
    const user = userEvent.setup()
    mockApi.getRequestFromDataset.mockRejectedValueOnce(NOT_FOUND).mockResolvedValue({
      generator: 'target_objective', request: { instructions: 'x', count: 1 }, source_dataset_name: 'x',
    })
    renderPage('/datasets/generate/new?selection_key=dataset%3Anamed%3Ax')

    expect(await screen.findByText(/That dataset was not made by a known generator/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByTestId('objective-instructions')).toHaveValue('x')
  })

  it('rejects a generator that does not match the source', async () => {
    mockApi.get.mockResolvedValue(makeGenerationDetail({ generator: 'assert' }))
    renderPage('/datasets/generate/new?generator=target_objective&from=gen-1')

    expect(await screen.findByText('The source was made by the assert generator, not target_objective.')).toBeInTheDocument()
  })

  it('reports an unknown generator and a failed generator list', async () => {
    renderPage('/datasets/generate/new?generator=missing')
    expect(await screen.findByText(/No generator is named "missing"/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Choose a generator' })).toHaveAttribute('href', '/datasets/generate/new')
  })

  it('shows the generator list error with a retry', async () => {
    jest.mocked(promptGeneratorsApi.list).mockRejectedValueOnce(new Error('Offline'))
    renderPage('/datasets/generate/new')

    expect(await screen.findByText(/Generators could not be loaded: Offline/)).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('list', { name: 'Generators' })).toBeInTheDocument()
  })

  it('explains a generator this GUI does not support', async () => {
    renderPage('/datasets/generate/new?generator=custom')

    expect(await screen.findByTestId('unsupported-generator'))
      .toHaveTextContent('This GUI doesn\'t support the "custom" generator. The frontend and backend may be out of date with each other.')
    expect(screen.queryByTestId('generate-btn')).not.toBeInTheDocument()
  })
})
