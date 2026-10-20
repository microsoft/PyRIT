import axios, { type InternalAxiosRequestConfig } from 'axios'
import { InteractionRequiredAuthError, type PublicClientApplication } from '@azure/msal-browser'
import { generateClientId } from '@/utils/clientId'
import { toApiError } from './errors'
import { compatibility, COMPATIBILITY_HEADER } from './compatibility'
import { getGraphScopes } from '../auth/msalConfig'
import type {
  TargetInstance,
  UnregisteredTarget,
  UnregisteredConverter,
  SourceInstanceSpec,
  TargetListResponse,
  TargetTypeListResponse,
  ConverterTypeListResponse,
  ConverterInstance,
  ConverterListResponse,
  ConverterPreviewRequest,
  ConverterPreviewResponse,
  CreateConverterRequest,
  CreateTechniqueRequest,
  TechniqueInstance,
  TechniqueListResponse,
  TechniqueTypeResponse,
  CreateTargetRequest,
  InitializerSettingsResponse,
  ListRegisteredInitializersResponse,
  CustomInitializerListResponse,
  RegisterInitializerRequest,
  CreateAttackRequest,
  LabelOptionsResponse,
  CreateAttackResponse,
  AttackSummary,
  AttackListResponse,
  DatasetListResponse,
  SeedExampleDetailResponse,
  SeedExampleListParams,
  SeedExampleListResponse,
  ConversationMessagesResponse,
  AddMessageRequest,
  AddMessageResponse,
  MessageSendRequest,
  MessageSendStatus,
  AttackConversationsResponse,
  CreateConversationRequest,
  CreateConversationResponse,
  ChangeMainConversationResponse,
  ListRegisteredScenariosResponse,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioRunSizeEstimateResponse,
  ScenarioRunSizeEstimateRequest,
  ScenarioDatasetLoadResponse,
  ScenarioRunSummary,
  ScenarioRunListResponse,
  ScenarioRunProgress,
  PairedOutcomeStatistics,
  ScenarioQueueSnapshot,
  ScenarioRunState,
  ConfigurationFileContent,
  EnvironmentFileContent,
  UpdateEnvironmentFileRequest,
  EnvironmentFileListResponse,
  UpdateConfigurationFileRequest,
  AuthAccess,
  BackendScore,
  ManualScoreRequest,
  UpdateAttackRequest,
  SaveConversationRequest,
  Finding,
  FindingCreate,
  FindingCreateRequest,
  FindingListResponse,
  FindingOptionsResponse,
  FindingEvidenceCreateRequest,
  FindingEvidenceAttachResponse,
  FindingEvidenceListResponse,
  Operation,
  OperationCreate,
  OperationListResponse,
  OperationUpdate,
  AttackAnalyticsQuery,
  AttackAnalyticsReport,
  AttackAnalyticsResultsQuery,
  AttackAnalyticsResults,
  AttackAnalyticsFacetQuery,
  AttackAnalyticsFacets,
} from '../types'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

const operationPath = (operationId: string): string => `/operations/${encodeURIComponent(operationId)}`

export const operationsApi = {
  getFindingOptions: async (): Promise<FindingOptionsResponse> => {
    return (await apiClient.get('/operations/finding-options')).data
  },
  list: async (): Promise<OperationListResponse> => {
    return (await apiClient.get('/operations')).data
  },
  get: async (operationId: string): Promise<Operation> => {
    return (await apiClient.get(operationPath(operationId))).data
  },
  create: async (request: OperationCreate): Promise<Operation> => {
    return (await apiClient.post('/operations', request)).data
  },
  update: async (operationId: string, request: OperationUpdate): Promise<Operation> => {
    return (await apiClient.patch(operationPath(operationId), request)).data
  },
  listFindings: async (
    operationId: string, params: { limit: number; offset: number; title?: string },
  ): Promise<FindingListResponse> => {
    return (await apiClient.get(`${operationPath(operationId)}/findings`, { params })).data
  },
  createFinding: async (operationId: string, request: FindingCreateRequest): Promise<Finding> => {
    return (await apiClient.post(`${operationPath(operationId)}/findings`, request)).data
  },
  updateFinding: async (operationId: string, findingId: string, request: FindingCreate): Promise<Finding> => {
    return (await apiClient.put(`${operationPath(operationId)}/findings/${encodeURIComponent(findingId)}`, request)).data
  },
  deleteFinding: async (operationId: string, findingId: string): Promise<void> => {
    await apiClient.delete(`${operationPath(operationId)}/findings/${encodeURIComponent(findingId)}`)
  },
  attachFindingEvidence: async (
    operationId: string, findingId: string, request: FindingEvidenceCreateRequest,
  ): Promise<FindingEvidenceAttachResponse> => {
    return (await apiClient.post(
      `${operationPath(operationId)}/findings/${encodeURIComponent(findingId)}/evidence`, request,
    )).data
  },
  listFindingEvidence: async (
    operationId: string, findingId: string, params: { limit: number; offset: number },
  ): Promise<FindingEvidenceListResponse> => {
    return (await apiClient.get(
      `${operationPath(operationId)}/findings/${encodeURIComponent(findingId)}/evidence`, { params },
    )).data
  },
  detachFindingEvidence: async (operationId: string, findingId: string, evidenceId: string): Promise<void> => {
    await apiClient.delete(
      `${operationPath(operationId)}/findings/${encodeURIComponent(findingId)}/evidence/${encodeURIComponent(evidenceId)}`,
    )
  },
}

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 5 * 60 * 1000, // 5 minutes – video generation can take a while
})

