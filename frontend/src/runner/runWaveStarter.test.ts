// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `createRunWaveStarter` — the production adapter that bridges
 * the shim's `RunWaveStarter` contract to `runWave` + the real attacks
 * API. The adapter is a thin argument-mapping; these tests pin that
 * mapping (and the operation-label sourcing) without exercising the full
 * dispatch machinery, by injecting a spy `runWaveImpl`.
 */

import { createRunWaveStarter } from './runWaveStarter'
import { createWaveController, type WaveSummary } from './wave'
import { mkRoot, mkSend, mkTree, nodeId, treeId } from './testHelpers'
import type { RunWaveArgs } from './wave'
import type { RunWaveStarterArgs } from './shim'
import type { RunnerAttacksApi } from './dispatch'
import type { RunnerStateSink } from './treeTypes'

const emptySummary: WaveSummary = {
  succeeded: 0,
  failed: { transient: 0, rate_limited: 0, permanent: 0 },
  blocked: 0,
  cancelled: 0,
  reflog_evicted: 0,
}

const noopApi: RunnerAttacksApi = {
  createAttack: jest.fn(),
  addMessage: jest.fn(),
}

const noopSink: RunnerStateSink = {
  setNodeState: jest.fn(),
  recordExecution: jest.fn(),
  clearExecution: jest.fn(),
  setReflogPinned: jest.fn(),
  emitWaveEvent: jest.fn(),
}

function mkArgs(): RunWaveStarterArgs {
  return {
    treeId: treeId('t-1'),
    tree: mkTree('root', [mkRoot('root'), mkSend('send-1', 'root')]),
    S: new Set([nodeId('send-1')]),
    waveId: 'w-1',
    waveTriggerKind: 'refresh_tree',
    operator: 'alice',
    parentConversationTreeId: null,
    controller: createWaveController(),
    sink: noopSink,
  }
}

describe('createRunWaveStarter', () => {
  it('forwards shim args to runWave and injects api + operation', async () => {
    const captured: RunWaveArgs[] = []
    const runWaveImpl = jest.fn(async (a: RunWaveArgs) => {
      captured.push(a)
      return emptySummary
    })
    const starter = createRunWaveStarter({
      api: noopApi,
      operation: () => 'campaign-x',
      runWaveImpl,
    })

    const args = mkArgs()
    const summary = await starter(args)

    expect(summary).toBe(emptySummary)
    expect(runWaveImpl).toHaveBeenCalledTimes(1)
    const fwd = captured[0]
    expect(fwd.treeId).toBe(args.treeId)
    expect(fwd.tree).toBe(args.tree)
    expect(fwd.S).toBe(args.S)
    expect(fwd.sink).toBe(args.sink)
    expect(fwd.api).toBe(noopApi)
    expect(fwd.operator).toBe('alice')
    expect(fwd.operation).toBe('campaign-x')
    expect(fwd.waveId).toBe('w-1')
    expect(fwd.waveTriggerKind).toBe('refresh_tree')
    expect(fwd.parentConversationTreeId).toBeNull()
    expect(fwd.controller).toBe(args.controller)
  })

  it('re-reads the operation provider on each call (live label sourcing)', async () => {
    let op = 'first'
    const runWaveImpl = jest.fn(async () => emptySummary)
    const starter = createRunWaveStarter({
      api: noopApi,
      operation: () => op,
      runWaveImpl,
    })

    await starter(mkArgs())
    op = 'second'
    await starter(mkArgs())

    expect(runWaveImpl.mock.calls[0][0].operation).toBe('first')
    expect(runWaveImpl.mock.calls[1][0].operation).toBe('second')
  })

  it('defaults operation to empty string when provider returns nullish', async () => {
    const runWaveImpl = jest.fn(async () => emptySummary)
    const starter = createRunWaveStarter({
      api: noopApi,
      operation: () => null,
      runWaveImpl,
    })
    await starter(mkArgs())
    expect(runWaveImpl.mock.calls[0][0].operation).toBe('')
  })
})
