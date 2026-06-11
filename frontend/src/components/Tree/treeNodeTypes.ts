// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Kind → react-flow node component map. Passed to
 * `<ReactFlow nodeTypes={treeNodeTypes} />`.
 *
 * Lives in its own module so eslint's react-refresh/only-export-components
 * rule stays happy (mixing component exports with non-component exports
 * defeats HMR for the components).
 *
 * The `satisfies Record<ConversationTreeNodeKind, ...>` clause makes
 * registry completeness a compile-time guarantee: adding a new kind to
 * the ConversationTreeNodeKind union without a registry entry fails tsc.
 */

import type { ComponentType } from 'react'

import type { ConversationTreeNodeKind } from '../../runner/treeTypes'
import {
  FanCard,
  ImportMessageCard,
  RootPromptCard,
  ScoreCard,
  SendCard,
  UserTurnCard,
} from './nodeCards'

export const treeNodeTypes = {
  root_prompt: RootPromptCard,
  import_message: ImportMessageCard,
  user_turn: UserTurnCard,
  send: SendCard,
  fan: FanCard,
  score: ScoreCard,
} as const satisfies Record<ConversationTreeNodeKind, ComponentType<never>>
