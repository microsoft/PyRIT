import { useCallback, useEffect, useRef, useState } from 'react'

interface TriedNames {
  scope: string
  names: Set<string>
}

/**
 * Start one load for each dataset name the first time it is a candidate within `scope`, such as a page visit
 * of one scenario in one runtime generation. A name already tried is never started again on its own, so a
 * metadata reload that still reports or omits it as missing cannot start a load loop. The returned retry
 * re-arms only the names it is given.
 */
export function useDatasetAutoLoad(
  scope: string, candidates: readonly string[], start: (names: string[]) => void,
): (names: string[]) => void {
  const tried = useRef<TriedNames>({ scope, names: new Set() })
  const [retries, setRetries] = useState(0)
  const candidateKey = JSON.stringify(candidates)

  useEffect(() => {
    if (tried.current.scope !== scope) tried.current = { scope, names: new Set() }
    const names = (JSON.parse(candidateKey) as string[]).filter((name) => !tried.current.names.has(name))
    if (names.length === 0) return
    for (const name of names) tried.current.names.add(name)
    start(names)
  }, [candidateKey, scope, retries, start])

  return useCallback((names: string[]) => {
    for (const name of names) tried.current.names.delete(name)
    setRetries((count) => count + 1)
  }, [])
}
