// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Type-level test for `InsertMenuOption`. Pins the discriminated-union
 * shape so a V1.1 disabled item cannot silently carry a stale `kind`
 * (the failure mode the PR5 reviewer flagged in Finding H#1: a disabled
 * "Fan out: prompt (coming later)" item that mints `kind: 'fan_attempt'`
 * would silently dispatch the wrong axis once a flag-flip swaps the
 * `disabled` flag without removing the wrong `kind`).
 *
 * Runtime cases use real values; the discriminant-narrowing cases use
 * `@ts-expect-error` to assert the type system rejects the wrong shape.
 */

import type { InsertMenuOption } from './InsertEdge'

describe('InsertMenuOption — discriminated-union shape', () => {
  it('allows a disabled item without a kind field', () => {
    const opt: InsertMenuOption = {
      disabled: true,
      label: 'Fan out: prompt (coming later)',
      disabledReason: 'Available in a future release',
    }
    expect(opt.disabled).toBe(true)
  })

  it('allows an enabled item with a kind field', () => {
    const opt: InsertMenuOption = {
      disabled: false,
      kind: 'send',
      label: 'Send to target',
    }
    expect(opt.disabled).toBe(false)
    if (opt.disabled === false) {
      expect(opt.kind).toBe('send')
    }
  })

  it('rejects a disabled item that carries a kind (the H#1 silent-passthrough)', () => {
    // @ts-expect-error: the `disabled: true` arm must not include `kind`
    const opt: InsertMenuOption = {
      disabled: true,
      kind: 'send',
      label: 'x',
      disabledReason: 'y',
    }
    expect(opt.disabled).toBe(true)
  })

  it('rejects an enabled item that omits kind', () => {
    // @ts-expect-error: the `disabled: false` arm requires `kind`
    const opt: InsertMenuOption = {
      disabled: false,
      label: 'x',
    }
    expect(opt.disabled).toBe(false)
  })

  it('narrows correctly: kind is accessible only after asserting disabled=false', () => {
    const opt: InsertMenuOption = {
      disabled: false,
      kind: 'follow_up_user_turn',
      label: 'Follow-up',
    }
    if (opt.disabled === false) {
      // After narrowing, opt.kind is typed as EdgeInsertKind.
      expect(opt.kind).toBe('follow_up_user_turn')
    }
  })
})
