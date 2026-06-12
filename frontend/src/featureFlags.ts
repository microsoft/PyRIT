// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Build-time feature flags read from Vite env vars.
 */

/**
 * Per spec §9.4.5: the V1.0 tree view ships behind `VITE_ENABLE_TREE_UI`
 * so it stays inert in production until explicitly enabled. Accepts
 * `'true'` or `'1'`; anything else (including unset) is off.
 */
export function isTreeUiEnabled(): boolean {
  const raw = import.meta.env.VITE_ENABLE_TREE_UI
  return raw === 'true' || raw === '1'
}
