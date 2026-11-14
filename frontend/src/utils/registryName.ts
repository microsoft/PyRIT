/** Mirrors the server-side registry-name pattern so document editors fail fast instead of on a 422. */
const REGISTRY_NAME_PATTERN = /^[a-z][a-z0-9_]{0,63}$/
const MAX_REGISTRY_NAME_LENGTH = 64
const FALLBACK_REGISTRY_NAME = 'entry'

export function validateRegistryName(name: string): string | null {
  if (name.length === 0) {
    return 'Name is required.'
  }
  if (!REGISTRY_NAME_PATTERN.test(name)) {
    return 'Use lowercase letters, digits and underscores, starting with a letter (max 64 characters).'
  }
  return null
}

/** The lowercase words of a label, splitting a class name such as "AzureBlobStorage" at its capitals. */
function labelWords(label: string): string[] {
  // A label without spaces is a class name, so split its words at the capitals.
  const spaced = /\s/.test(label)
    ? label
    : label.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
  return spaced.toLowerCase().match(/[a-z0-9]+/g) ?? []
}

/**
 * Suggest a valid registry name from a label such as "OpenAI chat" or "AzureBlobStorage" and an optional detail
 * such as a model name, adding `_2`, `_3` and so on until it isn't one of the taken names.
 */
export function suggestRegistryName(label: string, takenNames: ReadonlySet<string>, detail = ''): string {
  const slug = [...labelWords(label), ...labelWords(detail)].join('_')
  let base = slug === '' ? FALLBACK_REGISTRY_NAME : slug
  if (!/^[a-z]/.test(base)) {
    base = `${FALLBACK_REGISTRY_NAME}_${base}`
  }
  base = base.slice(0, MAX_REGISTRY_NAME_LENGTH)
  if (!takenNames.has(base)) {
    return base
  }
  for (let suffix = 2; ; suffix += 1) {
    const ending = `_${suffix}`
    const candidate = `${base.slice(0, MAX_REGISTRY_NAME_LENGTH - ending.length)}${ending}`
    if (!takenNames.has(candidate)) {
      return candidate
    }
  }
}
