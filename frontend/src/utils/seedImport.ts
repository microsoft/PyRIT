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
  composerDraft?: {
    text: string
    attachments: MessageAttachment[]
    textMetadata?: Record<string, unknown>
  }
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

function recordToDraftPiece(record: SeedRecord): ConversationDraftPiece {
  return {
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
    source_piece_id: undefined, // Never use seed ID as chat piece ID
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
 * 5. Turns are grouped by stored sequence and compatible role (Comment 1).
 * 6. Final user turn split retains all content unsent in composer (Issue spec).
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

  // 5. Group pieces into turns by stored sequence.
  // In SeedGroup, members of an example share the same prompt_group_id, while
  // different conversation turns have distinct sequence numbers.
  // Records with the same sequence form pieces within the same turn.
  interface GroupedTurn {
    role: ConversationDraftRole
    sequence: number
    pieces: ConversationDraftPiece[]
  }

  const turns: GroupedTurn[] = []

  // Check sequence definition across prompt records
  const hasSequenceDefined = promptRecords.some((r) => r.sequence !== null && r.sequence !== undefined)
  const missingSequenceCount = promptRecords.filter((r) => r.sequence === null || r.sequence === undefined).length

  if (promptRecords.length > 1 && hasSequenceDefined && missingSequenceCount > 0) {
    return {
      type: 'unsupported',
      reason: 'Some prompt members are missing sequence numbers, making the turn order ambiguous.',
    }
  }

  if (hasSequenceDefined) {
    // Group records by sequence
    const sequenceMap = new Map<number, SeedRecord[]>()
    for (const record of promptRecords) {
      const seq = record.sequence ?? 0
      const existing = sequenceMap.get(seq) ?? []
      existing.push(record)
      sequenceMap.set(seq, existing)
    }

    const sortedSequences = Array.from(sequenceMap.keys()).sort((a, b) => a - b)

    for (const seq of sortedSequences) {
      const recordsInSequence = sequenceMap.get(seq) ?? []
      const sequenceRoles = recordsInSequence
        .map((r) => r.role)
        .filter((r): r is ChatMessageRole => r !== null && r !== undefined)
      const mappedRoles = sequenceRoles.map(mapToDraftRole)

      if (mappedRoles.some((r) => r === null)) {
        return {
          type: 'unsupported',
          reason: `Turn at sequence ${seq} contains unsupported role values.`,
        }
      }

      const distinctRoles = new Set(mappedRoles)
      if (distinctRoles.size > 1) {
        return {
          type: 'unsupported',
          reason: `Members within sequence ${seq} have conflicting roles.`,
        }
      }

      const finalRole = mappedRoles[0] ?? (nonNullRoles.length === 0 ? 'user' : null)
      if (!finalRole) {
        return {
          type: 'unsupported',
          reason: `Turn at sequence ${seq} has an ambiguous or missing role.`,
        }
      }

      const pieces = recordsInSequence.map(recordToDraftPiece)
      turns.push({
        role: finalRole,
        sequence: seq,
        pieces,
      })
    }
  } else {
    // No sequence numbers stored on records
    if (promptRecords.length === 1) {
      const record = promptRecords[0]
      const mappedRole = mapToDraftRole(record.role) ?? 'user'
      turns.push({
        role: mappedRole,
        sequence: 0,
        pieces: [recordToDraftPiece(record)],
      })
    } else {
      // Multiple records without sequence numbers:
      // If all records share the same role (e.g. text + image pieces in a single prompt),
      // combine them into a single turn at sequence 0.
      const allRoles = promptRecords.map((r) => mapToDraftRole(r.role) ?? 'user')
      const distinctRoles = new Set(allRoles)
      if (distinctRoles.size === 1) {
        turns.push({
          role: allRoles[0],
          sequence: 0,
          pieces: promptRecords.map(recordToDraftPiece),
        })
      } else {
        return {
          type: 'unsupported',
          reason: 'Prompt members have ambiguous turn sequence or missing sequence ordering.',
        }
      }
    }
  }

  if (turns.length === 0) {
    return {
      type: 'unsupported',
      reason: 'No valid turns could be constructed from this example.',
    }
  }

  // 6. Differentiate Single Prompt vs. Multi-turn Conversation with Final User Turn Split
  // If exactly 1 turn and role is 'user', import as single prompt to editable composer.
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

  // Apply final-user-turn split convention:
  // If the conversation ends in a user turn, earlier turns become prepended history
  // and the final user turn remains editable and unsent in the composer.
  // If it ends in an assistant/tool turn, retain the whole selected history and leave next prompt empty.
  const lastTurn = turns[turns.length - 1]
  let historyTurns = turns
  let composerDraft: ConversationImport['composerDraft'] | undefined

  if (lastTurn.role === 'user' && turns.length > 1) {
    historyTurns = turns.slice(0, -1)
    const textPieces = lastTurn.pieces.filter((p) => !isPathDataType(p.data_type))
    const mediaPieces = lastTurn.pieces.filter((p) => isPathDataType(p.data_type))
    composerDraft = {
      text: textPieces.map((p) => p.original_value).join('\n\n'),
      attachments: mediaPieces.map(draftAttachment),
      textMetadata: textPieces[0]?.prompt_metadata,
    }
  }

  const messages: ConversationDraftMessage[] = historyTurns.map((turn) => ({
    id: generateClientId(),
    role: turn.role,
    pieces: turn.pieces,
  }))

  return {
    type: 'conversation',
    messages,
    composerDraft,
    objective,
    systemPrompt,
  }
}
