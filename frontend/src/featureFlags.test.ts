// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the tree-UI feature flag. The flag gates the V1.0 tree
 * view's nav entry + mount so the in-progress feature ships inert in
 * production until explicitly enabled (spec §9.4.5 enableTreeUI).
 */

import { isTreeUiEnabled } from './featureFlags'

describe('isTreeUiEnabled', () => {
  const original = process.env.VITE_ENABLE_TREE_UI

  afterEach(() => {
    if (original === undefined) delete process.env.VITE_ENABLE_TREE_UI
    else process.env.VITE_ENABLE_TREE_UI = original
  })

  it('returns false when the flag is unset', () => {
    delete process.env.VITE_ENABLE_TREE_UI
    expect(isTreeUiEnabled()).toBe(false)
  })

  it('returns true when the flag is the string "true"', () => {
    process.env.VITE_ENABLE_TREE_UI = 'true'
    expect(isTreeUiEnabled()).toBe(true)
  })

  it('returns true when the flag is "1"', () => {
    process.env.VITE_ENABLE_TREE_UI = '1'
    expect(isTreeUiEnabled()).toBe(true)
  })

  it('returns false for any other value', () => {
    process.env.VITE_ENABLE_TREE_UI = 'false'
    expect(isTreeUiEnabled()).toBe(false)
    process.env.VITE_ENABLE_TREE_UI = 'yes'
    expect(isTreeUiEnabled()).toBe(false)
  })
})
