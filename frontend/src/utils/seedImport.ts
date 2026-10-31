import type {
  ChatMessageRole,
  ConversationDraftMessage,
  ConversationDraftPiece,
  ConversationDraftRole,
  MessageAttachment,
  SeedRecord,
} from '@/types'
import { generateClientId } from './clientId'
import { draftAttachment } from './conversationDraft'
import { basenameFromValue, isPathDataType } from './media'

export interface SinglePromptImport {
  type: 'single_prompt'
  text: string
  attachments: MessageAttachment[]
  textMetadata?: Record<string, unknown>
  objective?: string
}

export interface ConversationImport {
  type: 'conversation'
  messages: ConversationDraftMessage[]
  objective?: string
  systemPrompt?: string
}

export interface UnsupportedImport {
  type: 'unsupported'
  reason: string
}

export type SeedImportOutcome = SinglePromptImport | ConversationImport | UnsupportedImport

/**
 * Maps a stored SeedRecord role to a conversation draft role.
 * Imported assistant and tool messages strictly use simulated roles.
 */
function mapToDraftRole(role: ChatMessageRole | null): ConversationDraftRole | null {
  if (!role) return null
  switch (role) {
    case 'user':
      return 'user'
    case 'assistant':
    case 'simulated_assistant':
      return 'simulated_assistant'
    case 'system':
      return 'system'
    case 'developer':
      return 'developer'
    case 'tool':
    case 'simulated_tool':
      return 'simulated_tool'
    default:
      return null
  }
}

/**
 * Inspects a list of SeedRecords belonging to one logical example and produces
 * either a single prompt (for the composer), a conversation draft (for the ConversationEditor),
 * or an unsupported error with an explicit explanation.
 *
 * Strict invariants:
 * 1. Never makes network or model calls.
 * 2. Does not execute or generate simulated conversations.
 * 3. Seed IDs are never assigned to source_piece_id.
 * 4. Stored value remains the durable reference; value_url is preview only.
 * 5. Does not guess ambiguous roles or sequences.
 */