// ---------------------------------------------------------------------------
// MSAL token acquisition for API calls
// ---------------------------------------------------------------------------

let _msalInstance: PublicClientApplication | null = null

export function setMsalInstance(instance: PublicClientApplication | null): void {
  _msalInstance = instance
}

async function getAccessToken(forceRefresh = false): Promise<string | null> {
  if (!_msalInstance) return null

  const account = _msalInstance.getActiveAccount()
  if (!account) return null

  try {
    const response = await _msalInstance.acquireTokenSilent({
      scopes: getGraphScopes(),
      account,
      forceRefresh,
    })
    return response.accessToken
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await _msalInstance.acquireTokenRedirect({
        scopes: getGraphScopes(),
      })
    }
    return null
  }
}

function isCompatibilityNeutral(config: InternalAxiosRequestConfig): boolean {
  const origin = window.location.origin
  const basePath = new URL(API_BASE_URL, origin).pathname.replace(/\/$/, '')
  let path = new URL(apiClient.getUri(config), origin).pathname
  if (basePath && path.startsWith(`${basePath}/`)) path = path.slice(basePath.length)
  return ['/health', '/auth/config', '/version', '/media'].includes(path)
}

apiClient.interceptors.request.use(async (config) => {
  const businessRequest = !isCompatibilityNeutral(config)
  if (businessRequest) compatibility.assertReady()
  config.headers.set('X-Request-ID', generateClientId())

  const token = await getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }

  if (businessRequest) {
    compatibility.assertReady()
    config.headers.set(COMPATIBILITY_HEADER, compatibility.bundledId)
  }

  return config
})

// ---------------------------------------------------------------------------
// Response interceptor: retry once on 401 with forced token refresh
// ---------------------------------------------------------------------------

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const problem = error?.response?.data
    if (
      (error?.response?.status === 400 && problem?.type === 'urn:pyrit:compatibility:invalid') ||
      (error?.response?.status === 409 && problem?.type === 'urn:pyrit:compatibility:mismatch')
    ) {
      compatibility.block('The backend rejected this frontend build identity.', problem.expected, problem.actual)
      return Promise.reject(error)
    }
    const originalRequest = error?.config
    if (
      error?.response?.status === 401 && originalRequest && !originalRequest._retried
      && !originalRequest.url?.endsWith('/message-sends')
    ) {
      originalRequest._retried = true
      const freshToken = await getAccessToken(true)
      if (freshToken) {
        originalRequest.headers.set('Authorization', `Bearer ${freshToken}`)
        return apiClient(originalRequest)
      }
    }

    const apiError = toApiError(error)
    const method = error?.config?.method?.toUpperCase() ?? '?'
    const url = error?.config?.url ?? '?'
    const requestId = error?.config?.headers?.['X-Request-ID'] ?? ''

    console.error(
      `[API] ${method} ${url} failed | status=${apiError.status ?? 'N/A'} | ` +
        `requestId=${requestId} | ${apiError.detail}`
    )

    return Promise.reject(error)
  }
)

export { apiClient }

