/** Mirrors the server-side registry-name pattern so document editors fail fast instead of on a 422. */
const REGISTRY_NAME_PATTERN = /^[a-z][a-z0-9_]{0,63}$/

export function validateRegistryName(name: string): string | null {
  if (name.length === 0) {
    return 'Name is required.'
  }
  if (!REGISTRY_NAME_PATTERN.test(name)) {
    return 'Use lowercase letters, digits and underscores, starting with a letter (max 64 characters).'
  }
  return null
}
