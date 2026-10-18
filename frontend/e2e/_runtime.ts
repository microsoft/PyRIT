import type { RuntimeReadiness, RuntimeStatus } from '../src/types'

export const READY_RUNTIME: RuntimeReadiness = {
  ready: true,
  state: 'ready',
  generation: 'e2e-runtime',
}

export const READY_RUNTIME_STATUS: RuntimeStatus = {
  state: 'ready',
  generation: READY_RUNTIME.generation,
  version: 'e2e-config',
  enabled: true,
  applying: false,
  outcome: 'success',
  message: 'PyRIT is ready.',
}