export const analyticsApi = {
  query: async (
    query: AttackAnalyticsQuery,
    signal?: AbortSignal,
  ): Promise<AttackAnalyticsReport> => {
    const response = await apiClient.post<AttackAnalyticsReport>('/analytics/attacks/query', query, { signal })
    return response.data
  },
  results: async (
    query: AttackAnalyticsResultsQuery,
    signal?: AbortSignal,
  ): Promise<AttackAnalyticsResults> => {
    const response = await apiClient.post<AttackAnalyticsResults>('/analytics/attacks/results', query, { signal })
    return response.data
  },
  facets: async (
    query: AttackAnalyticsFacetQuery,
    signal?: AbortSignal,
  ): Promise<AttackAnalyticsFacets> => {
    const response = await apiClient.post<AttackAnalyticsFacets>('/analytics/attacks/facets', query, { signal })
    return response.data
  },
}

export const healthApi = {
  checkHealth: async () => {
    const response = await apiClient.get('/health')
    return response.data
  },
}

export const versionApi = {
  getVersion: async () => {
    const response = await apiClient.get('/version')
    return response.data
  },
}

export const authApi = {
  getAccess: async (): Promise<AuthAccess> => {
    const response = await apiClient.get('/auth/access')
    return response.data
  },
}

export const configurationApi = {
  getRuntimeStatus: async (): Promise<RuntimeStatus> => {
    return (await apiClient.get('/config/runtime')).data
  },
  reinitialize: async (version: string): Promise<RuntimeStatus> => {
    return (await apiClient.post('/config/runtime/apply', { version })).data
  },
  getContent: async (): Promise<ConfigurationFileContent> => {
    const response = await apiClient.get('/config')
    return response.data
  },

  updateContent: async (request: UpdateConfigurationFileRequest): Promise<ConfigurationFileContent> => {
    const response = await apiClient.put('/config', request)
    return response.data
  },

  listEnvironmentFiles: async (): Promise<EnvironmentFileListResponse> => {
    const response = await apiClient.get('/config/env-files')
    return response.data
  },

  getEnvironmentFile: async (fileId: string): Promise<EnvironmentFileContent> => {
    const response = await apiClient.get(`/config/env-files/${encodeURIComponent(fileId)}`)
    return response.data
  },

  updateEnvironmentFile: async (
    fileId: string,
    request: UpdateEnvironmentFileRequest,
  ): Promise<EnvironmentFileContent> => {
    const response = await apiClient.put(`/config/env-files/${encodeURIComponent(fileId)}`, request)
    return response.data
  },
}

export const runtimeApi = {
  getReadiness: async (): Promise<RuntimeReadiness> => {
    return (await apiClient.get('/runtime')).data
  },
}

export const targetsApi = {
  listTargetTypes: async (): Promise<TargetTypeListResponse> => {
    const response = await apiClient.get('/targets/types')
    return response.data
  },

  listTargets: async (limit = 50, cursor?: string): Promise<TargetListResponse> => {
    const params: Record<string, string | number> = { limit }
    if (cursor) params.cursor = cursor
    const response = await apiClient.get('/targets', { params })
    return response.data
  },

  getTarget: async (targetRegistryName: string): Promise<TargetInstance> => {
    const response = await apiClient.get(`/targets/${encodeURIComponent(targetRegistryName)}`)
    return response.data
  },

  createTarget: async (request: CreateTargetRequest): Promise<TargetInstance> => {
    const response = await apiClient.post('/targets', request)
    return response.data
  },
  buildTarget: async (type: string, source: SourceInstanceSpec): Promise<UnregisteredTarget> => {
    const response = await apiClient.post<UnregisteredTarget>('/targets', { type, source, register: false })
    return response.data
  },
}

export const techniquesApi = {
  listTechniques: async (limit = 50, cursor?: string): Promise<TechniqueListResponse> => {
    const params: Record<string, string | number> = { limit }
    if (cursor) params.cursor = cursor
    return (await apiClient.get('/techniques', { params })).data
  },
  listTypes: async (): Promise<TechniqueTypeResponse> =>
    (await apiClient.get('/techniques/types')).data,
  createTechnique: async (request: CreateTechniqueRequest): Promise<TechniqueInstance> =>
    (await apiClient.post('/techniques', request)).data,
}

