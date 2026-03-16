// ============================================================================
// Frontend UI Types
// ============================================================================

export interface MessageAttachment {
  type: 'image' | 'audio' | 'video' | 'file'
  name: string
  url: string
  mimeType: string
  size: number
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

export interface TargetInstance {
  target_registry_name: string
  target_type: string
  endpoint?: string | null
  model_name?: string | null
  temperature?: number | null
  top_p?: number | null
  max_requests_per_minute?: number | null
  supports_multi_turn?: boolean
  target_specific_params?: Record<string, unknown> | null
}

export interface TargetListResponse {
  items: TargetInstance[]
  pagination: PaginationInfo
}

export interface CreateTargetRequest {
  type: string
  params: Record<string, unknown>
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
  outcome?: 'undetermined' | 'success' | 'failure' | null
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
}

export interface CreateAttackResponse {
  attack_result_id: string
  conversation_id: string
  created_at: string
}

// --- Messages ---

export interface BackendScore {
  score_id: string
  scorer_type: string
  score_type: string
  score_value: string
  score_category?: string[] | null
  score_rationale?: string | null
  scored_at: string
}

export interface BackendMessagePiece {
  piece_id: string
  original_value_data_type: string
  converted_value_data_type: string
  original_value?: string | null
  original_value_mime_type?: string | null
  converted_value: string
  converted_value_mime_type?: string | null
  original_filename?: string | null
  converted_filename?: string | null
  prompt_metadata?: Record<string, unknown> | null
  scores: BackendScore[]
  response_error: string // 'none' | 'blocked' | 'processing' | 'empty' | 'unknown'
  response_error_description?: string | null
}

export interface BackendMessage {
  turn_number: number
  role: string
  pieces: BackendMessagePiece[]
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
