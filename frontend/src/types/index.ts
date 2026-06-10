// ============================================================================
// Frontend UI Types
// ============================================================================

export interface MessageAttachment {
  type: 'image' | 'audio' | 'video' | 'file'
  name: string
  url: string
  mimeType: string
  /**
   * Decoded byte count when known. Omitted for path / URL / scheme-prefixed
   * values (e.g. `/api/media?path=...`) where the value is a reference, not
   * the payload, so its string length would be meaningless.
   */
  size?: number
  file?: File
  /** Backend piece ID — preserved so remix/copy can trace back to the original piece */
  pieceId?: string
  /** Backend prompt_metadata — preserved so video_id etc. carry over on remix/copy */
  metadata?: Record<string, unknown>
}

export interface Message {
  role: 'user' | 'assistant' | 'simulated_assistant' | 'system'
  content: string
  timestamp: string
  attachments?: MessageAttachment[]
  /** If the backend returned an error for this message */
  error?: MessageError
  /** True while waiting for the backend response */
  isLoading?: boolean
  /** Reasoning summaries from model thinking (e.g. OpenAI reasoning tokens) */
  reasoningSummaries?: string[]
  /**
   * Original text content before conversion. Only set when it differs
   * from `content` (which holds the converted value).
   */
  originalContent?: string
  /** Original media attachments before conversion (when different from converted). */
  originalAttachments?: MessageAttachment[]
}

export interface MessageError {
  type: string // e.g. 'blocked', 'processing', 'empty', 'unknown'
  description?: string
}

// ============================================================================
// Backend DTO Types (mirror pyrit/backend/models)
// ============================================================================

export interface PaginationInfo {
  limit: number
  has_more: boolean
  next_cursor?: string | null
  prev_cursor?: string | null
}

// --- Targets ---

export interface TargetCapabilitiesInfo {
  supports_multi_turn: boolean
  supports_multi_message_pieces: boolean
  supports_json_schema: boolean
  supports_json_output: boolean
  supports_editable_history: boolean
  supports_system_prompt: boolean
  supported_input_modalities: string[]
  supported_output_modalities: string[]
}

export interface TargetInstance {
  target_registry_name: string
  target_type: string
  endpoint?: string | null
  model_name?: string | null
  underlying_model_name?: string | null
  temperature?: number | null
  top_p?: number | null
  max_requests_per_minute?: number | null
  capabilities?: TargetCapabilitiesInfo | null
  target_specific_params?: Record<string, unknown> | null
  /** Inner targets for composite targets like RoundRobinTarget. */
  inner_targets?: TargetInstance[] | null
  /** ComponentIdentifier content hash. Targets with the same hash resolve to the
   *  same backend configuration and are treated as duplicates for RoundRobinTarget grouping. */
  identifier_hash?: string | null
}

export interface TargetListResponse {
  items: TargetInstance[]
  pagination: PaginationInfo
}

export interface CreateTargetRequest {
  type: string
  params: Record<string, unknown>
  auth_mode?: 'api_key' | 'entra'
}

// --- Converters ---

export interface ConverterInstance {
  converter_id: string
  converter_type: string
  display_name?: string | null
  supported_input_types: string[]
  supported_output_types: string[]
  converter_specific_params?: Record<string, unknown> | null
  sub_converter_ids?: string[] | null
}

export interface ConverterListResponse {
  items: ConverterInstance[]
}

export interface ConverterParameterSchema {
  name: string
  type_name: string
  required: boolean
  default_value?: string | null
  choices?: string[] | null
  description?: string | null
}

export interface ConverterCatalogEntry {
  converter_type: string
  supported_input_types: string[]
  supported_output_types: string[]
  parameters: ConverterParameterSchema[]
  is_llm_based: boolean
  description?: string | null
}

export interface ConverterCatalogResponse {
  items: ConverterCatalogEntry[]
}

// --- Attacks ---

export interface TargetInfo {
  target_type: string
  endpoint?: string | null
  model_name?: string | null
}

export interface AttackSummary {
  attack_result_id: string
  conversation_id: string
  attack_type: string
  attack_specific_params?: Record<string, unknown> | null
  target?: TargetInfo | null
  converters: string[]
  outcome?: 'undetermined' | 'success' | 'failure' | 'error' | null
  last_message_preview?: string | null
  message_count: number
  related_conversation_ids: string[]
  labels: Record<string, string>
  created_at: string
  updated_at: string
}

export interface CreateAttackRequest {
  target_registry_name: string
  name?: string
  labels?: Record<string, string>
  source_conversation_id?: string
  cutoff_index?: number
  /**
   * Tree-UI V1.0 (per doc/gui/design/01_tree_primitives.md §7 + §9.4.4 (a)):
   * the runner sends per-leaf clean-prefix history here as one bulk insert,
   * avoiding the N round-trip cost of using `add_message` for context turns.
   * Backend caps the list at 200 messages; the runner short-circuits before
   * dispatch if the resolved clean prefix would exceed the cap.
   */
  prepended_conversation?: PrependedMessageRequest[]
}