export function buildSeedImport(members: readonly SeedRecord[]): SeedImportOutcome {
  if (!members || members.length === 0) {
    return {
      type: 'unsupported',
      reason: 'No stored members were returned for this example.',
    }
  }

  // 1. Simulated conversation configuration guard
  if (members.some((m) => m.seed_type === 'simulated_conversation')) {
    return {
      type: 'unsupported',
      reason:
        'This example contains a simulated conversation configuration that requires generation. It cannot be imported directly into chat.',
    }
  }

  // 2. Extract objective metadata
  const objectiveRecords = members.filter((m) => m.seed_type === 'objective')
  const objective = objectiveRecords
    .map((m) => m.value?.trim())
    .filter(Boolean)
    .join('\n\n') || undefined

  // 3. Extract prompt members
  const promptRecords = members.filter((m) => m.seed_type === 'prompt')
  if (promptRecords.length === 0) {
    return {
      type: 'unsupported',
      reason: 'This example contains only objective records without any prompt content to import.',
    }
  }

  // 4. Validate roles & check for ambiguity
  const hasRoles = promptRecords.map((m) => m.role)
  const nonNullRoles = hasRoles.filter((r): r is ChatMessageRole => r !== null && r !== undefined)

  if (promptRecords.length > 1) {
    if (nonNullRoles.length > 0 && nonNullRoles.length < promptRecords.length) {
      return {
        type: 'unsupported',
        reason: 'Some prompt members are missing conversation roles, making the turn sequence ambiguous.',
      }
    }
  }

  // 5. Build raw draft pieces
  // Group pieces into logical turns:
  // - If prompt_group_id is present, group by prompt_group_id.
  // - If prompt_group_id is missing, group by consecutive matching roles or single turn if single member.
  interface GroupedTurn {
    role: ConversationDraftRole
    groupId: string | null
    sequence: number
    pieces: ConversationDraftPiece[]
  }

  const turns: GroupedTurn[] = []

  // Check if prompt_group_id is used
  const hasGroupIds = promptRecords.some((m) => Boolean(m.prompt_group_id))

  if (hasGroupIds) {
    const groupMap = new Map<string, SeedRecord[]>()
    for (const record of promptRecords) {
      const gId = record.prompt_group_id || `turn-${record.id}`
      const existing = groupMap.get(gId) ?? []
      existing.push(record)
      groupMap.set(gId, existing)
    }

    for (const [gId, recordsInGroup] of groupMap.entries()) {
      // Validate roles within group are consistent
      const groupRoles = recordsInGroup
        .map((r) => r.role)
        .filter((r): r is ChatMessageRole => r !== null && r !== undefined)
      const mappedGroupRoles = groupRoles.map(mapToDraftRole)

      if (mappedGroupRoles.some((r) => r === null)) {
        return {
          type: 'unsupported',
          reason: `Group ${gId} contains unsupported role values.`,
        }
      }

      const distinctRoles = new Set(mappedGroupRoles)
      if (distinctRoles.size > 1) {
        return {
          type: 'unsupported',
          reason: `Members within the prompt group '${gId}' have conflicting roles.`,
        }
      }

      const finalRole = mappedGroupRoles[0] ?? 'user'
      // Sort pieces by sequence within group
      const sortedRecords = [...recordsInGroup].sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0))
      const minSeq = Math.min(...recordsInGroup.map((r) => r.sequence ?? 0))

      const pieces: ConversationDraftPiece[] = sortedRecords.map((record) => ({
        draftId: generateClientId(),
        data_type: record.data_type,
        original_value: record.value,
        mime_type: undefined,
        previewUrl: record.value_url ?? undefined,
        filename: record.name ?? (isPathDataType(record.data_type) ? basenameFromValue(record.value, record.data_type) : undefined),
        prompt_metadata: record.metadata ?? undefined,
        source_piece_id: undefined, // Never use seed ID as chat piece ID
      }))

      turns.push({
        role: finalRole,
        groupId: gId,
        sequence: minSeq,
        pieces,
      })
    }

    turns.sort((a, b) => a.sequence - b.sequence)
  } else {
    // No prompt_group_id: group records into turns by role and sequence.
    // If consecutive records share the same role and sequence (e.g. text + image in a single turn),
    // they are combined as pieces within that turn.
    let currentTurn: GroupedTurn | null = null
    for (const record of promptRecords) {
      const mappedRole = mapToDraftRole(record.role) ?? (nonNullRoles.length === 0 ? 'user' : null)
      if (!mappedRole) {
        return {
          type: 'unsupported',
          reason: `Record ${record.id} has an unsupported role '${record.role}'.`,
        }
      }

      const piece: ConversationDraftPiece = {
        draftId: generateClientId(),
        data_type: record.data_type,
        original_value: record.value,
        mime_type: undefined,
        previewUrl: record.value_url ?? undefined,
        filename:
          record.name ??
          (isPathDataType(record.data_type)
            ? basenameFromValue(record.value, record.data_type)
            : undefined),
        prompt_metadata: record.metadata ?? undefined,
        source_piece_id: undefined,
      }

      const matchesCurrentTurn =
        currentTurn &&
        currentTurn.role === mappedRole &&
        (record.sequence === null ||
          currentTurn.sequence === record.sequence ||
          promptRecords.every((r) => (r.role ?? 'user') === 'user'))

      if (matchesCurrentTurn && currentTurn) {
        currentTurn.pieces.push(piece)
      } else {
        currentTurn = {
          role: mappedRole,
          groupId: null,
          sequence: record.sequence ?? 0,
          pieces: [piece],
        }
        turns.push(currentTurn)
      }
    }
    turns.sort((a, b) => a.sequence - b.sequence)
  }

  if (turns.length === 0) {
    return {
      type: 'unsupported',
      reason: 'No valid turns could be constructed from this example.',
    }
  }

  // 6. Check if this is a Single Prompt vs. Multi-turn Conversation
  // Single prompt: exactly 1 turn, role is 'user'
  if (turns.length === 1 && turns[0].role === 'user') {
    const turn = turns[0]
    const textPieces = turn.pieces.filter((p) => !isPathDataType(p.data_type))
    const mediaPieces = turn.pieces.filter((p) => isPathDataType(p.data_type))

    const text = textPieces.map((p) => p.original_value).join('\n\n')
    const attachments: MessageAttachment[] = mediaPieces.map(draftAttachment)
    const textMetadata = textPieces[0]?.prompt_metadata

    return {
      type: 'single_prompt',
      text,
      attachments,
      textMetadata,
      objective,
    }
  }

  // Multi-turn or single non-user turn -> Conversation
  // Extract system prompt if first turn is system or developer
  let systemPrompt: string | undefined
  if (turns[0].role === 'system' || turns[0].role === 'developer') {
    const sysPieces = turns[0].pieces.filter((p) => !isPathDataType(p.data_type))
    if (sysPieces.length > 0) {
      systemPrompt = sysPieces.map((p) => p.original_value).join('\n\n')
    }
  }

  const messages: ConversationDraftMessage[] = turns.map((turn) => ({
    id: generateClientId(),
    role: turn.role,
    pieces: turn.pieces,
  }))

  return {
    type: 'conversation',
    messages,
    objective,
    systemPrompt,
  }
}
