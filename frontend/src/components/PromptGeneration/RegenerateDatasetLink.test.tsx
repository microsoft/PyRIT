import { render, screen, waitFor } from '@testing-library/react'
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
    mockLookup.mockRejectedValue(new Error('Not found'))
    renderLink('dataset:named:harmbench')

    await waitFor(() => expect(mockLookup).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
