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

export interface MessageTextDisplayPiece {
  type: 'text'
  pieceId: string
  pieceIndex: number
  content: string
  scores?: DisplayScore[]
}

export interface MessageMediaDisplayPiece {
  type: 'media'
  pieceId: string
  pieceIndex: number
  /**
   * Renderable media for this piece. Absent when the backend piece carries no
   * usable media value (e.g. an empty or blocked response) but still has
   * scores to present — such pieces must never enter copy/download/export
   * paths, so they deliberately have no attachment.
   */
  attachment?: MessageAttachment
  scores?: DisplayScore[]
}

export type MessageDisplayPiece = MessageTextDisplayPiece | MessageMediaDisplayPiece

export interface Message {
  role: 'user' | 'assistant' | 'simulated_assistant' | 'system'
  content: string
  timestamp: string
  /**
   * Legacy scores for messages created directly by the frontend. Backend
   * messages keep scores on their corresponding `displayPieces` entry.
   */
  scores?: DisplayScore[]
  attachments?: MessageAttachment[]
  /** Converted text and media pieces in backend order, with piece-local scores. */
  displayPieces?: MessageDisplayPiece[]
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

export interface ConfigurationFileContent {
  content: string
  source: string
  version: string
}

export interface UpdateConfigurationFileRequest {
  content: string
  version: string
}

export interface UpdateEnvironmentFileRequest {
  content: string
  version: string
}

export interface EnvironmentFileContent {
  id: string
  name: string
  path: string
  content: string
  exists: boolean
  version?: string | null
  read_only?: boolean
  read_only_reason?: string | null
}

export interface AuthAccess {
  isAdmin: boolean
}

export interface EnvironmentFileListResponse {
  items: EnvironmentFileContent[]
}

// --- Targets ---

export interface TargetCapabilities {
  supports_multi_turn: boolean
  supports_multi_message_pieces?: boolean
  supports_json_schema: boolean
  supports_json_output: boolean
  supports_editable_history?: boolean
  supports_system_prompt: boolean
  supports_streaming_audio?: boolean
  supported_input_modalities: string[]
  supported_output_modalities: string[]
}

export interface TargetIdentifier {
  class_name: string
  class_module?: string
  hash: string
  pyrit_version?: string
  endpoint?: string | null
  model_name?: string | null
  underlying_model_name?: string | null
  temperature?: number | null
  top_p?: number | null
  max_requests_per_minute?: number | null
  // Promoted + target-specific constructor params are inlined at the top level;
  // inner target identifiers live under `__children__`.
  [key: string]: unknown
}

export interface TargetInstance {
  target_registry_name: string
  /** Typed identity: class name, endpoint, model name, generation params, content hash. */
  identifier: TargetIdentifier
  capabilities?: TargetCapabilities | null
  /** Non-promoted constructor params, curated for display (e.g., RoundRobin weights). */
  target_specific_params?: Record<string, unknown> | null
  /** Inner targets for composite targets like RoundRobinTarget. */
  inner_targets?: TargetInstance[] | null
}

export interface TargetListResponse {
  items: TargetInstance[]
  pagination: PaginationInfo
}

export interface CreateTargetRequest {
  type: string
  params: Record<string, unknown>
  auth_mode?: 'api_key' | 'identity'
}

// --- Initializers ---

export interface RegisteredInitializer {
  initializer_name: string
  initializer_type: string
  description: string
  required_env_vars: string[]
  supported_parameters: Parameter[]
}

/** A read-only initializer invocation from the active `.pyrit_conf`. */
export interface ConfiguredInitializerSetting {
  initializer_name: string
  parameters?: Record<string, unknown> | null
  order_index: number
}

export interface InitializerSettingsResponse {
  /** Read-only initializers from the active `.pyrit_conf`, in run order. */
  configured: ConfiguredInitializerSetting[]
}

export interface ListRegisteredInitializersResponse {
  items: RegisteredInitializer[]
  pagination: PaginationInfo
}

export interface RegisterInitializerRequest {
  name: string
  script_content: string
}

export interface CustomInitializer {
  initializer_name: string
  script_content: string
  source: string
}

export interface CustomInitializerListResponse {
  source: string
  items: CustomInitializer[]
}

// --- Converters ---

export interface ConverterIdentifier {
  class_name: string
  class_module: string
  hash: string
  pyrit_version: string
  supported_input_types?: string[] | null
  supported_output_types?: string[] | null
  // Converter-specific constructor params are inlined at the top level.
  [key: string]: unknown
}

export interface ConverterInstance {
  converter_id: string
  identifier: ConverterIdentifier
}

export interface ConverterListResponse {
  items: ConverterInstance[]
}

export interface Parameter {
  name: string
  type_name: string
  required: boolean
  /** Scalar default renders as a display string; a list default renders as a list of display strings. */
  default?: string | string[] | null
  choices?: string[] | null
  is_list?: boolean
  description?: string | null
}

export interface ConverterCatalogEntry {
  converter_type: string
  supported_input_types: string[]
  supported_output_types: string[]
  parameters: Parameter[]
  is_llm_based: boolean
  description?: string | null
}

export interface ConverterCatalogResponse {
  items: ConverterCatalogEntry[]
}

export interface TargetCatalogEntry {
  target_type: string
  parameters: Parameter[]
  supported_auth_modes: ('api_key' | 'identity')[]
  description?: string | null
}

export interface TargetCatalogResponse {
  items: TargetCatalogEntry[]
}

// --- Attacks ---

export interface TargetInfo {
  target_type: string
  target_registry_name?: string | null
  endpoint?: string | null
  model_name?: string | null
  identifier_hash: string
}

export type AttackTargetResolutionStatus =
  | 'idle'
  | 'loading'
  | 'resolved'
  | 'explicit-mismatch'
  | 'unavailable'
  | 'ambiguous'
  | 'error'
  | 'legacy'

export interface AttackSummary {
  attack_result_id: string
  conversation_id: string
  attack_type: string
  attack_specific_params?: Record<string, unknown> | null
  objective: string
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
  system_prompt?: string
  prepended_conversation?: PrependedMessageRequest[]
}

export interface CreateAttackResponse {
  attack_result_id: string
  conversation_id: string
  created_at: string
}

// --- Messages ---

/** ScoreView payload returned by the backend. */
export interface BackendScore {
  id: string
  message_piece_id: string
  scorer_type: string
  score_type: string
  score_value?: string | null
  status?: string
  is_objective_score?: boolean
  score_category?: string[] | null
  score_rationale?: string | null
  timestamp: string
}

/** Score enriched with message-piece presentation fields for transcript rendering. */
export interface DisplayScore extends BackendScore {
  pieceIndex: number
  pieceType: string
  sourceLabel: string
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

export interface PrependedMessageRequest {
  role: string // 'system' | 'user' | 'assistant'
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

// --- Scenarios ---

export interface RegisteredScenario {
  scenario_name: string
  scenario_type: string
  scenario_version: number
  description: string
  description_markdown: string
  default_technique: string
  default_techniques: string[]
  aggregate_techniques: string[]
  aggregate_technique_expansions: Record<string, string[]>
  all_techniques: string[]
  technique_summaries: ScenarioTechniqueSummary[]
  default_datasets: string[]
  baseline_policy: 'enabled' | 'disabled' | 'forbidden'
  include_baseline_by_default: boolean
  supported_parameters: Parameter[]
  default_run_size: ScenarioRunSizeEstimateResponse
}

export interface ScenarioTechniqueSummary {
  name: string
  description: string | null
  tags: string[]
}

export interface ListRegisteredScenariosResponse {
  items: RegisteredScenario[]
  pagination: PaginationInfo
}

export interface RunScenarioRequest {
  scenario_name: string
  target_name: string
  initializers?: string[] | null
  techniques?: string[] | null
  dataset_names?: string[] | null
  max_dataset_size?: number | null
  dataset_filters?: Record<string, string[]> | null
  max_concurrency?: number
  max_retries?: number
  include_baseline?: boolean | null
  labels?: Record<string, string> | null
  scenario_params?: Record<string, unknown> | null
  initializer_args?: Record<string, Record<string, unknown>> | null
  scenario_result_id?: string | null
}

export interface ScenarioRunSizeComponent {
  label: string
  count: number
  is_baseline: boolean
  note: string | null
}

export interface ScenarioDatasetSizeCap {
  label: string
  count: number
  configured_on: 'dataset' | 'configuration' | 'compound'
  dataset_name: string | null
}

export interface ScenarioDatasetSummary {
  name: string
  kind: 'dataset' | 'synthesized'
  logical_seed_group_count: number
  selected_seed_group_count: number
  configured_caps: ScenarioDatasetSizeCap[]
  selection_note: string | null
}

export interface ScenarioRunSizeEstimateResponse {
  estimated_attack_count: number | null
  minimum_attack_count?: number | null
  maximum_attack_count?: number | null
  components: ScenarioRunSizeComponent[]
  datasets: ScenarioDatasetSummary[]
  effective_parameters?: Record<string, boolean | number | string | string[]>
  note: string | null
}

export interface ScenarioRunSizeEstimateRequest {
  target_name?: string | null
  techniques?: string[] | null
  dataset_names?: string[] | null
  max_dataset_size?: number | null
  dataset_filters?: Record<string, string[]> | null
  include_baseline?: boolean | null
  scenario_params?: Record<string, unknown> | null
}

export interface ScenarioRunEstimateComponent {
  id: string
  label: string
  count: number
  isBaseline: boolean
  note: string | null
}

export interface ScenarioRunEstimateDatasetCap {
  id: string
  label: string
  count: number
  configuredOn: 'dataset' | 'configuration' | 'compound'
  datasetName: string | null
}

export interface ScenarioRunEstimateDataset {
  id: string
  name: string
  kind: 'dataset' | 'synthesized'
  logicalSeedGroupCount: number
  selectedSeedGroupCount: number
  configuredCaps: ScenarioRunEstimateDatasetCap[]
  selectionNote: string | null
}

export interface ScenarioRunEstimate {
  scope: 'default' | 'request'
  total: number | null
  minimum?: number | null
  maximum?: number | null
  components: ScenarioRunEstimateComponent[]
  datasets: ScenarioRunEstimateDataset[]
  effectiveParameters: Record<string, boolean | number | string | string[]>
  note: string | null
}

export type ScenarioRunEstimateResult =
  | {
      status: 'available'
      estimate: ScenarioRunEstimate
    }
  | {
      status: 'conditional'
      estimate: ScenarioRunEstimate
    }
  | {
      status: 'unavailable'
      scope: 'default' | 'request'
      label: string
      note?: string
    }

export type ScenarioRunEstimateState =
  | {
      status: 'loading'
      scope: 'default' | 'request'
    }
  | {
      status: 'refreshing'
      estimate: ScenarioRunEstimate
      label: string
    }
  | {
      status: 'stale'
      estimate: ScenarioRunEstimate
      label: string
      error: string
    }
  | ScenarioRunEstimateResult

export type ScenarioRunEstimator = (
  scenarioName: string,
  request: ScenarioRunSizeEstimateRequest,
  signal?: AbortSignal,
) => Promise<ScenarioRunSizeEstimateResponse>

export interface AttackErrorSummary {
  atomic_attack_name: string
  objective: string
  error_type?: string | null
  error_message?: string | null
  total_retries: number
}

export interface RetryEvent {
  timestamp: string
  attempt_number: number
  function_name: string
  exception_type: string
  exception_message: string
  component_role: string
  component_name?: string | null
  endpoint?: string | null
  elapsed_seconds: number
}

export interface AttackRetrySummary {
  attack_result_id: string
  atomic_attack_name: string
  retries: RetryEvent[]
}

export type ScenarioRunState = 'CREATED' | 'QUEUED' | 'IN_PROGRESS' | 'COMPLETED' | 'FAILED' | 'CANCELLED'

export interface ScenarioRunSummary {
  scenario_result_id: string
  scenario_name: string
  scenario_registry_name?: string | null
  scenario_version: number
  status: ScenarioRunState
  created_at: string
  updated_at: string
  error?: string | null
  error_type?: string | null
  techniques_used: string[]
  total_attacks: number
  completed_attacks: number
  objective_achieved_rate: number
  failed_attacks: AttackErrorSummary[]
  attack_retries: AttackRetrySummary[]
  total_retries: number
  labels: Record<string, string>
  completed_at?: string | null
  pyrit_version?: string | null
  target?: ScenarioTargetSummary | null
  datasets_used?: string[]
  scenario_parameters?: Record<string, unknown>
  planned_total_available?: boolean
  successful_attacks?: number
  error_attacks?: number
  attack_details_available?: boolean
}

export interface ScenarioTargetSummary {
  target_type: string
  endpoint?: string | null
  model_name?: string | null
  identifier_hash?: string | null
}

export interface ScenarioRunListItem {
  scenario_result_id: string
  scenario_name: string
  scenario_registry_name?: string | null
  scenario_version: number
  status: ScenarioRunState
  created_at: string
  updated_at: string
  error?: string | null
  error_type?: string | null
  techniques_used: string[]
  total_attacks: number | null
  completed_attacks: number
  objective_achieved_rate: number
  total_retries: number
  labels: Record<string, string>
  completed_at?: string | null
  pyrit_version?: string | null
  target?: ScenarioTargetSummary | null
  datasets_used: string[]
  scenario_parameters: Record<string, unknown>
  planned_total_available: boolean
  successful_attacks: number
  error_attacks: number
  attack_details_available: boolean
}

export interface ScenarioRunListResponse {
  items: ScenarioRunListItem[]
  pagination: PaginationInfo
}

/** Compact persisted run header returned by the progress endpoint. */
export interface ScenarioProgressHeader {
  scenario_result_id: string
  scenario_name: string
  scenario_registry_name?: string | null
  scenario_version: number
  status: ScenarioRunState
  created_at: string
  completed_at?: string | null
  pyrit_version?: string | null
  target?: ScenarioTargetSummary | null
  techniques_used?: string[]
  datasets_used?: string[]
  scenario_parameters?: Record<string, unknown>
  labels?: Record<string, string>
}

/** One persisted attack attempt in ascending progress order. */
export interface ScenarioProgressScore {
  scorer_name: string
  score_type: 'true_false' | 'float_scale' | 'unknown'
  status: 'complete' | 'undetermined'
  score_value?: string | null
  score_rationale?: string | null
}

export type ScenarioIdentityValue =
  | string
  | number
  | boolean
  | null
  | ScenarioIdentityValue[]
  | { [key: string]: ScenarioIdentityValue }

export interface ScenarioComponentIdentity {
  component_name: string
  parameters: Record<string, ScenarioIdentityValue>
  children: Record<string, ScenarioComponentIdentity[]>
}

export type ScenarioAttackTechniqueDetails = ScenarioComponentIdentity

export interface ScenarioProgressResult {
  attack_result_id: string
  conversation_id: string
  atomic_group_id: string
  atomic_attack_name: string
  seed_group_id: string
  outcome: 'success' | 'failure' | 'error' | 'undetermined'
  execution_time_ms: number
  timestamp: string
  total_retries: number
  retries: RetryEvent[]
  error_type?: string | null
  error_message?: string | null
  score?: ScenarioProgressScore | null
}

export interface ScenarioRunPlanSeedGroup {
  id: string
  objective_sha256: string
  objective: string
  prompts: ScenarioRunPlanSeedPrompt[]
}

export interface ScenarioRunPlanSeedPrompt {
  value: string
  data_type?: string | null
  role?: string | null
  sequence: number
  parameters: string[]
}

export interface ScenarioRunPlanAtomicGroup {
  id: string
  atomic_attack_name: string
  display_group: string
  technique_name?: string | null
  technique_eval_hash: string
  seed_group_ids: string[]
  description?: string | null
  tags: string[]
}

export interface ScenarioRunPlan {
  version: 1
  scenario_registry_name?: string | null
  atomic_groups: ScenarioRunPlanAtomicGroup[]
  seed_groups: ScenarioRunPlanSeedGroup[]
}

export interface ScenarioProgressCounts {
  completed: number
  planned: number | null
  succeeded: number
  success_percentage: number | null
  errors: number
  retries: number
}

export interface ScenarioTechniqueProgress extends ScenarioProgressCounts {
  id: string
  display_group: string
  atomic_attack_names: string[]
  atomic_group_ids: string[]
  description?: string | null
  tags: string[]
}

export interface ScenarioDisplayGroupProgress extends ScenarioProgressCounts {
  id: string
  display_group: string
  atomic_attack_names: string[]
  atomic_group_ids: string[]
}

export interface ScenarioSeedGroupProgress extends ScenarioProgressCounts {
  id: string
  objective?: string | null
}

export interface ScenarioAtomicGroupProgress extends ScenarioProgressCounts {
  id: string
  atomic_attack_name: string
  display_group: string
  status: 'RUNNING' | 'PENDING' | 'INCOMPLETE' | 'COMPLETED'
  technique_details?: ScenarioAttackTechniqueDetails | null
}

export interface ScenarioObjectiveScorerMetrics {
  accuracy: number
  accuracy_standard_error?: number | null
  f1_score?: number | null
  precision?: number | null
  recall?: number | null
  average_score_time_seconds?: number | null
}

export type ScenarioScorerIdentity = ScenarioComponentIdentity

export interface ScenarioObjectiveScorer extends ScenarioScorerIdentity {
  metrics?: ScenarioObjectiveScorerMetrics | null
}

export interface ScenarioProgressSummary {
  overall: ScenarioProgressCounts
  objective_scorer?: ScenarioObjectiveScorer | null
  display_groups?: ScenarioDisplayGroupProgress[]
  techniques: ScenarioTechniqueProgress[]
  seed_groups: ScenarioSeedGroupProgress[]
  atomic_groups: ScenarioAtomicGroupProgress[]
  unattributed_attempts?: number
}

export interface ScenarioRunProgress {
  run: ScenarioProgressHeader
  plan: ScenarioRunPlan | null
  results: ScenarioProgressResult[]
  summary: ScenarioProgressSummary
  next_cursor?: string | null
  has_more: boolean
  plan_complete: boolean
}
