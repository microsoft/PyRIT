import type {
  AssertDimension,
  AssertGenerationResultSummary,
  AssertReviewPayload,
  AssertTaxonomy,
} from '@/types'
import { generateClientId } from '@/utils/clientId'

import { asRecord, readBoolean, readString, readStringList } from './generationFormHelpers'

const MIN_LEVELS = 2

export interface CategoryDraft {
  readonly id: string
  readonly name: string
  readonly definition: string
  /** One example per line. */
  readonly examplesText: string
  readonly permissible: boolean
}

export interface TermDraft {
  readonly id: string
  readonly term: string
  readonly definition: string
  /** One example per line. */
  readonly examplesText: string
}

export interface ReviewLevelDraft {
  readonly id: string
  readonly name: string
  readonly definition: string
}

export interface ReviewDimensionDraft {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly levels: readonly ReviewLevelDraft[]
}

export interface AssertReviewDraft {
  readonly behaviorName: string
  readonly behaviorDefinition: string
  readonly terms: readonly TermDraft[]
  readonly categories: readonly CategoryDraft[]
  readonly dimensions: readonly ReviewDimensionDraft[]
}

function readNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function examplesFromText(text: string): string[] {
  return text.split('\n').map((line) => line.trim()).filter(Boolean)
}

/** Reads an ASSERT review payload, or returns null when it has no taxonomy to edit. */
export function parseAssertReviewPayload(payload: Record<string, unknown>): AssertReviewPayload | null {
  const taxonomy = asRecord(payload.taxonomy)
  const behavior = asRecord(taxonomy.behavior)
  if (!Array.isArray(taxonomy.behavior_categories)) return null
  const systematization = payload.systematization === null || payload.systematization === undefined
    ? null
    : asRecord(payload.systematization)
  return {
    systematization: systematization
      ? { scope: readString(systematization.scope), reasoning_summary: readString(systematization.reasoning_summary) }
      : null,
    taxonomy: {
      behavior: { name: readString(behavior.name), definition: readString(behavior.definition) },
      definition_of_terms: (Array.isArray(taxonomy.definition_of_terms) ? taxonomy.definition_of_terms : []).map((item) => {
        const term = asRecord(item)
        return { term: readString(term.term), definition: readString(term.definition), examples: readStringList(term.examples) }
      }),
      behavior_categories: taxonomy.behavior_categories.map((item) => {
        const category = asRecord(item)
        return {
          name: readString(category.name),
          definition: readString(category.definition),
          examples: readStringList(category.examples),
          permissible: readBoolean(category.permissible, false),
        }
      }),
    },
    dimensions: (Array.isArray(payload.dimensions) ? payload.dimensions : []).map((item) => {
      const dimension = asRecord(item)
      return {
        name: readString(dimension.name),
        description: readString(dimension.description),
        levels: (Array.isArray(dimension.levels) ? dimension.levels : []).map((level) => {
          const entry = asRecord(level)
          return { name: readString(entry.name), definition: readString(entry.definition) }
        }),
      }
    }),
  }
}

export function reviewDraftFromPayload(payload: AssertReviewPayload): AssertReviewDraft {
  return {
    behaviorName: payload.taxonomy.behavior.name,
    behaviorDefinition: payload.taxonomy.behavior.definition,
    terms: payload.taxonomy.definition_of_terms.map((term) => ({
      id: generateClientId(),
      term: term.term,
      definition: term.definition,
      examplesText: term.examples.join('\n'),
    })),
    categories: payload.taxonomy.behavior_categories.map((category) => ({
      id: generateClientId(),
      name: category.name,
      definition: category.definition,
      examplesText: category.examples.join('\n'),
      permissible: category.permissible,
    })),
    dimensions: payload.dimensions.map((dimension) => ({
      id: generateClientId(),
      name: dimension.name,
      description: dimension.description,
      levels: dimension.levels.map((level) => ({ id: generateClientId(), name: level.name, definition: level.definition })),
    })),
  }
}

export function newCategoryDraft(): CategoryDraft {
  return { id: generateClientId(), name: '', definition: '', examplesText: '', permissible: false }
}

export function newTermDraft(): TermDraft {
  return { id: generateClientId(), term: '', definition: '', examplesText: '' }
}

