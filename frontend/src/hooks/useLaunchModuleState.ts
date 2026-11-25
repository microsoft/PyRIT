import { useCallback, useMemo } from 'react'

import { useSearchParams } from 'react-router'

const OPEN_MODULE_PARAM = 'open'

interface LaunchModuleState {
  /** Module cards the user opened, kept in the URL so Back and links restore them. */
  readonly openModules: readonly string[]
  readonly setOpenModules: (modules: readonly string[]) => void
}

/** The Launch tab's open module cards, kept in the URL as `?open=airt&open=garak`. */
export function useLaunchModuleState(): LaunchModuleState {
  const [searchParams, setSearchParams] = useSearchParams()
  const openModules = useMemo(() => searchParams.getAll(OPEN_MODULE_PARAM), [searchParams])

  const setOpenModules = useCallback((modules: readonly string[]) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete(OPEN_MODULE_PARAM)
      for (const module of new Set(modules)) next.append(OPEN_MODULE_PARAM, module)
      return next
    }, { replace: true })
  }, [setSearchParams])

  return { openModules, setOpenModules }
}