export const convertersApi = {
  listConverterTypes: async (): Promise<ConverterTypeListResponse> => {
    const response = await apiClient.get('/converters/types')
    return response.data
  },

  listConverters: async (): Promise<ConverterListResponse> => {
    const response = await apiClient.get('/converters')
    return response.data
  },

  getConverter: async (converterId: string): Promise<ConverterInstance> => {
    const response = await apiClient.get(`/converters/${encodeURIComponent(converterId)}`)
    return response.data
  },

  createConverter: async (request: CreateConverterRequest): Promise<ConverterInstance> => {
    const response = await apiClient.post('/converters', request)
    return response.data
  },
  buildConverter: async (type: string, source: SourceInstanceSpec): Promise<UnregisteredConverter> => {
    const response = await apiClient.post<UnregisteredConverter>('/converters', { type, source, register: false })
    return response.data
  },

  deleteConverter: async (converterId: string): Promise<void> => {
    await apiClient.delete(`/converters/${encodeURIComponent(converterId)}`)
  },

  previewConversion: async (request: ConverterPreviewRequest): Promise<ConverterPreviewResponse> => {
    const response = await apiClient.post('/converters/preview', request)
    return response.data
  },
}

export const initializersApi = {
  getSettings: async (): Promise<InitializerSettingsResponse> => {
    const response = await apiClient.get('/initializers/settings')
    return response.data
  },

  listRegistered: async (): Promise<ListRegisteredInitializersResponse> => {
    const response = await apiClient.get('/initializers', { params: { limit: 200 } })
    return response.data
  },

  listCustom: async (): Promise<CustomInitializerListResponse> => {
    const response = await apiClient.get('/initializers/custom')
    return response.data
  },

  register: async (request: RegisterInitializerRequest): Promise<void> => {
    await apiClient.post('/initializers', request)
  },

  unregister: async (initializerName: string): Promise<void> => {
    await apiClient.delete(`/initializers/${encodeURIComponent(initializerName)}`)
  },

}

export const attacksApi = {
  saveConversation: async (request: SaveConversationRequest): Promise<AddMessageResponse> => {
    const response = await apiClient.post('/attacks/save-conversation', request)
    return response.data
  },
  createAttack: async (request: CreateAttackRequest): Promise<CreateAttackResponse> => {
    const response = await apiClient.post('/attacks', request)
    return response.data
  },

  getAttack: async (attackResultId: string): Promise<AttackSummary> => {
    const response = await apiClient.get(`/attacks/${encodeURIComponent(attackResultId)}`)
    return response.data
  },

  updateAttack: async (attackResultId: string, request: UpdateAttackRequest): Promise<AttackSummary> => {
    const response = await apiClient.patch(`/attacks/${encodeURIComponent(attackResultId)}`, request)
    return response.data
  },

  removeHumanScore: async (attackResultId: string): Promise<AttackSummary> => {
    const response = await apiClient.delete(
      `/attacks/${encodeURIComponent(attackResultId)}/human-score`
    )
    return response.data
  },

  getConversation: async (conversationId: string): Promise<AddMessageResponse> => {
    const response = await apiClient.get(`/attacks/conversations/${encodeURIComponent(conversationId)}`)
    return response.data
  },

  getMessages: async (attackResultId: string, conversationId: string): Promise<ConversationMessagesResponse> => {
    const response = await apiClient.get(
      `/attacks/${encodeURIComponent(attackResultId)}/messages`,
      { params: { conversation_id: conversationId } }
    )
    return response.data
  },

  addMessage: async (attackResultId: string, request: AddMessageRequest): Promise<AddMessageResponse> => {
    const response = await apiClient.post(
      `/attacks/${encodeURIComponent(attackResultId)}/messages`,
      request
    )
    return response.data
  },

  submitMessageSend: async (attackResultId: string, request: MessageSendRequest): Promise<MessageSendStatus> => {
    const response = await apiClient.post(
      `/attacks/${encodeURIComponent(attackResultId)}/message-sends`, request,
    )
    return response.data
  },

  getMessageSend: async (
    attackResultId: string, sendId: string, signal?: AbortSignal,
  ): Promise<MessageSendStatus> => {
    const response = await apiClient.get(
      `/attacks/${encodeURIComponent(attackResultId)}/message-sends/${encodeURIComponent(sendId)}`,
      { params: { wait_ms: 1000 }, signal },
    )
    return response.data
  },

  getConversations: async (attackResultId: string): Promise<AttackConversationsResponse> => {
    const response = await apiClient.get(
      `/attacks/${encodeURIComponent(attackResultId)}/conversations`
    )
    return response.data
  },

  createConversation: async (
    attackResultId: string,
    request: CreateConversationRequest
  ): Promise<CreateConversationResponse> => {
    const response = await apiClient.post(
      `/attacks/${encodeURIComponent(attackResultId)}/conversations`,
      request
    )
    return response.data
  },

  changeMainConversation: async (
    attackResultId: string,
    conversationId: string
  ): Promise<ChangeMainConversationResponse> => {
    const response = await apiClient.post(
      `/attacks/${encodeURIComponent(attackResultId)}/update-main-conversation`,
      { conversation_id: conversationId }
    )
    return response.data
  },

  listAttacks: async (params?: {
    limit?: number
    cursor?: string
    attack_types?: string[]
    converter_types?: string[]
    converter_types_match?: 'any' | 'all'
    has_converters?: boolean
    include_scenario_attacks?: boolean
    outcome?: string
    operator?: string[]
    operation?: string[]
    operation_id?: string[]
    label?: string[]
    min_turns?: number
    max_turns?: number
  }): Promise<AttackListResponse> => {
    const response = await apiClient.get('/attacks', {
      params,
      paramsSerializer: {
        indexes: null, // serialize arrays as ?key=val1&key=val2
      },
    })
    return response.data
  },

  getAttackOptions: async (): Promise<{ attack_types: string[] }> => {
    const response = await apiClient.get('/attacks/attack-options')
    return response.data
  },

  getConverterOptions: async (): Promise<{ converter_types: string[] }> => {
    const response = await apiClient.get('/attacks/converter-options')
    return response.data
  },
}

