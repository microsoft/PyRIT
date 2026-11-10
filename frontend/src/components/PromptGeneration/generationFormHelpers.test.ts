import { makeTarget } from '@/test-utils/targetFixtures'
import { ASSERT_DESCRIPTOR, makePreflight } from '@/test-utils/promptGenerationFixtures'

import {
  asRecord,
  hasReviewableStage,
  initialTargetSelection,
  parseWholeNumber,
  preflightProblems,
  readBoolean,
  readNumberText,
  readString,
  readStringList,
  targetSelectionErrors,
} from './generationFormHelpers'

describe('generationFormHelpers', () => {
  const gpt = makeTarget({ target_registry_name: 'gpt' })

  it('reports targets that are not registered or fail a role filter', () => {
    const filters = { research: { allows: () => false, reason: 'Needs web search.' } }
    expect(targetSelectionErrors(ASSERT_DESCRIPTOR.target_roles, [gpt], { generation: 'gpt' })).toEqual([])
    expect(targetSelectionErrors(ASSERT_DESCRIPTOR.target_roles, [gpt], { generation: 'gone', research: 'gpt' }, filters))
      .toEqual(['Generation target: "gone" is not a registered target.', 'Research target: Needs web search.'])
  })

  it('selects the seed targets, else the default target for required roles', () => {
    expect(initialTargetSelection(ASSERT_DESCRIPTOR, { research: 'r' }, gpt)).toEqual({ research: 'r' })
    expect(initialTargetSelection(ASSERT_DESCRIPTOR, undefined, gpt)).toEqual({ generation: 'gpt' })
    expect(initialTargetSelection(ASSERT_DESCRIPTOR, undefined, null)).toEqual({})
    expect(hasReviewableStage(ASSERT_DESCRIPTOR)).toBe(true)
  })

  it('reads loosely typed request values', () => {
    expect(asRecord([1])).toEqual({})
    expect(asRecord({ a: 1 })).toEqual({ a: 1 })
    expect(readString(3, 'x')).toBe('x')
    expect(readBoolean('true', false)).toBe(false)
    expect(readStringList(['a', 1, 'b'])).toEqual(['a', 'b'])
    expect(readStringList('a')).toEqual([])
    expect(readNumberText(5, 1)).toBe('5')
    expect(readNumberText(Number.NaN, 1)).toBe('1')
    expect(parseWholeNumber(' 12 ', 1, 20)).toBe(12)
    expect(parseWholeNumber('1.5', 1, 20)).toBeNull()
    expect(parseWholeNumber('', 0, 20)).toBeNull()
    expect(parseWholeNumber('21', 1, 20)).toBeNull()
  })

  it('labels preflight problems by dataset and blocks generating', () => {
    const taken = makePreflight({ dataset_name_available: false, valid: false, errors: ['Missing generation target.'] })
    expect(preflightProblems([taken], ['a'])).toEqual({
      messages: ['A dataset named "a" already exists. Choose another name.', 'Missing generation target.'],
      blocksGenerate: true,
    })
    expect(preflightProblems([makePreflight(), makePreflight({ errors: ['Bad.'], valid: false })], ['a', 'b']))
      .toEqual({ messages: ['b: Bad.'], blocksGenerate: true })
  })
})
