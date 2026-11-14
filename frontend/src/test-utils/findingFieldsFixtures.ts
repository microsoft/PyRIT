import type { FindingField, FindingFieldOption, FindingFields } from '@/types'

type FieldDefinition = Pick<FindingField, 'key' | 'label' | 'type'> & Partial<FindingField>

/** Fills in the backend's defaults, so each fixture field has the full JSON shape. */
function field(definition: FieldDefinition): FindingField {
  return {
    required: false,
    options: [],
    levels: [],
    default: null,
    allow_other: false,
    colors: {},
    hint: null,
    section: null,
    internal: false,
    ...definition,
  }
}

export function option(key: string, label: string, children: FindingFieldOption[] = []): FindingFieldOption {
  return { key, label, children }
}

/** A Contoso deployment's fields: a severity level, a cascade, Other text, a required multiline and an internal field. */
export const CONTOSO_FINDING_FIELDS: FindingFields = {
  severity: field({
    key: 'severity',
    label: 'Severity',
    type: 'choice',
    required: true,
    options: [
      option('sev1', 'Sev 1', [option('customer_impact', 'Customer impact')]),
      option('sev2', 'Sev 2', [option('internal_impact', 'Internal impact')]),
    ],
    levels: [['severity_rationale', 'Severity rationale']],
    colors: { sev1: 'red', sev2: 'yellow' },
  }),
  fields: [
    field({
      key: 'category',
      label: 'Category',
      type: 'choice',
      required: true,
      options: [
        option('model_security', 'Model security', [
          option('system_prompt_leak', 'System prompt leak'),
          option('tool_misuse', 'Tool misuse'),
        ]),
        option('content_safety', 'Content safety', [
          option('hate_speech', 'Hate speech'),
          option('self_harm', 'Self-harm'),
        ]),
      ],
      levels: [['subcategory', 'Subcategory']],
      section: 'Classification',
    }),
    field({
      key: 'technique',
      label: 'Technique',
      type: 'choice',
      options: [option('prompt_injection', 'Prompt injection'), option('role_play', 'Role play')],
      allow_other: true,
      section: 'Reproduction',
    }),
    field({
      key: 'steps',
      label: 'Steps to reproduce',
      type: 'multiline',
      required: true,
      hint: 'List each prompt you sent to the Contoso assistant, in order.',
      section: 'Reproduction',
    }),
    field({ key: 'owner', label: 'Owner', type: 'text', section: 'Tracking', internal: true }),
  ],
}

export function contosoField(key: string): FindingField {
  const match = CONTOSO_FINDING_FIELDS.fields.find((candidate: FindingField) => candidate.key === key)
  if (match === undefined) throw new Error(`CONTOSO_FINDING_FIELDS has no field ${key}`)
  return match
}

/** A copy of the backend's `DEFAULT_FINDING_FIELDS`, with three harm types instead of the whole taxonomy. */
export const DEFAULT_FINDING_FIELDS_FIXTURE: FindingFields = {
  severity: field({
    key: 'severity',
    label: 'Severity',
    type: 'choice',
    required: true,
    options: [
      option('critical', 'Critical'),
      option('important', 'Important'),
      option('moderate', 'Moderate'),
      option('low', 'Low'),
      option('informational', 'Informational'),
    ],
    default: 'moderate',
    allow_other: true,
    colors: { critical: 'red', important: 'yellow', moderate: 'yellow', low: 'gray' },
  }),
  fields: [
    field({
      key: 'harm_type',
      label: 'Harm-type',
      type: 'choice',
      options: [
        option('Hate Speech', 'Hate Speech'),
        option('Insecure Code', 'Insecure Code'),
        option('Malware', 'Malware'),
      ],
      allow_other: true,
      hint: 'Optional. Select a category, or choose Other for custom text.',
    }),
    field({ key: 'description', label: 'Description', type: 'multiline', hint: 'Optional' }),
  ],
}