export interface CreateAttackResponse {
  attack_result_id: string
  conversation_id: string
  created_at: string
}

// --- Messages ---

export interface BackendScore {
  id: string
  scorer_type: string
  score_type: string
  score_value: string
  score_category?: string[] | null
  score_rationale?: string | null
  timestamp: string
}

export interface BackendMessagePiece {
  id: string
  original_value_data_type: string
  converted_value_data_type: string
  original_value?: string | null
  original_value_url?: string | null
  original_value_mime_type?: string | null
  converted_value: string
  converted_value_url?: string | null
  converted_value_mime_type?: string | null
  original_filename?: string | null
  converted_filename?: string | null
  prompt_metadata?: Record<string, unknown> | null
  scores: BackendScore[]
  response_error: string // 'none' | 'blocked' | 'processing' | 'empty' | 'unknown'
  response_error_description?: string | null
  /**
   * Lineage-root piece id (per doc/gui/design/01_tree_primitives.md §9.4.4 (b)).
   * Defaults to the piece's own id for fresh pieces; preserved across
   * `Message.duplicate()` so descendants share the same lineage root. Required
   * on every PR2-or-newer payload (the field is `null` when the source piece
   * had no original_prompt_id, which never occurs for persisted pieces but
   * is the safe defensive shape).
   */
  original_prompt_id: string | null
  /**
   * Sequential converter pipeline applied to produce `converted_value`
   * (per doc/gui/design/01_tree_primitives.md §9.4.4 (b)). Empty list = no
   * converter applied (distinguishable from "field missing" by being present).
   * The tree-UI reload-reconstruction path (§9.4.1) and `Fan(axis='converter')`
   * variant-payload reconstruction (§9.3.1) both read this.
   */
  converter_identifiers: ComponentIdentifier[]
}

export interface BackendMessage {
  turn_number: number
  role: string
  message_pieces: BackendMessagePiece[]
  created_at: string
}

export interface ConversationMessagesResponse {
  conversation_id: string
  messages: BackendMessage[]
}

export interface MessagePieceRequest {
  data_type: string // 'text' | 'image_path' | 'audio_path' | 'video_path' | 'binary_path'
  original_value: string
  converted_value?: string
  mime_type?: string
  original_prompt_id?: string
  prompt_metadata?: Record<string, unknown>
}

/**
 * Frontend mirror of the backend's `ComponentIdentifier.model_dump()` wire shape
 * (per pyrit/models/identifiers/component_identifier.py). Used by the tree-UI
 * V1.0 to read each `BackendMessagePiece.converter_identifiers` entry; the
 * runner's `Fan(axis='converter')` variant-payload reconstruction (§9.3.1)
 * builds a `ConverterRef` from the (class_name, class_module, params) triple.
 *
 * `hash`, `pyrit_version`, `eval_hash`, and `children` are emitted by the
 * backend but are not consumed by V1.0 frontend code paths — declared optional
 * so the wire payload type-checks regardless of which optional fields are
 * populated, and so the V1.x additions don't require a frontend bump.
 */
export interface ComponentIdentifier {
  class_name: string
  class_module: string
  params: Record<string, unknown>
  hash?: string | null
  pyrit_version?: string
  eval_hash?: string | null
  children?: Record<string, ComponentIdentifier | ComponentIdentifier[]>
}

/**
 * Frontend mirror of the backend's `PrependedMessageRequest` wire shape (per
 * pyrit/backend/models/attacks.py). Used inside `CreateAttackRequest.prepended_conversation`
 * by the tree-UI V1.0 runner to inject clean-prefix history when creating a
 * per-leaf `AttackResult` (per doc/gui/design/03_runner.md §3.3 / §4.1).
 *
 * Multimodal turns bundle multiple pieces into one message; the backend caps
 * pieces per message at 50.
 */
export interface PrependedMessageRequest {
  role: 'user' | 'assistant' | 'system' | 'simulated_assistant'
  pieces: MessagePieceRequest[]
}

export interface AddMessageRequest {
  role: string
  pieces: MessagePieceRequest[]
  send: boolean
  target_registry_name?: string
  converter_ids?: string[]
  target_conversation_id: string
  labels?: Record<string, string>
}

export interface AddMessageResponse {
  attack: AttackSummary
  messages: ConversationMessagesResponse
}

export interface AttackListResponse {
  items: AttackSummary[]
  pagination: PaginationInfo
}

// --- Conversations ---

export interface ConversationSummary {
  conversation_id: string
  message_count: number
  last_message_preview?: string | null
  created_at?: string | null
}

export interface AttackConversationsResponse {
  attack_result_id: string
  main_conversation_id: string
  conversations: ConversationSummary[]
}


export interface CreateConversationRequest {
  source_conversation_id?: string
  cutoff_index?: number
}

export interface CreateConversationResponse {
  conversation_id: string
  created_at: string
}

export interface ChangeMainConversationResponse {
  attack_result_id: string
  conversation_id: string
}
