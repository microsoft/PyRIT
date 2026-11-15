import type { FilterOption, TargetInstance } from '@/types'
import { targetType } from '@/utils/targetIdentity'

/** Display name for each known modality, shared by the table icons and the filters. */
export const MODALITY_LABELS: Readonly<Record<string, string>> = {
  text: 'Text',
  image_path: 'Image',
  audio_path: 'Audio',
  video_path: 'Video',
  reasoning: 'Reasoning',
  function_call: 'Function call',
  function_call_output: 'Function call output',
  tool_call: 'Tool call',
  binary_path: 'Binary',
  url: 'URL',
}

/** Canonical display order for modalities; unknown values are appended last. */
export const MODALITY_ORDER: readonly string[] = [
  'text',
  'image_path',
  'audio_path',
  'video_path',
  'reasoning',
  'function_call',
  'function_call_output',
  'tool_call',
  'binary_path',
  'url',
]

/** Capability column definitions with tooltip descriptions. */
export const CAPABILITY_COLUMNS = [
  { key: 'supports_multi_turn', label: 'Multi-turn', tooltip: 'Supports multi-turn conversations' },
  { key: 'supports_multi_message_pieces', label: 'Multi-piece', tooltip: 'Supports multiple message pieces in a single request' },
  { key: 'supports_json_schema', label: 'JSON Schema', tooltip: 'Supports constraining output to a JSON schema' },
  { key: 'supports_json_output', label: 'JSON Output', tooltip: 'Supports JSON output format' },
  { key: 'supports_editable_history', label: 'Edit History', tooltip: 'Allows attack history to be modified' },
  { key: 'supports_system_prompt', label: 'System Prompt', tooltip: 'Supports system prompts' },
] as const

type CapabilityKey = (typeof CAPABILITY_COLUMNS)[number]['key']
type ModalityField = 'supported_input_modalities' | 'supported_output_modalities'

/** Selected values per filter; an empty list means the filter is off. */
export interface TargetFilters {
  types: string[]
  inputs: string[]
  outputs: string[]
  capabilities: string[]
}

export const DEFAULT_TARGET_FILTERS: TargetFilters = { types: [], inputs: [], outputs: [], capabilities: [] }

/** Choices per filter; a filter with no choices cannot narrow the table and is not shown. */
export type TargetFilterOptions = Record<keyof TargetFilters, FilterOption[]>

/** Known modalities in canonical order, followed by the rest in their given order. */
export function orderModalities(modalities: string[]): string[] {
  const known = MODALITY_ORDER.filter((modality: string) => modalities.includes(modality))
  const extras = modalities.filter((modality: string) => !MODALITY_ORDER.includes(modality))
  return [...known, ...extras]
}

function supportsModality(target: TargetInstance, field: ModalityField, modality: string): boolean {
  return (target.capabilities?.[field] ?? []).includes(modality)
}

function supportsCapability(target: TargetInstance, key: string): boolean {
  return target.capabilities?.[key as CapabilityKey] === true
}

/** Keep only the choices some target lacks; if none, the filter could not hide a row. */
function narrowingOptions(
  targets: TargetInstance[],
  options: FilterOption[],
  supports: (target: TargetInstance, value: string) => boolean,
): FilterOption[] {
  const canNarrow = options.some((option: FilterOption) =>
    targets.some((target: TargetInstance) => !supports(target, option.value)))
  return canNarrow ? options : []
}

function modalityOptions(targets: TargetInstance[], field: ModalityField): FilterOption[] {
  const present = new Set<string>()
  for (const target of targets) {
    for (const modality of target.capabilities?.[field] ?? []) {
      present.add(modality)
    }
  }
  // Sorting first puts modalities the GUI does not know yet at the end in alphabetical order.
  const options = orderModalities([...present].sort()).map((modality: string) => ({
    value: modality,
    label: MODALITY_LABELS[modality] ?? modality,
  }))
  return narrowingOptions(targets, options, (target, modality) => supportsModality(target, field, modality))
}

function capabilityOptions(targets: TargetInstance[]): FilterOption[] {
  const options = CAPABILITY_COLUMNS
    .filter(({ key }) => targets.some((target: TargetInstance) => supportsCapability(target, key)))
    .map(({ key, label }) => ({ value: key, label }))
  return narrowingOptions(targets, options, supportsCapability)
}

/** Current choices plus selected values that must remain visible and removable. */
export function getTargetFilterOptions(
  targets: TargetInstance[], filters: TargetFilters = DEFAULT_TARGET_FILTERS,
): TargetFilterOptions {
  const types = [...new Set(targets.map((target: TargetInstance) => targetType(target)))].sort()
  const options: TargetFilterOptions = {
    types: types.length > 1 ? types.map((type: string) => ({ value: type, label: type })) : [],
    inputs: modalityOptions(targets, 'supported_input_modalities'),
    outputs: modalityOptions(targets, 'supported_output_modalities'),
    capabilities: capabilityOptions(targets),
  }
  for (const key of ['types', 'inputs', 'outputs', 'capabilities'] as const) {
    for (const value of filters[key]) {
      if (options[key].some((option: FilterOption) => option.value === value)) continue
      const label = key === 'capabilities'
        ? CAPABILITY_COLUMNS.find((column: (typeof CAPABILITY_COLUMNS)[number]) => column.key === value)?.label
        : key === 'types' ? value : MODALITY_LABELS[value]
      options[key].push({ value, label: label ?? value })
    }
  }
  return options
}

export function hasActiveTargetFilters(filters: TargetFilters): boolean {
  return Object.values(filters).some((values: string[]) => values.length > 0)
}

/**
 * Whether a target passes every filter. Types, inputs, and outputs match any selected value;
 * capabilities are requirements, so a target must support every selected one.
 */
export function targetMatchesFilters(target: TargetInstance, filters: TargetFilters): boolean {
  const matchesAny = (selected: string[], matches: (value: string) => boolean): boolean =>
    selected.length === 0 || selected.some(matches)
  return matchesAny(filters.types, (type: string) => targetType(target) === type)
    && matchesAny(filters.inputs, (modality: string) => supportsModality(target, 'supported_input_modalities', modality))
    && matchesAny(filters.outputs, (modality: string) => supportsModality(target, 'supported_output_modalities', modality))
    && filters.capabilities.every((key: string) => supportsCapability(target, key))
}