export function newReviewLevelDraft(): ReviewLevelDraft {
  return { id: generateClientId(), name: '', definition: '' }
}

function duplicates(values: readonly string[]): string[] {
  const seen = new Set<string>()
  const repeated = new Set<string>()
  for (const value of values) {
    if (seen.has(value)) repeated.add(value)
    seen.add(value)
  }
  return [...repeated]
}

/** Problems the server would reject when saving the review. */
export function reviewDraftErrors(draft: AssertReviewDraft): string[] {
  const errors: string[] = []
  if (!draft.behaviorDefinition.trim()) errors.push('The behavior needs a definition.')
  if (draft.categories.length === 0) errors.push('Keep at least one behavior category.')
  for (const [index, category] of draft.categories.entries()) {
    const label = category.name.trim() ? `Category "${category.name.trim()}"` : `Category ${index + 1}`
    if (!category.name.trim()) errors.push(`${label} needs a name.`)
    if (!category.definition.trim()) errors.push(`${label} needs a definition.`)
    if (examplesFromText(category.examplesText).length === 0) errors.push(`${label} needs at least one example.`)
  }
  for (const name of duplicates(draft.categories.map((category) => category.name.trim()).filter(Boolean))) {
    errors.push(`Category names must be unique; "${name}" is repeated.`)
  }
  for (const [index, term] of draft.terms.entries()) {
    if (!term.term.trim() || !term.definition.trim()) errors.push(`Term ${index + 1} needs a term and a definition.`)
  }
  for (const name of duplicates(draft.terms.map((term) => term.term.trim()).filter(Boolean))) {
    errors.push(`Terms must be unique; "${name}" is repeated.`)
  }
  for (const dimension of draft.dimensions) {
    if (dimension.levels.length < MIN_LEVELS) errors.push(`Dimension "${dimension.name}" needs at least ${MIN_LEVELS} levels.`)
    if (dimension.levels.some((level) => !level.name.trim() || !level.definition.trim())) {
      errors.push(`Every level of dimension "${dimension.name}" needs a name and a definition.`)
    }
    for (const name of duplicates(dimension.levels.map((level) => level.name.trim()).filter(Boolean))) {
      errors.push(`Dimension "${dimension.name}" repeats the level "${name}".`)
    }
  }
  return errors
}

/** The PUT payload for a valid review draft. */
export function reviewPayloadFromDraft(draft: AssertReviewDraft): { taxonomy: AssertTaxonomy; dimensions: AssertDimension[] } {
  return {
    taxonomy: {
      behavior: { name: draft.behaviorName, definition: draft.behaviorDefinition.trim() },
      definition_of_terms: draft.terms.map((term) => ({
        term: term.term.trim(),
        definition: term.definition.trim(),
        examples: examplesFromText(term.examplesText),
      })),
      behavior_categories: draft.categories.map((category) => ({
        name: category.name.trim(),
        definition: category.definition.trim(),
        examples: examplesFromText(category.examplesText),
        permissible: category.permissible,
      })),
    },
    dimensions: draft.dimensions.map((dimension) => ({
      name: dimension.name,
      description: dimension.description,
      levels: dimension.levels.map((level) => ({ name: level.name.trim(), definition: level.definition.trim() })),
    })),
  }
}

/** Reads a completed ASSERT generation's summary. */
export function parseAssertSummary(summary: Record<string, unknown>): AssertGenerationResultSummary {
  const coverage = summary.coverage === null || summary.coverage === undefined ? null : asRecord(summary.coverage)
  return {
    group_count: readNumber(summary.group_count),
    coverage: coverage ? {
      sample_size: readNumber(coverage.sample_size),
      unique_assignments: readNumber(coverage.unique_assignments),
      total_levels: readNumber(coverage.total_levels),
      covered_levels: readNumber(coverage.covered_levels),
      total_pairs: readNumber(coverage.total_pairs),
      covered_pairs: readNumber(coverage.covered_pairs),
    } : null,
    categories: (Array.isArray(summary.categories) ? summary.categories : []).map((item) => {
      const category = asRecord(item)
      return {
        name: readString(category.name),
        permissible: readBoolean(category.permissible, false),
        groups: readNumber(category.groups),
      }
    }),
    edited_stages: readStringList(summary.edited_stages),
  }
}
