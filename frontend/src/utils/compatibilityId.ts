export function isCompatibilityId(value: unknown): value is string {
  return typeof value === 'string' && value.length <= 256 && value === value.trim() &&
    /^[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?\+g[0-9a-f]{40}$/.test(value)
}
