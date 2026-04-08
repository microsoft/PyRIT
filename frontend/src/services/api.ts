import axios from 'axios'
import { toApiError } from './errors'
import type {
  TargetInstance,
  TargetListResponse,
  ConverterCatalogResponse,
  ConverterInstance,
  ConverterListResponse,
  CreateTargetRequest,
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

apiClient.interceptors.request.use((config) => {
  config.headers.set('X-Request-ID', generateRequestId())
  return config
})

// ---------------------------------------------------------------------------
// Response interceptor: log errors with request context
// ---------------------------------------------------------------------------

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
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

export const targetsApi = {
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

  previewConversion: async (request: { original_value: string; converter_ids: string[]; original_value_data_type?: string }): Promise<{ converted_value: string }> => {
    const response = await apiClient.post('/converters/preview', request)
    return response.data
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
    attack_type?: string
    converter_types?: string[]
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
