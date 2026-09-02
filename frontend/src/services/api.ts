import axios from 'axios'
import { InteractionRequiredAuthError, type PublicClientApplication } from '@azure/msal-browser'
import { toApiError } from './errors'
import { getGraphScopes } from '../auth/msalConfig'
import type {
  TargetInstance,
  TargetListResponse,
  TargetCatalogResponse,
  ConverterCatalogResponse,
  ConverterInstance,
  ConverterListResponse,
  CreateTargetRequest,
  InitializerSettingsResponse,
  ListRegisteredInitializersResponse,
  CustomInitializerListResponse,
  RegisterInitializerRequest,
  CreateAttackRequest,
  CreateAttackResponse,
  AttackSummary,
  AttackListResponse,
  ConversationMessagesResponse,
  AddMessageRequest,
  AddMessageResponse,
  AttackConversationsResponse,
  CreateConversationRequest,
  CreateConversationResponse,
  ChangeMainConversationResponse,
  ListRegisteredScenariosResponse,
  RegisteredScenario,
  RunScenarioRequest,
  ScenarioRunSizeEstimateResponse,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunSummary,
  ScenarioRunProgress,
  ConfigurationFileContent,
  EnvironmentFileContent,
  UpdateEnvironmentFileRequest,
  EnvironmentFileListResponse,
  UpdateConfigurationFileRequest,
  AuthAccess,
} from '../types'

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  timeout: 5 * 60 * 1000, // 5 minutes – video generation can take a while
})

// ---------------------------------------------------------------------------
// Request interceptor: attach X-Request-ID for log correlation
// ---------------------------------------------------------------------------

/** Generate a UUID v4, falling back to Math.random for HTTP dev environments. */
function generateRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  // Fallback for environments without crypto.randomUUID
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

// ---------------------------------------------------------------------------
// MSAL token acquisition for API calls
// ---------------------------------------------------------------------------

let _msalInstance: PublicClientApplication | null = null

export function setMsalInstance(instance: PublicClientApplication): void {
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

apiClient.interceptors.request.use(async (config) => {
  config.headers.set('X-Request-ID', generateRequestId())

  const token = await getAccessToken()
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }

  return config
})

// ---------------------------------------------------------------------------
// Response interceptor: retry once on 401 with forced token refresh
// ---------------------------------------------------------------------------

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error?.config
    if (error?.response?.status === 401 && originalRequest && !originalRequest._retried) {
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

export const targetsApi = {
  listTargetCatalog: async (): Promise<TargetCatalogResponse> => {
    const response = await apiClient.get('/targets/catalog')
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
}

export const convertersApi = {
  listConverterCatalog: async (): Promise<ConverterCatalogResponse> => {
    const response = await apiClient.get('/converters/catalog')
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

  createConverter: async (request: { type: string; params?: Record<string, unknown> }): Promise<{ converter_id: string; converter_type: string }> => {
    const response = await apiClient.post('/converters', request)
    return response.data
  },

  previewConversion: async (request: { original_value: string; converter_ids: string[]; original_value_data_type?: string }): Promise<{ converted_value: string; converted_value_data_type?: string }> => {
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
  createAttack: async (request: CreateAttackRequest): Promise<CreateAttackResponse> => {
    const response = await apiClient.post('/attacks', request)
    return response.data
  },

  getAttack: async (attackResultId: string): Promise<AttackSummary> => {
    const response = await apiClient.get(`/attacks/${encodeURIComponent(attackResultId)}`)
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
    outcome?: string
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

export const labelsApi = {
  getLabels: async (source: string = 'attacks'): Promise<{ source: string; labels: Record<string, string[]> }> => {
    const response = await apiClient.get('/labels', { params: { source } })
    return response.data
  },
}

export const scenariosApi = {
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

  getRunProgress: async (
    scenarioResultId: string,
    params?: { since?: string; limit?: number },
  ): Promise<ScenarioRunProgress> => {
    const response = await apiClient.get(
      `/scenarios/runs/${encodeURIComponent(scenarioResultId)}/progress`,
      { params },
    )
    return response.data
  },
}
