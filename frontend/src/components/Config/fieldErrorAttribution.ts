/**
 * Match a create-target error message to the one form field it names.
 * Returns a field only when exactly one field matches; otherwise the error
 * stays a form-level summary.
 */

export interface AttributionCandidate {
  /** `null` marks a name the dialog cannot mark invalid; matching it attributes nothing. */
  key: string | null
  names: string[]
}

// Shorter names ("id", "n") match too much ordinary prose.
const MIN_NAME_LENGTH = 3

/** `http_request` → `Http Request` */
export function getParameterLabel(name: string): string {
  return name
    .split('_')
    .map((word) => word.length === 1 ? word.toUpperCase() : `${word[0].toUpperCase()}${word.slice(1)}`)
    .join(' ')
}

/** `http_request`, `Http Request` and `HTTP-request` all become `http request`. */
export function normalizeForMatch(text: string): string {
  return text
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[_\-\s]+/g, ' ')
    .trim()
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

// Unicode-aware word boundaries (\b is ASCII-only), so `endpoint` never matches inside `azure_endpoint`.
const NOT_AFTER_WORD = '(?<![\\p{L}\\p{N}_])'
const NOT_BEFORE_WORD = '(?![\\p{L}\\p{N}_])'
const IDENTIFIER = /^[\p{L}\p{N}_]+$/u

function namePattern(name: string): RegExp | null {
  const normalizedName = normalizeForMatch(name)
  if (normalizedName.length < MIN_NAME_LENGTH) return null
  const raw = name.normalize('NFKC').toLowerCase()
  // Identifiers match as written; multi-word labels match with any separator.
  const body = IDENTIFIER.test(raw)
    ? escapeRegExp(raw)
    : normalizedName.split(' ').map(escapeRegExp).join('[\\s_\\-]+')
  return new RegExp(`${NOT_AFTER_WORD}${body}${NOT_BEFORE_WORD}`, 'u')
}

export function attributeErrorToField(
  message: string,
  candidates: readonly AttributionCandidate[],
): string | null {
  const normalizedMessage = message.normalize('NFKC').toLowerCase()
  const matchedKeys = new Set<string>()
  for (const candidate of candidates) {
    if (!candidate.names.some((name) => namePattern(name)?.test(normalizedMessage))) continue
    if (candidate.key === null) return null
    matchedKeys.add(candidate.key)
  }
  return matchedKeys.size === 1 ? [...matchedKeys][0] : null
}
