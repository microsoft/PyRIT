import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { promptGenerationsApi } from '@/services/api'

import RegenerateDatasetLink from './RegenerateDatasetLink'

jest.mock('@/services/api', () => ({
  promptGenerationsApi: { getRequestFromDataset: jest.fn() },
}))

const mockLookup = jest.mocked(promptGenerationsApi.getRequestFromDataset)

function renderLink(selectionKey: string) {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter><RegenerateDatasetLink selectionKey={selectionKey} /></MemoryRouter>
    </FluentProvider>,
  )
}

describe('RegenerateDatasetLink', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockLookup.mockReset()
  })

  it('links to the generator that made a named dataset', async () => {
    mockLookup.mockResolvedValue({ generator: 'target_objective', request: {}, source_dataset_name: 'refunds' })
    renderLink('dataset:named:refunds')

    expect(await screen.findByRole('link', { name: /Edit policy & regenerate/ })).toHaveAttribute(
      'href', '/datasets/generate/new?generator=target_objective&selection_key=dataset%3Anamed%3Arefunds',
    )
  })

  it('does not look up the unnamed dataset', () => {
    renderLink('dataset:unnamed')

    expect(mockLookup).not.toHaveBeenCalled()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('shows nothing for a dataset no generator made', async () => {
    mockLookup.mockRejectedValue({ isAxiosError: true, response: { status: 404, data: { detail: 'Not generated' } } })
    renderLink('dataset:named:harmbench')

    await waitFor(() => expect(mockLookup).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(screen.queryByTestId('regenerate-dataset-unavailable')).not.toBeInTheDocument()
  })

  it('reports a failed check and checks again instead of hiding the link', async () => {
    const user = userEvent.setup()
    mockLookup
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 503, data: { detail: 'Backend restarting' } } })
      .mockResolvedValueOnce({ generator: 'assert', request: {}, source_dataset_name: 'returns' })
    renderLink('dataset:named:returns')

    const unavailable = await screen.findByTestId('regenerate-dataset-unavailable')
    expect(unavailable).toHaveTextContent('Backend restarting')
    await user.click(screen.getByRole('button', { name: 'Check again' }))

    expect(await screen.findByRole('link', { name: /Edit policy & regenerate/ })).toBeInTheDocument()
    expect(mockLookup).toHaveBeenCalledTimes(2)
  })
})
