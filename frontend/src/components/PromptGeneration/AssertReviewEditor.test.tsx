import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { promptGenerationsApi } from '@/services/api'
import {
  ASSERT_DESCRIPTOR,
  makeAssertReview,
  makeGenerationDetail,
  makeGenerationEstimate,
} from '@/test-utils/promptGenerationFixtures'
import { replaceText } from '@/test-utils/replaceText'

import AssertReviewEditor from './AssertReviewEditor'

jest.mock('@/services/api', () => ({
  promptGenerationsApi: {
    getReview: jest.fn(),
    saveReview: jest.fn(),
    getEstimate: jest.fn(),
    continueAfterReview: jest.fn(),
  },
}))

const mockApi = jest.mocked(promptGenerationsApi)
const GENERATION = makeGenerationDetail({ status: 'awaiting_review' })

function renderEditor(onGenerationChange: jest.Mock = jest.fn()) {
  render(
    <FluentProvider theme={webLightTheme}>
      <AssertReviewEditor generation={GENERATION} descriptor={ASSERT_DESCRIPTOR} onGenerationChange={onGenerationChange} />
    </FluentProvider>,
  )
  return onGenerationChange
}

describe('AssertReviewEditor', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    for (const mock of Object.values(mockApi)) mock.mockReset()
    mockApi.getReview.mockResolvedValue(makeAssertReview())
    mockApi.getEstimate.mockResolvedValue(makeGenerationEstimate({ exact_calls: true, min_calls: 25, max_calls: 25 }))
  })

  it('shows the research summary, policy, and refined estimate', async () => {
    renderEditor()

    expect(await screen.findByTestId('assert-review-systematization')).toHaveTextContent('Return policy questions.')
    expect(screen.getByText('Behavior: returns')).toBeInTheDocument()
    expect(screen.getByText('2 categories: 1 permissible, 1 impermissible')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Category 2 examples' })).toHaveValue('Where is Bob’s order?')
    expect(screen.getByRole('textbox', { name: 'channel level 2 name' })).toHaveValue('email')
    const estimate = await screen.findByRole('region', { name: 'Remaining work' })
    await waitFor(() => expect(within(estimate).getByTestId('estimate-calls')).toHaveTextContent('25'))
    expect(within(estimate).getByText('Model calls')).toBeInTheDocument()
    expect(mockApi.getReview).toHaveBeenCalledWith('gen-1', expect.any(AbortSignal))
    expect(screen.getByTestId('assert-review-save')).toBeDisabled()
  })

  it('saves edits and refreshes the estimate', async () => {
    const user = userEvent.setup()
    mockApi.saveReview.mockResolvedValue(makeAssertReview())
    renderEditor()

    const name = await screen.findByRole('textbox', { name: 'Category 1 name' })
    await replaceText(user, name, 'window_question')
    await user.click(screen.getByRole('switch', { name: 'Category 2 is permissible' }))
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    await user.click(screen.getByTestId('assert-review-save'))

    await waitFor(() => expect(mockApi.saveReview).toHaveBeenCalledTimes(1))
    const [generationId, payload] = mockApi.saveReview.mock.calls[0]!
    expect(generationId).toBe('gen-1')
    expect(payload).toEqual(expect.objectContaining({
      taxonomy: expect.objectContaining({
        behavior_categories: [
          expect.objectContaining({ name: 'window_question', permissible: true }),
          expect.objectContaining({ name: 'other_customer', permissible: true }),
        ],
      }),
    }))
    expect(await screen.findByText('Changes saved.')).toBeInTheDocument()
    await waitFor(() => expect(mockApi.getEstimate).toHaveBeenCalledTimes(2))
  })

  it('saves unsaved edits before continuing generation', async () => {
    const user = userEvent.setup()
    const continued = makeGenerationDetail({ status: 'queued' })
    mockApi.saveReview.mockResolvedValue(makeAssertReview())
    mockApi.continueAfterReview.mockResolvedValue(continued)
    const onGenerationChange = renderEditor()

    const definition = await screen.findByRole('textbox', { name: 'Behavior definition' })
    await replaceText(user, definition, 'Explaining return policies. Updated.')
    await user.click(screen.getByRole('button', { name: 'Save and continue generation' }))

    await waitFor(() => expect(onGenerationChange).toHaveBeenCalledWith(continued))
    expect(mockApi.saveReview.mock.invocationCallOrder[0]!)
      .toBeLessThan(mockApi.continueAfterReview.mock.invocationCallOrder[0]!)
    expect(mockApi.saveReview.mock.calls[0]![1]).toEqual(expect.objectContaining({
      taxonomy: expect.objectContaining({ behavior: { name: 'returns', definition: 'Explaining return policies. Updated.' } }),
    }))
  })

  it('continues without saving when nothing changed, and reports a failed continue', async () => {
    const user = userEvent.setup()
    mockApi.continueAfterReview.mockRejectedValue(new Error('Generation is busy'))
    const onGenerationChange = renderEditor()

    await user.click(await screen.findByTestId('assert-review-continue'))

    expect(await screen.findByRole('alert')).toHaveTextContent('Generation is busy')
    expect(mockApi.saveReview).not.toHaveBeenCalled()
    expect(onGenerationChange).not.toHaveBeenCalled()
    expect(screen.getByTestId('assert-review-continue')).toBeEnabled()
  })

  it('keeps edits and shows the error when a save fails', async () => {
    const user = userEvent.setup()
    mockApi.saveReview.mockRejectedValue(new Error('Category names must be unique.'))
    const onGenerationChange = renderEditor()

    const name = await screen.findByRole('textbox', { name: 'Category 1 name' })
    await replaceText(user, name, 'policy_question_edited')
    await user.click(screen.getByRole('button', { name: 'Save and continue generation' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Category names must be unique.')
    expect(screen.getByRole('textbox', { name: 'Category 1 name' })).toHaveValue('policy_question_edited')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    expect(mockApi.getReview).toHaveBeenCalledTimes(1)
    expect(mockApi.continueAfterReview).not.toHaveBeenCalled()
    expect(onGenerationChange).not.toHaveBeenCalled()
  })

  it('blocks saving invalid edits and lets items be added and removed', async () => {
    const user = userEvent.setup()
    renderEditor()

    const examples = await screen.findByRole('textbox', { name: 'Category 1 examples' })
    await user.clear(examples)
    expect(await screen.findByTestId('assert-review-errors')).toHaveTextContent('Category "policy_question" needs at least one example.')
    expect(screen.getByTestId('assert-review-save')).toBeDisabled()
    expect(screen.getByTestId('assert-review-continue')).toBeDisabled()
    await replaceText(user, examples, 'Can I return shoes?')
    expect(screen.queryByTestId('assert-review-errors')).not.toBeInTheDocument()

    await user.click(screen.getByTestId('assert-review-add-category'))
    expect(screen.getByRole('textbox', { name: 'Category 3 name' })).toHaveValue('')
    await user.click(screen.getByRole('button', { name: 'Remove category 3' }))
    expect(screen.queryByRole('textbox', { name: 'Category 3 name' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add term' }))
    expect(screen.getByRole('textbox', { name: 'Term 2' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Remove term 2' }))

    expect(screen.getByRole('button', { name: 'Remove channel level 1' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Add level to channel' }))
    await replaceText(user, screen.getByRole('textbox', { name: 'channel level 3 name' }), 'phone')
    await replaceText(user, screen.getByRole('textbox', { name: 'channel level 3 definition' }), 'Phone call.')
    await user.click(screen.getByRole('button', { name: 'Remove channel level 1' }))
    expect(screen.getByRole('textbox', { name: 'channel level 1 name' })).toHaveValue('email')
    expect(screen.getByTestId('assert-review-save')).toBeEnabled()
  })

  it('offers a retry when the review cannot be loaded', async () => {
    const user = userEvent.setup()
    mockApi.getReview.mockRejectedValueOnce(new Error('Review unavailable')).mockResolvedValue(makeAssertReview())
    renderEditor()

    expect(await screen.findByText(/The review could not be loaded: Review unavailable/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('textbox', { name: 'Category 1 name' })).toBeInTheDocument()
  })

  it('explains when the review has no taxonomy', async () => {
    mockApi.getReview.mockResolvedValue(makeAssertReview({ payload: { taxonomy: null, dimensions: [] } }))
    renderEditor()

    expect(await screen.findByText(/The review has no taxonomy to edit/)).toBeInTheDocument()
  })
})
