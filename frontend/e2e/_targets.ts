// Shared helpers for building target-instance mock payloads in e2e tests.
//
// Mirrors the frontend `makeTarget` fixture (src/test-utils/targetFixtures.ts):
// folds flat identity scalars into the embedded `identifier` so route mocks
// match the current nested `TargetInstance` wire model. Without this, the
// target table crashes reading `target.identifier.class_name` and no rows
// (and no "Set Active" button) render.

export interface FlatTarget {
  target_registry_name: string;
  target_type?: string;
  endpoint?: string | null;
  model_name?: string | null;
  capabilities?: unknown;
  target_specific_params?: unknown;
  inner_targets?: FlatTarget[] | null;
}

interface TargetInstanceMock {
  target_registry_name: string;
  identifier: {
    class_name: string;
    hash: string;
    endpoint: string | null;
    model_name: string | null;
  };
  capabilities: unknown;
  target_specific_params: unknown;
  inner_targets: TargetInstanceMock[] | null;
}

export function makeTarget(flat: FlatTarget): TargetInstanceMock {
  return {
    target_registry_name: flat.target_registry_name,
    identifier: {
      class_name: flat.target_type ?? "TextTarget",
      hash: `${flat.target_registry_name}-hash`,
      endpoint: flat.endpoint ?? null,
      model_name: flat.model_name ?? null,
    },
    capabilities: flat.capabilities ?? null,
    target_specific_params: flat.target_specific_params ?? null,
    inner_targets: flat.inner_targets ? flat.inner_targets.map(makeTarget) : null,
  };
}
