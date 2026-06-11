// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Edge-type registry. Passed to `<ReactFlow edgeTypes={treeEdgeTypes} />`.
 * One entry today (`insert` → InsertEdge); future edge types (e.g.,
 * highlight-on-main-path in V1.1) register here.
 */

import { InsertEdge } from './InsertEdge'

export const treeEdgeTypes = {
  insert: InsertEdge,
} as const
