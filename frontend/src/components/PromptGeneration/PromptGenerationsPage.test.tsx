import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { promptGenerationsApi, promptGeneratorsApi } from '@/services/api'
import {
  ASSERT_DESCRIPTOR,
  OBJECTIVE_DESCRIPTOR,
  makeGenerationDetail,
  makeGenerationSummary,
} from '@/test-utils/promptGenerationFixtures'

import PromptGenerationsPage from './PromptGenerationsPage'

jest.mock('@/services/api', () => ({
  promptGeneratorsApi: { list: jest.fn() },
  promptGenerationsApi: { list: jest.fn(), cancel: jest.fn() },
}))

const mockList = jest.mocked(promptGenerationsApi.list)
const mockCancel = jest.mocked(promptGenerationsApi.cancel)
const mockGenerators = jest.mocked(promptGeneratorsApi.list)

function LocationReadout() {
  const location = useLocation()
  return <output aria-label="Current URL">{`${location.pathname}${location.search}`}</output>
}

function renderPage() {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={['/datasets/generate']}>
        <Routes>
          <Route path="/datasets/generate" element={<PromptGenerationsPage />} />
          <Route path="/datasets/generate/new" element={<p>New generation form</p>} />
        </Routes>
        <LocationReadout />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('PromptGenerationsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockList.mockReset()
    mockCancel.mockReset()
    mockGenerators.mockResolvedValue({ items: [ASSERT_DESCRIPTOR, OBJECTIVE_DESCRIPTOR] })
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('lists generations newest first with batch members under their group label', async () => {
    mockList.mockResolvedValue({ items: [
      makeGenerationSummary({
        id: 'suite-a', dataset_name: 'travel_sycophancy', group_id: 'group-1', group_label: 'Travel suite', status: 'completed',
      }),
      makeGenerationSummary({ id: 'solo', dataset_name: 'returns_policy', status: 'failed', error: 'Target refused.' }),
      makeGenerationSummary({
        id: 'suite-b', dataset_name: 'travel_injection', group_id: 'group-1', group_label: 'Travel suite',
        status: 'awaiting_review',
      }),
    ] })
    renderPage()

    const table = await screen.findByRole('table', { name: 'Generations' })
    const group = within(table).getByTestId('generation-group-row-group-1')
    expect(group).toHaveTextContent('Travel suite')
    expect(within(group).queryByRole('link')).not.toBeInTheDocument()
    expect(group).toHaveTextContent('Review needed')
    expect(group).toHaveTextContent('1 of 2 datasets completed')
    const rows = within(table).getAllByRole('row').map((row) => row.getAttribute('data-testid'))
    expect(rows).toEqual([null, 'generation-group-row-group-1', 'generation-row-suite-a', 'generation-row-suite-b', 'generation-row-solo'])
    const solo = within(table).getByTestId('generation-row-solo')
    expect(within(solo).getByRole('link', { name: 'returns_policy' })).toHaveAttribute('href', '/datasets/generate/solo')
    expect(solo).toHaveTextContent('ASSERT policy dataset')
    expect(solo).toHaveTextContent('Failed')
    expect(solo).toHaveTextContent('Target refused.')
    expect(solo).toHaveTextContent('Build the taxonomy · 0 of 25')
    expect(within(solo).queryByRole('button', { name: /Cancel/ })).not.toBeInTheDocument()
    const reviewing = within(table).getByTestId('generation-row-suite-b')
    expect(within(reviewing).getByRole('link', { name: 'travel_injection' })).toHaveAttribute('href', '/datasets/generate/suite-b')
    expect(within(reviewing).getByRole('button', { name: 'Cancel travel_injection' })).toBeEnabled()
    expect(screen.queryByTestId('generation-group-run-scenario-link')).not.toBeInTheDocument()
  })

  it('links a finished batch to its follow-up scenario with every dataset', async () => {
    mockList.mockResolvedValue({ items: [
      makeGenerationSummary({ id: 'a', dataset_name: 'travel_sycophancy', group_id: 'group-1', group_label: 'Travel', status: 'completed' }),
      makeGenerationSummary({ id: 'b', dataset_name: 'travel_injection', group_id: 'group-1', group_label: 'Travel', status: 'completed' }),
      makeGenerationSummary({
        id: 'c', generator: 'target_objective', dataset_name: 'objectives_a', group_id: 'group-2', group_label: 'Objectives',
        status: 'completed',
      }),
    ] })
    renderPage()

    const link = await screen.findByTestId('generation-group-run-scenario-link')
    expect(link).toHaveTextContent('Run foundry.assert_policy with these 2 datasets')
    expect(link).toHaveAttribute(
      'href',
      '/scanner/launch/foundry.assert_policy?dataset=travel_sycophancy&dataset=travel_injection',
    )
    expect(within(screen.getByTestId('generation-group-row-group-2')).queryByRole('link')).not.toBeInTheDocument()
  })

  it('cancels one batch member and refreshes the list', async () => {
    const user = userEvent.setup()
    mockList
      .mockResolvedValueOnce({ items: [makeGenerationSummary({ id: 'a', dataset_name: 'travel_a', group_id: 'g', group_label: 'Travel', status: 'awaiting_review' })] })
      .mockResolvedValue({ items: [makeGenerationSummary({ id: 'a', dataset_name: 'travel_a', group_id: 'g', group_label: 'Travel', status: 'cancelled' })] })
    mockCancel.mockRejectedValueOnce(new Error('Generation is saving')).mockResolvedValue(makeGenerationDetail({ id: 'a', status: 'cancelled' }))
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Cancel travel_a' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not cancel travel_a: Generation is saving')
    expect(mockList).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: 'Cancel travel_a' }))
    await waitFor(() => expect(within(screen.getByTestId('generation-row-a')).getByText('Cancelled')).toBeInTheDocument())
    expect(mockCancel).toHaveBeenCalledWith('a')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel travel_a' })).not.toBeInTheDocument()
  })

  it('starts a new generation with the chosen generator', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ items: [] })
    renderPage()

    expect(await screen.findByTestId('generations-empty')).toHaveTextContent('No generations yet')
    await user.click(screen.getByRole('button', { name: 'New generation' }))
    await user.click(await screen.findByRole('menuitem', { name: 'Objective dataset' }))

    expect(await screen.findByText('New generation form')).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent('/datasets/generate/new?generator=target_objective')
  })

  it('refreshes the list while a generation is active', async () => {
    jest.useFakeTimers()
    mockList
      .mockResolvedValueOnce({ items: [makeGenerationSummary({ status: 'running' })] })
      .mockResolvedValueOnce({ items: [makeGenerationSummary({ status: 'completed' })] })
    renderPage()

    expect(await screen.findByText('Running')).toBeInTheDocument()
    await act(async () => { await jest.advanceTimersByTimeAsync(2_000) })
    expect(await screen.findByText('Completed')).toBeInTheDocument()
    await act(async () => { await jest.advanceTimersByTimeAsync(4_000) })
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('reports load failures with a retry', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockGenerators.mockRejectedValueOnce(new Error('Generators offline'))
    mockList.mockRejectedValue(new Error('List offline'))
    renderPage()

    expect(await screen.findByText(/Generators could not be loaded: Generators offline/)).toBeInTheDocument()
    await act(async () => { await jest.advanceTimersByTimeAsync(2_000) })
    expect(await screen.findByText(/Generations could not be loaded: List offline/)).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('button', { name: 'New generation' })).toBeDisabled()
    mockList.mockResolvedValue({ items: [] })
    const retries = screen.getAllByRole('button', { name: 'Retry' })
    for (const retry of retries) await user.click(retry)
    expect(await screen.findByTestId('generations-empty')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New generation' })).toBeEnabled()
  })
})
