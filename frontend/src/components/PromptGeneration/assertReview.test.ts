import { makeAssertReview } from '@/test-utils/promptGenerationFixtures'

import {
  newCategoryDraft,
  newReviewLevelDraft,
  newTermDraft,
  parseAssertReviewPayload,
  parseAssertSummary,
  reviewDraftErrors,
  reviewDraftFromPayload,
  reviewPayloadFromDraft,
} from './assertReview'

describe('assertReview', () => {
  it('round-trips a review payload through an editable draft', () => {
    const payload = parseAssertReviewPayload(makeAssertReview().payload)
    expect(payload?.systematization).toEqual({
      scope: 'Return policy questions.', reasoning_summary: 'Customers ask about returns.',
    })
    const draft = reviewDraftFromPayload(payload!)
    expect(reviewDraftErrors(draft)).toEqual([])
    expect(reviewPayloadFromDraft(draft)).toEqual({
      taxonomy: makeAssertReview().payload.taxonomy,
      dimensions: makeAssertReview().payload.dimensions,
    })
  })

  it('returns null without a taxonomy to edit and tolerates missing parts', () => {
    expect(parseAssertReviewPayload({ taxonomy: null, dimensions: [] })).toBeNull()
    expect(parseAssertReviewPayload({ taxonomy: { behavior_categories: [{}] } })).toEqual({
      systematization: null,
      taxonomy: {
        behavior: { name: '', definition: '' },
        definition_of_terms: [],
        behavior_categories: [{ name: '', definition: '', examples: [], permissible: false }],
      },
      dimensions: [],
    })
  })

  it('splits examples by line and reports what the server would reject', () => {
    const draft = reviewDraftFromPayload(parseAssertReviewPayload(makeAssertReview().payload)!)
    const edited = {
      ...draft,
      behaviorDefinition: ' ',
      categories: [
        { ...draft.categories[0]!, examplesText: ' one \n\n two ' },
        { ...draft.categories[0]!, id: 'copy' },
        newCategoryDraft(),
      ],
      terms: [newTermDraft(), { ...draft.terms[0]! }, { ...draft.terms[0]!, id: 'copy' }],
      dimensions: [{ ...draft.dimensions[0]!, levels: [newReviewLevelDraft()] }],
    }
    expect(reviewPayloadFromDraft(edited).taxonomy.behavior_categories[0]?.examples).toEqual(['one', 'two'])
    expect(reviewDraftErrors(edited)).toEqual([
      'The behavior needs a definition.',
      'Category 3 needs a name.',
      'Category 3 needs a definition.',
      'Category 3 needs at least one example.',
      'Category names must be unique; "policy_question" is repeated.',
      'Term 1 needs a term and a definition.',
      'Terms must be unique; "order" is repeated.',
      'Dimension "channel" needs at least 2 levels.',
      'Every level of dimension "channel" needs a name and a definition.',
    ])
    expect(reviewDraftErrors({ ...draft, categories: [] })).toEqual(['Keep at least one behavior category.'])
    const repeatedLevels = {
      ...draft,
      dimensions: [{ ...draft.dimensions[0]!, levels: [draft.dimensions[0]!.levels[0]!, draft.dimensions[0]!.levels[0]!] }],
    }
    expect(reviewDraftErrors(repeatedLevels)).toEqual(['Dimension "channel" repeats the level "chat".'])
  })

  it('reads a completed summary with defaults for missing values', () => {
    expect(parseAssertSummary({
      group_count: 25,
      coverage: { covered_pairs: 10, total_pairs: 12 },
      categories: [{ name: 'a', permissible: true, groups: 5 }, 'bad'],
      edited_stages: ['taxonomy'],
    })).toEqual({
      group_count: 25,
      coverage: {
        sample_size: 0, unique_assignments: 0, total_levels: 0, covered_levels: 0, total_pairs: 12, covered_pairs: 10,
      },
      categories: [{ name: 'a', permissible: true, groups: 5 }, { name: '', permissible: false, groups: 0 }],
      edited_stages: ['taxonomy'],
    })
    expect(parseAssertSummary({}).coverage).toBeNull()
  })
})
