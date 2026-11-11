import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { promptGenerationsApi, promptGeneratorsApi } from '@/services/api'
import {
  ASSERT_DESCRIPTOR,
  OBJECTIVE_DESCRIPTOR,
  makeAssertReview,
  makeGenerationDetail,
  makeGenerationEstimate,
} from '@/test-utils/promptGenerationFixtures'

import PromptGenerationPage from './PromptGenerationPage'
import { PROMPT_GENERATION_ROUTE, PROMPT_GENERATIONS_PATH } from './promptGenerationRoutes'

jest.mock('@/services/api', () => ({
  promptGeneratorsApi: { list: jest.fn() },
  promptGenerationsApi: {
    get: jest.fn(),
    cancel: jest.fn(),
    retry: jest.fn(),
    remove: jest.fn(),
    continueAfterReview: jest.fn(),
    getReview: jest.fn(),
    saveReview: jest.fn(),
    getEstimate: jest.fn(),
  },
}))

const mockApi = jest.mocked(promptGenerationsApi)
const NOT_FOUND = { isAxiosError: true, response: { status: 404, data: { detail: 'No generation gen-1.' } } }

function LocationReadout() {
  const location = useLocation()
  return <output aria-label="Current URL">{`${location.pathname}${location.search}`}</output>
}

