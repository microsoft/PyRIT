import { useEffect, useState } from 'react'

import { promptGeneratorsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { AssertBehaviorPreset, AssertSuitePreset } from '@/types'

export interface AssertLibraryState {
  readonly loading: boolean
  readonly behaviors: AssertBehaviorPreset[]
  readonly suites: AssertSuitePreset[]
  readonly error: string | null
}

const INITIAL_STATE: AssertLibraryState = { loading: true, behaviors: [], suites: [], error: null }

/** Loads the bundled ASSERT behavior and suite templates. */
export function useAssertLibrary(): AssertLibraryState {
  const [state, setState] = useState<AssertLibraryState>(INITIAL_STATE)

  useEffect(() => {
    const controller = new AbortController()
    const loadAsync = async (): Promise<void> => {
      try {
        const [behaviors, suites] = await Promise.all([
          promptGeneratorsApi.listAssertBehaviors(controller.signal),
          promptGeneratorsApi.listAssertSuites(controller.signal),
        ])
        if (!controller.signal.aborted) {
          setState({ loading: false, behaviors: behaviors.items, suites: suites.items, error: null })
        }
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setState({ loading: false, behaviors: [], suites: [], error: toApiError(error).detail })
        }
      }
    }
    void loadAsync()
    return () => { controller.abort() }
  }, [])

  return state
}
