import type { RegisteredComponentReference, ScorerInstance } from '@/types'

import { resolveChatScorerReference, scorerMetricDescription, scorerMetricLabel } from './scorerPresentation'

const REFERENCE: RegisteredComponentReference = { registry_name: 'registered', identifier_hash: 'full-hash' }
const SCORER: ScorerInstance = {
  scorer_registry_name: 'registered',
  identifier: {
    class_name: 'SubStringScorer', class_module: 'pyrit.score', hash: 'full-hash', scorer_type: 'true_false',
  },
}

describe('resolveChatScorerReference', () => {
  it('keeps eligible registered identities and distinguishes Off from no preference', () => {
    expect(resolveChatScorerReference(REFERENCE, [SCORER])).toEqual(REFERENCE)
    expect(resolveChatScorerReference(null, [SCORER])).toBeNull()
    expect(resolveChatScorerReference(undefined, [SCORER])).toBeUndefined()
  })

  it('does not turn an inherited alias into a registered chat preference by hash alone', () => {
    expect(resolveChatScorerReference({ ...REFERENCE, registry_name: 'Scorer::alias' }, [SCORER])).toBeUndefined()
  })

  it.each([
    { name: 'missing', scorers: [] },
    { name: 'changed', scorers: [{ ...SCORER, identifier: { ...SCORER.identifier, hash: 'changed-hash' } }] },
    { name: 'float-scale', scorers: [{ ...SCORER, identifier: { ...SCORER.identifier, scorer_type: 'float_scale' } }] },
    { name: 'criteria-dependent', scorers: [{ ...SCORER, chat_scoring_disabled_reason: 'Requires tool criteria.' }] },
  ])('ignores $name saved scorer references', ({ scorers }: { scorers: ScorerInstance[] }) => {
    expect(resolveChatScorerReference(REFERENCE, scorers)).toBeUndefined()
  })
})

describe('Krippendorff alpha labels', () => {
  it('names the combined alpha after what it pools and labels the gold-vs-model alpha', () => {
    expect(scorerMetricLabel('krippendorff_alpha_combined')).toBe('Combined alpha (humans + trials)')
    expect(scorerMetricDescription('krippendorff_alpha_combined')).toContain('rises with the number of trials')
    expect(scorerMetricLabel('krippendorff_alpha_gold_model')).toBe('Gold vs model alpha')
    expect(scorerMetricDescription('krippendorff_alpha_gold_model')).toContain('does not depend on the number of trials')
  })
})
