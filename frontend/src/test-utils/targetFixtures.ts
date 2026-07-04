import type { TargetCapabilities, TargetIdentifier, TargetInstance } from '../types'

/**
 * Flat description of a target for tests, mirroring the old pre-slimming
 * `TargetInstance` shape. `makeTarget` folds the identity scalars into the
 * embedded `identifier` so tests can stay readable while exercising the
 * current nested wire model.
 */
export interface FlatTargetInput {
  target_registry_name: string
  target_type?: string
  endpoint?: string | null
  model_name?: string | null
  underlying_model_name?: string | null
  temperature?: number | null
  top_p?: number | null
  max_requests_per_minute?: number | null
  identifier_hash?: string | null
  identifier?: Partial<TargetIdentifier>
  capabilities?: TargetCapabilities | null
  target_specific_params?: Record<string, unknown> | null
  inner_targets?: FlatTargetInput[] | null
}

export function makeTarget(flat: FlatTargetInput): TargetInstance {
  const identifier: TargetIdentifier = {
    class_name: flat.target_type ?? 'TextTarget',
    hash: flat.identifier_hash ?? `${flat.target_registry_name}-hash`,
    endpoint: flat.endpoint ?? null,
    model_name: flat.model_name ?? null,
    underlying_model_name: flat.underlying_model_name ?? null,
    temperature: flat.temperature ?? null,
    top_p: flat.top_p ?? null,
    max_requests_per_minute: flat.max_requests_per_minute ?? null,
    ...flat.identifier,
  }
  return {
    target_registry_name: flat.target_registry_name,
    identifier,
    capabilities: flat.capabilities ?? null,
    target_specific_params: flat.target_specific_params ?? null,
    inner_targets: flat.inner_targets ? flat.inner_targets.map(makeTarget) : null,
  }
}