export const scoresApi = {
  createManualScore: async (request: ManualScoreRequest): Promise<BackendScore> => {
    const response = await apiClient.post('/scores/manual', request)
    return response.data
  },
}

export const labelsApi = {
  getLabels: async (
    source: 'attacks' | 'scenarios' = 'attacks',
    filters?: {
      operator?: string[]
      operation?: string[]
      label?: string[]
    },
  ): Promise<LabelOptionsResponse> => {
    const response = await apiClient.get('/labels', {
      params: { source, ...filters },
      paramsSerializer: {
        indexes: null, // serialize arrays as ?key=val1&key=val2
      },
    })
    return response.data
  },
}

export const scenariosApi = {
  loadDatasets: async (
    scenarioName: string, request: ScenarioRunSizeEstimateRequest,
  ): Promise<ScenarioDatasetLoadResponse> => {
    const response = await apiClient.post(
      `/scenarios/catalog/${encodeURIComponent(scenarioName)}/datasets/load`, request, { timeout: 300_000 },
    )
    return response.data
  },
  /**
   * Lists one page of the scenario catalog. Callers that need the full
   * catalog should follow `pagination.next_cursor` until `has_more` is false.
   */
  listCatalog: async (
    limit = 50,
    cursor?: string,
    includeEstimates = true,
  ): Promise<ListRegisteredScenariosResponse> => {
    const params: Record<string, string | number | boolean> = { limit }
    if (cursor) params.cursor = cursor
    if (!includeEstimates) params.include_estimates = false
    const response = await apiClient.get('/scenarios/catalog', { params })
    return response.data
  },

  getScenario: async (scenarioName: string): Promise<RegisteredScenario> => {
    // The backend route is a single `{scenario_name:path}` segment, so a dotted
    // or slash-bearing registry name (e.g. 'foundry/red_team_agent') must stay
    // a single encoded path segment — encodeURIComponent (not raw interpolation)
    // keeps '/' as '%2F', which the browser/Axios preserve and FastAPI's path
    // converter decodes back to the original name server-side.
    const response = await apiClient.get(`/scenarios/catalog/${encodeURIComponent(scenarioName)}`)
    return response.data
  },

  startRun: async (request: RunScenarioRequest): Promise<ScenarioRunSummary> => {
    const response = await apiClient.post('/scenarios/runs', request)
    return response.data
  },

  estimateRun: async (
    scenarioName: string,
    request: ScenarioRunSizeEstimateRequest,
    signal?: AbortSignal,
  ): Promise<ScenarioRunSizeEstimateResponse> => {
    const response = await apiClient.post(
      `/scenarios/catalog/${encodeURIComponent(scenarioName)}/estimate`,
      request,
      { signal },
    )
    return response.data
  },

  getRun: async (scenarioResultId: string): Promise<ScenarioRunSummary> => {
    const response = await apiClient.get(`/scenarios/runs/${encodeURIComponent(scenarioResultId)}`)
    return response.data
  },

  listRuns: async (params?: {
    limit?: number
    cursor?: string
    scenario_names?: string[]
    run_statuses?: ScenarioRunState[]
    label?: string[]
    operation_id?: string[]
  }): Promise<ScenarioRunListResponse> => {
    const response = await apiClient.get('/scenarios/runs', {
      params,
      paramsSerializer: {
        indexes: null,
      },
    })
    return response.data
  },

  getRunProgress: async (
    scenarioResultId: string,
    params?: { since?: string; limit?: number },
    signal?: AbortSignal,
  ): Promise<ScenarioRunProgress> => {
    const response = await apiClient.get(
      `/scenarios/runs/${encodeURIComponent(scenarioResultId)}/progress`,
      { params, signal },
    )
    return response.data
  },

  getPairedOutcomes: async (
    scenarioResultId: string,
    params: { baseline_group_id: string; attack_group_id: string },
    signal?: AbortSignal,
  ): Promise<PairedOutcomeStatistics> => {
    const response = await apiClient.get<PairedOutcomeStatistics>(
      `/scenarios/runs/${encodeURIComponent(scenarioResultId)}/paired-outcomes`,
      { params, signal },
    )
    return response.data
  },

  getQueue: async (signal?: AbortSignal): Promise<ScenarioQueueSnapshot> => {
    const response = await apiClient.get('/scenarios/runs/queue', { signal })
    return response.data
  },

  cancelRun: async (scenarioResultId: string, signal?: AbortSignal): Promise<ScenarioRunSummary> => {
    const response = await apiClient.post(
      `/scenarios/runs/${encodeURIComponent(scenarioResultId)}/cancel`,
      undefined,
      { signal },
    )
    return response.data
  },

  resumeRun: async (scenarioResultId: string): Promise<ScenarioRunSummary> => {
    const response = await apiClient.post(`/scenarios/runs/${encodeURIComponent(scenarioResultId)}/resume`)
    return response.data
  },
}

