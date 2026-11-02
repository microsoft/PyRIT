/**
 * A Fluent `Field` validationMessage slot without Fluent's default role="alert",
 * for forms that announce the error once from their own live region.
 * `id` is only set when given: an explicit undefined would drop Fluent's generated id.
 */
export function describedOnly(message: string | undefined, id?: string) {
  if (!message) return undefined
  return id ? { children: message, role: undefined, id } : { children: message, role: undefined }
}