function renderPage(path: string = '/datasets/generate/gen-1') {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path={PROMPT_GENERATIONS_PATH} element={<p>Generation list</p>} />
          <Route path={PROMPT_GENERATION_ROUTE} element={<PromptGenerationPage />} />
        </Routes>
        <LocationReadout />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('PromptGenerationPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    for (const mock of Object.values(mockApi)) mock.mockReset()
    jest.mocked(promptGeneratorsApi.list).mockResolvedValue({ items: [ASSERT_DESCRIPTOR, OBJECTIVE_DESCRIPTOR] })
    mockApi.getEstimate.mockResolvedValue(makeGenerationEstimate())
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('shows stage progress and polls a running generation until it completes', async () => {
    jest.useFakeTimers()
    mockApi.get
      .mockResolvedValueOnce(makeGenerationDetail({
        status: 'running',
        progress: { completed_stages: ['systematization', 'taxonomy', 'dimensions'], current_stage: 'seed_groups', items_completed: 10, items_total: 25 },
      }))
      .mockResolvedValueOnce(makeGenerationDetail({
        status: 'completed',
        progress: { completed_stages: ['systematization', 'taxonomy', 'dimensions', 'seed_groups'], current_stage: null, items_completed: 25, items_total: 25 },
        summary: { group_count: 25, categories: [], edited_stages: [] },
      }))
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'returns_policy' })).toBeInTheDocument()
    expect(screen.getByTestId('generation-status-badge')).toHaveTextContent('Running')
    const current = screen.getByTestId('generation-stage-seed_groups')
    expect(current).toHaveAttribute('aria-current', 'step')
    expect(current).toHaveTextContent('Generate seed groups (in progress)')
    expect(screen.getByTestId('generation-stage-taxonomy')).toHaveTextContent('(done)')
    expect(screen.getByRole('progressbar', { name: 'Generated items' })).toHaveAttribute('aria-valuenow', '10')
    expect(screen.getByText('10 of 25 items generated')).toBeInTheDocument()
    expect(screen.getByTestId('generation-cancel-btn')).toBeInTheDocument()
    expect(screen.queryByTestId('generation-delete-btn')).not.toBeInTheDocument()
    expect(mockApi.get).toHaveBeenCalledWith('gen-1', expect.any(AbortSignal))

    await act(async () => { await jest.advanceTimersByTimeAsync(2_000) })

    expect(await screen.findByTestId('generation-complete')).toBeInTheDocument()
    await act(async () => { await jest.advanceTimersByTimeAsync(6_000) })
    expect(mockApi.get).toHaveBeenCalledTimes(2)
  })

  it('offers the follow-up scenario with the dataset preselected once complete', async () => {
    mockApi.get.mockResolvedValue(makeGenerationDetail({
      status: 'completed',
      completed_at: '2026-11-09T11:00:00Z',
      summary: {
        group_count: 25,
        coverage: { sample_size: 25, unique_assignments: 20, total_levels: 6, covered_levels: 6, total_pairs: 12, covered_pairs: 11 },
        categories: [
          { name: 'policy_question', permissible: true, groups: 13 },
          { name: 'other_customer', permissible: false, groups: 12 },
        ],
        edited_stages: ['taxonomy'],
      },
    }))
    renderPage()

    const panel = await screen.findByTestId('generation-complete')
    expect(within(panel).getByRole('link', { name: /Run foundry\.assert_policy/ }))
      .toHaveAttribute('href', '/scanner/launch/foundry.assert_policy?dataset=returns_policy')
    expect(within(panel).getByRole('link', { name: 'Browse dataset' }))
      .toHaveAttribute('href', '/datasets/detail?selection_key=dataset%3Anamed%3Areturns_policy')
    const summary = within(panel).getByTestId('assert-generation-summary')
    expect(within(summary).getByText('11 of 12')).toBeInTheDocument()
    expect(within(summary).getByText('Reviewed and edited: taxonomy')).toBeInTheDocument()
    const categories = within(summary).getByRole('table', { name: 'Seed groups per category' })
    expect(within(categories).getByText('Impermissible')).toBeInTheDocument()
    expect(screen.getByTestId('generation-edit-link'))
      .toHaveAttribute('href', '/datasets/generate/new?generator=assert&from=gen-1')
    expect(screen.queryByTestId('generation-cancel-btn')).not.toBeInTheDocument()
  })

  it('shows only a browse link and the objective summary when no scenario follows', async () => {
    mockApi.get.mockResolvedValue(makeGenerationDetail({
      generator: 'target_objective',
      status: 'completed',
      summary: { objective_count: 20 },
    }))
    renderPage()

    const panel = await screen.findByTestId('generation-complete')
    expect(within(panel).queryByRole('link', { name: /Run / })).not.toBeInTheDocument()
    expect(within(panel).getByRole('link', { name: 'Browse dataset' })).toBeInTheDocument()
    expect(within(panel).getByTestId('target-objective-summary')).toHaveTextContent('Objectives20')
  })

  it('shows the error and resumes a failed generation from its checkpoint', async () => {
    const user = userEvent.setup()
    mockApi.get
      .mockResolvedValueOnce(makeGenerationDetail({ status: 'failed', error: 'The target refused.' }))
      .mockResolvedValue(makeGenerationDetail({ status: 'queued' }))
    mockApi.retry.mockResolvedValue(makeGenerationDetail({ status: 'queued' }))
    renderPage()

    const error = await screen.findByTestId('generation-error')
    expect(error).toHaveTextContent('Generation failed')
    expect(error).toHaveTextContent('The target refused.')
    await user.click(screen.getByTestId('generation-retry-btn'))

    expect(mockApi.retry).toHaveBeenCalledWith('gen-1')
    await waitFor(() => expect(screen.getByTestId('generation-status-badge')).toHaveTextContent('Queued'))
    expect(screen.queryByTestId('generation-retry-btn')).not.toBeInTheDocument()
  })

  it('cancels after confirmation and reports action errors', async () => {
    const user = userEvent.setup()
    mockApi.get.mockResolvedValueOnce(makeGenerationDetail({ status: 'awaiting_review' }))
      .mockResolvedValue(makeGenerationDetail({ status: 'cancelled' }))
    mockApi.getReview.mockResolvedValue(makeAssertReview())
    mockApi.cancel.mockRejectedValueOnce(new Error('Already finished'))
      .mockResolvedValueOnce(makeGenerationDetail({ status: 'cancelled' }))
    renderPage()

    await user.click(await screen.findByTestId('generation-cancel-btn'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel generation' }))
    expect(await within(dialog).findByText('Already finished')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel generation' }))

    await waitFor(() => expect(screen.getByTestId('generation-status-badge')).toHaveTextContent('Cancelled'))
    expect(mockApi.cancel).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('generation-retry-btn')).toBeInTheDocument()
    expect(screen.queryByTestId('assert-review-editor')).not.toBeInTheDocument()
  })

  it('deletes a finished generation and returns to the list', async () => {
    const user = userEvent.setup()
    mockApi.get.mockResolvedValue(makeGenerationDetail({ status: 'cancelled' }))
    mockApi.remove.mockResolvedValue(undefined)
    renderPage()

    await user.click(await screen.findByTestId('generation-delete-btn'))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('A dataset it already saved stays in memory.')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('Generation list')).toBeInTheDocument()
    expect(mockApi.remove).toHaveBeenCalledWith('gen-1')
  })

  it('shows the ASSERT review editor while paused for review', async () => {
    mockApi.get.mockResolvedValue(makeGenerationDetail({
      status: 'awaiting_review',
      pause_for_review: true,
      progress: { completed_stages: ['systematization', 'taxonomy', 'dimensions'], current_stage: 'seed_groups', items_completed: 0, items_total: 25 },
    }))
    mockApi.getReview.mockResolvedValue(makeAssertReview())
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Review the generated policy' })).toBeInTheDocument()
    expect(await screen.findByDisplayValue('policy_question')).toBeInTheDocument()
    expect(screen.getByTestId('generation-stage-seed_groups')).toHaveTextContent('(next, after review)')
  })

  it('explains a generator this GUI does not support', async () => {
    jest.mocked(promptGeneratorsApi.list).mockResolvedValue({ items: [] })
    mockApi.get.mockResolvedValue(makeGenerationDetail({
      generator: 'custom',
      status: 'completed',
      progress: { completed_stages: ['write'], current_stage: null, items_completed: 4, items_total: 4 },
      summary: { prompt_count: 4 },
    }))
    renderPage()

    expect(await screen.findByTestId('unsupported-generator'))
      .toHaveTextContent('This GUI doesn\'t support the "custom" generator.')
    expect(screen.getByTestId('generation-browse-dataset-link')).toBeInTheDocument()
  })

  it('shows a missing generation and retries the load', async () => {
    const user = userEvent.setup()
    mockApi.get.mockRejectedValueOnce(NOT_FOUND).mockResolvedValue(makeGenerationDetail())
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Generation not found' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'returns_policy' })).toBeInTheDocument()
  })
})
