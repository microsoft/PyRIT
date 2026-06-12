// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Production adapter bridging the shim's `RunWaveStarter` contract to
 * `runWave` + the real attacks API.
 *
 * The shim mints the wave (operator, waveId, controller, recording sink)
 * and hands the starter everything except the backend API + the audit
 * `operation` label. This adapter injects both, then forwards to
 * `runWave`. App.tsx constructs one of these from `attacksApi` and an
 * operation-label provider (sourced from the operator's global labels).
 */

import { runWave } from './wave'
import type { RunWaveArgs, WaveSummary } from './wave'
import type { RunWaveStarter, RunWaveStarterArgs } from './shim'
import type { RunnerAttacksApi } from './dispatch'

export interface CreateRunWaveStarterDeps {
  api: RunnerAttacksApi
  /** Audit `operation` label provider; re-read per wave for live sourcing. */
  operation: () => string | null | undefined
  /** Injectable for tests; defaults to the real `runWave`. */
  runWaveImpl?: (args: RunWaveArgs) => Promise<WaveSummary>
}

export function createRunWaveStarter(deps: CreateRunWaveStarterDeps): RunWaveStarter {
  const runWaveImpl = deps.runWaveImpl ?? runWave
  return (args: RunWaveStarterArgs) =>
    runWaveImpl({
      treeId: args.treeId,
      tree: args.tree,
      S: args.S,
      sink: args.sink,
      api: deps.api,
      operator: args.operator,
      operation: deps.operation() ?? '',
      waveId: args.waveId,
      waveTriggerKind: args.waveTriggerKind,
      parentConversationTreeId: args.parentConversationTreeId,
      controller: args.controller,
    })
}
