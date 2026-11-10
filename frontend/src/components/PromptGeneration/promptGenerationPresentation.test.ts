import {
  ASSERT_DESCRIPTOR,
  makeGenerationEstimate,
  makeGenerationSummary,
} from '@/test-utils/promptGenerationFixtures'

import {
  aggregateGenerationStatus,
  combineEstimates,
  countGenerationStatuses,
  datasetNameError,
  datasetNameFromText,
  formatRange,
  generationListRows,
  generationStatusLabel,
  generatorDisplayName,
  isActiveGenerationStatus,
  isCancellableGenerationStatus,
  isRetryableGenerationStatus,
  nextDatasetName,
  progressText,
  stageLabel,
  withIndefiniteArticle,
} from './promptGenerationPresentation'

describe('promptGenerationPresentation', () => {
  it('classifies statuses for polling and actions', () => {
    expect(['queued', 'running', 'saving'].every((status) => isActiveGenerationStatus(status as 'queued'))).toBe(true)
    expect(isActiveGenerationStatus('awaiting_review')).toBe(false)
    expect(isRetryableGenerationStatus('interrupted')).toBe(true)
    expect(isRetryableGenerationStatus('completed')).toBe(false)
    expect(isCancellableGenerationStatus('awaiting_review')).toBe(true)
    expect(isCancellableGenerationStatus('saving')).toBe(false)
    expect(generationStatusLabel('awaiting_review')).toBe('Review needed')
  })

  it('validates and derives dataset names', () => {
    expect(datasetNameError('')).toBe('Enter a dataset name.')
    expect(datasetNameError('_bad')).toMatch(/start with a letter or digit/)
    expect(datasetNameError('good.name-1')).toBeNull()
    expect(datasetNameFromText('  Private order: disclosure!  ')).toBe('Private_order_disclosure_')
    expect(datasetNameFromText('__x')).toBe('x')
    expect(datasetNameFromText('a'.repeat(200))).toHaveLength(128)
    expect(nextDatasetName('returns')).toBe('returns_v2')
    expect(nextDatasetName('returns_v2')).toBe('returns_v3')
    expect(nextDatasetName('returns_v9')).toBe('returns_v10')
    const longest = 'a'.repeat(128)
    expect(nextDatasetName(longest)).toBe(`${'a'.repeat(125)}_v2`)
    expect(nextDatasetName(`${'b'.repeat(125)}_v9`)).toBe(`${'b'.repeat(124)}_v10`)
    for (const name of [longest, `${'b'.repeat(125)}_v9`]) {
      expect(nextDatasetName(name)).toHaveLength(128)
      expect(nextDatasetName(name)).not.toBe(name)
      expect(datasetNameError(nextDatasetName(name))).toBeNull()
    }
  })

  it('formats generator, stage, count, and progress text', () => {
    expect(generatorDisplayName([ASSERT_DESCRIPTOR], 'assert')).toBe('ASSERT policy dataset')
    expect(generatorDisplayName([], 'unknown')).toBe('unknown')
    expect(stageLabel(ASSERT_DESCRIPTOR, 'dimensions')).toBe('Resolve coverage dimensions')
    expect(stageLabel(undefined, 'custom')).toBe('custom')
    expect(withIndefiniteArticle('ASSERT dataset')).toBe('an ASSERT dataset')
    expect(withIndefiniteArticle('Objective set')).toBe('an Objective set')
    expect(withIndefiniteArticle('policy')).toBe('a policy')
    expect(formatRange(2, 2)).toBe('2')
    expect(formatRange(1000, 2500)).toBe('1,000–2,500')
    const progress = { completed_stages: [], current_stage: 'seed_groups', items_completed: 3, items_total: 10 }
    expect(progressText('running', progress, ASSERT_DESCRIPTOR)).toBe('Generate seed groups · 3 of 10')
    expect(progressText('completed', progress, ASSERT_DESCRIPTOR)).toBe('3 of 10')
    expect(progressText('queued', { ...progress, current_stage: null, items_total: 0 }, undefined)).toBe('Queued')
  })

  it('sums estimates for several datasets', () => {
    expect(combineEstimates([])).toBeNull()
    const single = makeGenerationEstimate()
    expect(combineEstimates([single])).toBe(single)
    const combined = combineEstimates([single, makeGenerationEstimate({ exact_calls: true, notes: ['Other note.'] })])
    expect(combined).toEqual(expect.objectContaining({
      exact_calls: false,
      min_calls: 52,
      max_calls: 62,
      low_tokens: 14000,
      high_tokens: 39000,
      notes: ['Web search is not included.', 'Other note.'],
    }))
    expect(combined?.stages).toHaveLength(4)
  })

  it('groups suite members under one row at the newest member', () => {
    const items = [
      makeGenerationSummary({ id: 'a', group_id: 'g1', group_label: 'Travel', status: 'completed' }),
      makeGenerationSummary({ id: 'b' }),
      makeGenerationSummary({ id: 'c', group_id: 'g1', group_label: 'Travel', status: 'awaiting_review' }),
      makeGenerationSummary({ id: 'd', group_id: 'g2', group_label: null, status: 'failed' }),
    ]
    const rows = generationListRows(items)
    expect(rows.map((row) => row.kind === 'group' ? `group:${row.label}:${row.items.length}` : row.generation.id))
      .toEqual(['group:Travel:2', 'b', 'group:g2:1'])
    expect(countGenerationStatuses(items)).toEqual({ total: 4, completed: 1, active: 1, awaitingReview: 1, failed: 1 })
    expect(aggregateGenerationStatus(items)).toBe('awaiting_review')
    expect(aggregateGenerationStatus([items[0]!])).toBe('completed')
    expect(aggregateGenerationStatus([items[3]!])).toBe('failed')
  })

  it('lists batch members in the order they were requested', () => {
    const member = (id: string, createdAt: string) => makeGenerationSummary({
      id, group_id: 'g1', group_label: 'Travel', created_at: createdAt,
    })
    const rows = generationListRows([
      member('third', '2026-11-09T10:00:00.002Z'),
      member('first', '2026-11-09T10:00:00Z'),
      member('second', '2026-11-09T10:00:00.001Z'),
    ])
    const group = rows[0]
    expect(group?.kind === 'group' ? group.items.map((item) => item.id) : []).toEqual(['first', 'second', 'third'])
  })
})