const MAX_SEED_SEARCH_LENGTH = 1_000

function seedListQuery(params: SeedExampleListParams): Record<string, string | number | readonly string[]> {
  const query: Record<string, string | number | readonly string[]> = {
    selection_key: params.selection_key,
  }
  if (params.limit != null) query.limit = params.limit
  if (params.cursor) query.cursor = params.cursor
  if (params.search) query.search = params.search.slice(0, MAX_SEED_SEARCH_LENGTH)
  if (params.modality && params.modality.length > 0) query.modality = params.modality
  if (params.seed_type && params.seed_type.length > 0) query.seed_type = params.seed_type
  if (params.harm_category && params.harm_category.length > 0) query.harm_category = params.harm_category
  return query
}

export const datasetsApi = {
  /**
   * Lists dataset summaries. Counts, modalities, and harm categories on the
   * cards come from this payload; callers do not page seeds to compute them.
   */
  listDatasets: async (
    { loaded_only }: { readonly loaded_only: boolean },
    signal?: AbortSignal,
  ): Promise<DatasetListResponse> => {
    const response = await apiClient.get('/datasets', {
      params: { loaded_only },
      signal,
    })
    return response.data
  },

  /**
   * Lists one page of logical examples for a dataset.
   * Requires the opaque selection key; an empty key is not sent.
   */
  listSeedExamples: async (
    params: SeedExampleListParams,
    signal?: AbortSignal,
  ): Promise<SeedExampleListResponse> => {
    if (!params.selection_key) {
      throw new Error('A dataset selection key is required to list seed examples.')
    }
    const response = await apiClient.get('/datasets/seeds', {
      params: seedListQuery(params),
      paramsSerializer: { indexes: null },
      signal,
    })
    return response.data
  },

  /**
   * Loads every stored member of one logical example in that dataset.
   * The example id alone is not enough: the selection key keeps the group scoped.
   */
  getSeedExample: async (
    exampleId: string,
    selectionKey: string,
    signal?: AbortSignal,
  ): Promise<SeedExampleDetailResponse> => {
    if (!selectionKey) {
      throw new Error('A dataset selection key is required to read a seed example.')
    }
    const response = await apiClient.get(`/datasets/seeds/${encodeURIComponent(exampleId)}`, {
      params: { selection_key: selectionKey },
      signal,
    })
    return response.data
  },
}
import type { RuntimeReadiness, RuntimeStatus } from '@/types'
