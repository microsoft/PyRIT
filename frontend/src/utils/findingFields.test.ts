import { CONTOSO_FINDING_FIELDS, contosoField, DEFAULT_FINDING_FIELDS_FIXTURE } from '@/test-utils/findingFieldsFixtures'
import type { FindingFieldOption, FindingFields } from '@/types'

import {
  choiceLevels,
  describeFindingValues,
  fieldKeys,
  firstMissingRequired,
  FLUENT_BADGE_COLOR,
  type FindingValueRow,
  levelOptions,
  severityBadge,
  severityCountBadges,
} from './findingFields'

function optionKeys(options: FindingFieldOption[]): string[] {
  return options.map((option: FindingFieldOption) => option.key)
}

// Field and level keys such as `constructor` pass the backend's key pattern, and option keys can be any text.
const PROTOTYPE_KEY_FIELDS: FindingFields = {
  severity: {
    ...DEFAULT_FINDING_FIELDS_FIXTURE.severity,
    options: [...DEFAULT_FINDING_FIELDS_FIXTURE.severity.options, { key: 'toString', label: 'To string', children: [] }],
  },
  fields: [{ ...contosoField('category'), key: 'constructor', label: 'Constructor' }],
}

describe('findingFields', () => {
  it('maps every author color to one Fluent badge color', () => {
    expect(FLUENT_BADGE_COLOR).toEqual({ red: 'danger', orange: 'severe', yellow: 'warning', green: 'success',
      blue: 'brand', gray: 'informative', dark: 'important', neutral: 'subtle' })
  })

  it('gives the default severities their badge colors', () => {
    expect(['critical', 'important', 'moderate', 'low', 'informational']
      .map((key: string) => severityBadge(DEFAULT_FINDING_FIELDS_FIXTURE, key).color))
      .toEqual(['danger', 'warning', 'warning', 'informative', 'subtle'])
  })

  it('shows an unmatched severity as typed on a neutral badge', () => {
    expect(severityBadge(CONTOSO_FINDING_FIELDS, 'Sev 2.5')).toEqual({ label: 'Sev 2.5', color: 'subtle' })
    expect(severityBadge(CONTOSO_FINDING_FIELDS, 'sev1')).toEqual({ label: 'Sev 1', color: 'danger' })
  })

  it('lists a field with its levels and every key except severity', () => {
    expect(choiceLevels(contosoField('category'))).toEqual([
      { key: 'category', label: 'Category' }, { key: 'subcategory', label: 'Subcategory' }])
    expect(choiceLevels(contosoField('steps'))).toEqual([{ key: 'steps', label: 'Steps to reproduce' }])
    expect([...fieldKeys(CONTOSO_FINDING_FIELDS)]).toEqual(
      ['severity_rationale', 'category', 'subcategory', 'technique', 'steps', 'owner'])
  })

  it('describes filled values in configured order and lists retired keys', () => {
    const { rows, retired } = describeFindingValues(CONTOSO_FINDING_FIELDS, 'sev1', {
      owner: 'dana', category: 'model_security', subcategory: 'system_prompt_leak',
      technique: 'Many-shot priming', description: 'Old text',
    })
    expect(rows.map((row: FindingValueRow) => `${row.label}: ${row.value}`)).toEqual([
      'Category: Model security', 'Subcategory: System prompt leak', 'Technique: Many-shot priming', 'Owner: dana'])
    expect(retired).toEqual([{ key: 'description', value: 'Old text' }])
  })

  it('shows a cascade child that is not under its stored parent as typed', () => {
    const { rows } = describeFindingValues(CONTOSO_FINDING_FIELDS, 'sev1', {
      category: 'model_security', subcategory: 'hate_speech',
    })
    expect(rows).toEqual([
      { key: 'category', label: 'Category', value: 'Model security', multiline: false },
      { key: 'subcategory', label: 'Subcategory', value: 'hate_speech', multiline: false },
    ])
  })

  it('labels a severity level from the given severity and skips blank values', () => {
    const { rows, retired } = describeFindingValues(CONTOSO_FINDING_FIELDS, 'sev1', {
      severity_rationale: 'customer_impact', owner: ' ', steps: 'Send the Contoso prompt twice.',
    })
    expect(rows).toEqual([
      { key: 'severity_rationale', label: 'Severity rationale', value: 'Customer impact', multiline: false },
      { key: 'steps', label: 'Steps to reproduce', value: 'Send the Contoso prompt twice.', multiline: true },
    ])
    expect(retired).toEqual([])
    expect(describeFindingValues(CONTOSO_FINDING_FIELDS, 'sev2', { severity_rationale: 'customer_impact' }).rows)
      .toEqual([{ key: 'severity_rationale', label: 'Severity rationale', value: 'customer_impact', multiline: false }])
  })

  it('treats a stored severity key as retired and resolves levels under the given severity', () => {
    const { rows, retired } = describeFindingValues(CONTOSO_FINDING_FIELDS, 'sev1', {
      severity: 'sev2', severity_rationale: 'customer_impact',
    })
    expect(rows).toEqual([
      { key: 'severity_rationale', label: 'Severity rationale', value: 'Customer impact', multiline: false },
    ])
    expect(retired).toEqual([{ key: 'severity', value: 'sev2' }])
  })

  it('offers each cascade level the options under the chosen parent', () => {
    const category = contosoField('category')
    expect(optionKeys(levelOptions(category, {}, 0))).toEqual(['model_security', 'content_safety'])
    expect(optionKeys(levelOptions(category, { category: 'model_security' }, 1)))
      .toEqual(['system_prompt_leak', 'tool_misuse'])
    expect(optionKeys(levelOptions(category, { category: 'content_safety' }, 1))).toEqual(['hate_speech', 'self_harm'])
    expect(levelOptions(category, {}, 1)).toEqual([])
    expect(levelOptions(category, { category: 'Prompt injection' }, 1)).toEqual([])
  })

  it('names the first missing required value', () => {
    const missingSteps = {
      severity_rationale: 'customer_impact', category: 'model_security', subcategory: 'tool_misuse', steps: ' ',
    }
    expect(firstMissingRequired(CONTOSO_FINDING_FIELDS, 'sev1', missingSteps)).toBe('Steps to reproduce')
    const missingSubcategory = {
      ...missingSteps, category: 'content_safety', subcategory: '', steps: 'Send the Contoso prompt twice.',
    }
    expect(firstMissingRequired(CONTOSO_FINDING_FIELDS, 'sev1', missingSubcategory)).toBe('Subcategory')
    expect(firstMissingRequired(CONTOSO_FINDING_FIELDS, 'sev1', { ...missingSubcategory, subcategory: 'self_harm' }))
      .toBeNull()
  })

  it('always requires the severity and each of its levels that offers options', () => {
    const complete = {
      severity_rationale: 'customer_impact', category: 'model_security', subcategory: 'tool_misuse',
      steps: 'Send the Contoso prompt twice.',
    }
    expect(firstMissingRequired(CONTOSO_FINDING_FIELDS, ' ', complete)).toBe('Severity')
    expect(firstMissingRequired(CONTOSO_FINDING_FIELDS, 'sev1', { ...complete, severity_rationale: '' }))
      .toBe('Severity rationale')
    expect(firstMissingRequired(CONTOSO_FINDING_FIELDS, 'Sev 2.5', { ...complete, severity_rationale: '' })).toBeNull()
    expect(firstMissingRequired(DEFAULT_FINDING_FIELDS_FIXTURE, 'moderate', {})).toBeNull()
  })

  it('groups unmatched severity counts into one Other badge with a tooltip', () => {
    expect(severityCountBadges(DEFAULT_FINDING_FIELDS_FIXTURE, { low: 1, critical: 2, 'Sev 2.5': 1, sev3: 3 })).toEqual([
      { key: 'critical', label: 'Critical', color: 'danger', count: 2, tooltip: null },
      { key: 'low', label: 'Low', color: 'informative', count: 1, tooltip: null },
      { key: 'other', label: 'Other', color: 'subtle', count: 4, tooltip: 'Sev 2.5: 1, sev3: 3' },
    ])
  })

  it('returns no Other badge when every count matches an option', () => {
    expect(severityCountBadges(CONTOSO_FINDING_FIELDS, { sev2: 1, sev1: 0 })).toEqual([
      { key: 'sev2', label: 'Sev 2', color: 'warning', count: 1, tooltip: null },
    ])
    expect(severityCountBadges(CONTOSO_FINDING_FIELDS, {})).toEqual([])
  })

  it('ignores inherited members when a key names an Object.prototype member', () => {
    const constructorField = PROTOTYPE_KEY_FIELDS.fields[0]
    expect(severityBadge(PROTOTYPE_KEY_FIELDS, 'toString')).toEqual({ label: 'To string', color: 'subtle' })
    expect(levelOptions(constructorField, {}, 1)).toEqual([])
    expect(describeFindingValues(PROTOTYPE_KEY_FIELDS, 'toString', {})).toEqual({ rows: [], retired: [] })
    expect(firstMissingRequired(PROTOTYPE_KEY_FIELDS, 'toString', {})).toBe('Constructor')
    expect(severityCountBadges(PROTOTYPE_KEY_FIELDS, { low: 1 })).toEqual([
      { key: 'low', label: 'Low', color: 'informative', count: 1, tooltip: null },
    ])
  })
})
