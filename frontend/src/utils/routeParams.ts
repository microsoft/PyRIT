/**
 * Returns the original value represented by a React Router path parameter.
 *
 * React Router already decodes each path segment, but re-escapes decoded
 * slashes as `%2F` so they remain inside one parameter. Undo only that
 * re-escaping; calling `decodeURIComponent` again would corrupt literal `%`
 * sequences and can throw for malformed user-entered URLs.
 */
export function routerPathParamValue(value: string | undefined): string {
  return (value ?? '').replace(/%2F/gi, '/')
}
