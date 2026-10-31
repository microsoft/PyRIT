import type {
  Message, MessagePieceRequest, RequestGenerationConfig, RequestGenerationDraft, RequestGenerationInfo, TargetCapabilities,
} from '@/types'

const REQUEST_GENERATION_KEY = 'request_generation_config'
const GENERATION_INFO_KEY = '_pyrit_generation'

export const GENERATION_FIELDS: ReadonlyArray<{
  readonly name: keyof RequestGenerationConfig
  readonly label: string
  readonly capability: Extract<keyof TargetCapabilities, `supports_${string}`>
  readonly min: number
  readonly max: number
  readonly step: number
}> = [
  { name: 'temperature', label: 'Temperature', capability: 'supports_temperature', min: 0, max: 2, step: 0.1 },
]

export function generationFieldError(
  name: keyof RequestGenerationConfig, input: string | undefined, capabilities?: TargetCapabilities | null,
): string | undefined {
  if (input === undefined) return undefined
  const field = GENERATION_FIELDS.find((entry) => entry.name === name)
  if (!field) throw new Error(`Unknown generation option: ${name}`)
  if (!capabilities?.[field.capability]) return `This target does not support runtime ${name}.`
  if (!input.trim() || !Number.isFinite(Number(input)) || Number(input) < field.min || Number(input) > field.max) {
    return `${field.label} must be a finite number between ${field.min} and ${field.max}.`
  }
  return undefined
}

export function generationConfigError(draft: RequestGenerationDraft, capabilities?: TargetCapabilities | null): string | undefined {
  for (const field of GENERATION_FIELDS) {
    const error = generationFieldError(field.name, draft[field.name], capabilities)
    if (error) return error
  }
  return undefined
}

export function parseGenerationConfig(draft: RequestGenerationDraft, capabilities?: TargetCapabilities | null): RequestGenerationConfig {
  const config: { -readonly [Field in keyof RequestGenerationConfig]?: number } = {}
  for (const field of GENERATION_FIELDS) {
    const input = draft[field.name]
    const error = generationFieldError(field.name, input, capabilities)
    if (error) throw new Error(error)
    if (input !== undefined) config[field.name] = Number(input)
  }
  return config
}

/** The composer selects the next send; copied historical metadata never selects it. */
export function withRequestGenerationConfig(pieces: MessagePieceRequest[], config: RequestGenerationConfig): MessagePieceRequest[] {
  const options = Object.fromEntries(Object.entries(config).filter(([, value]) => value != null))
  return pieces.map((piece: MessagePieceRequest, index: number) => {
    const metadata = { ...piece.prompt_metadata }
    delete metadata[REQUEST_GENERATION_KEY]
    delete metadata[GENERATION_INFO_KEY]
    if (index === 0 && Object.keys(options).length > 0) {
      metadata[REQUEST_GENERATION_KEY] = { ...options }
    }
    return { ...piece, prompt_metadata: Object.keys(metadata).length ? metadata : undefined }
  })
}

export function generationDisplay(metadata?: Record<string, unknown> | null): Pick<Message, 'generationDescription' | 'generationDetails'> {
  const value = metadata?.[GENERATION_INFO_KEY]
  if (value == null) return { generationDescription: 'Temperature: not recorded' }
  const info = readGenerationInfo(value)
  if (!info) return { generationDescription: 'Temperature: invalid record' }
  const descriptions: string[] = []
  const details: string[] = []
  for (const field of GENERATION_FIELDS) {
    const { requested, resolved, source } = info[field.name]
    const sourceLabel = { request: 'request override', target_default: 'target default', provider_default: 'provider default' }[source]
    if (source === 'provider_default' && resolved === null) descriptions.push(`${field.label}: provider default (unknown)`)
    else if (resolved !== null && source !== 'provider_default') descriptions.push(`${field.label}: ${resolved} (${sourceLabel})`)
    else return { generationDescription: `${field.label}: invalid record` }
    details.push(`${field.label}. Requested override: ${requested ?? 'none'}. Sent parameter: ${resolved ?? 'omitted (provider default unknown)'}. Source: ${sourceLabel}.`)
  }
  return { generationDescription: descriptions.join('; '), generationDetails: details.join(' ') }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isParameterValue(value: unknown): value is number | null {
  return value === null || typeof value === 'number' && Number.isFinite(value)
}

function readGenerationInfo(value: unknown): RequestGenerationInfo | undefined {
  if (!isRecord(value)) return undefined
  const temperature = value.temperature
  if (!isRecord(temperature)) return undefined
  const { requested, resolved, source } = temperature
  const ids = value.request_piece_ids === undefined ? [] : value.request_piece_ids
  if (!isParameterValue(requested) || !isParameterValue(resolved)
    || source !== 'request' && source !== 'target_default' && source !== 'provider_default'
    || !Array.isArray(ids) || !ids.every((id: unknown): id is string => typeof id === 'string')) return undefined
  return { temperature: { requested, resolved, source }, request_piece_ids: ids }
}
