import type { MessagePieceRequest, RequestGenerationConfig, RequestGenerationInfo } from '@/types'

const REQUEST_GENERATION_KEY = 'request_generation_config'
const GENERATION_INFO_KEY = '_pyrit_generation'
const LEGACY_REQUEST_TEMPERATURE_KEY = 'request_temperature'
const LEGACY_TEMPERATURE_INFO_KEY = '_pyrit_temperature'

/** The toolbar selects the next send; copied historical metadata never selects it. */
export function withRequestTemperature(pieces: MessagePieceRequest[], input: string): MessagePieceRequest[] {
  const temperature = input.trim() === '' ? undefined : Number(input)
  if (temperature !== undefined && (!Number.isFinite(temperature) || temperature < 0 || temperature > 2)) {
    throw new Error('Temperature must be a finite number between 0 and 2.')
  }
  return pieces.map((piece: MessagePieceRequest, index: number) => {
    const metadata = { ...piece.prompt_metadata }
    delete metadata[REQUEST_GENERATION_KEY]
    delete metadata[GENERATION_INFO_KEY]
    delete metadata[LEGACY_REQUEST_TEMPERATURE_KEY]
    delete metadata[LEGACY_TEMPERATURE_INFO_KEY]
    if (index === 0 && temperature !== undefined) {
      const config: RequestGenerationConfig = { temperature }
      metadata[REQUEST_GENERATION_KEY] = config
    }
    return { ...piece, prompt_metadata: Object.keys(metadata).length ? metadata : undefined }
  })
}

export function temperatureDescription(metadata?: Record<string, unknown> | null): string {
  const currentValue = metadata?.[GENERATION_INFO_KEY]
  const legacyValue = metadata?.[LEGACY_TEMPERATURE_INFO_KEY]
  if (currentValue == null && legacyValue == null) return 'Temperature: not recorded'
  const current = readGenerationInfo(currentValue)
  const legacy = readGenerationInfo(legacyValue, true)
  if (currentValue != null && !current || legacyValue != null && !legacy) return 'Temperature: invalid record'
  if (current && legacy && (
    current.temperature.requested !== legacy.temperature.requested
    || current.temperature.resolved !== legacy.temperature.resolved
    || current.temperature.source !== legacy.temperature.source
    || current.request_piece_ids.length !== legacy.request_piece_ids.length
    || current.request_piece_ids.some((id: string, index: number) => id !== legacy.request_piece_ids[index])
  )) return 'Temperature: invalid record'
  const info = current ?? legacy
  if (!info) return 'Temperature: invalid record'
  const { resolved: value, source } = info.temperature
  if (source === 'provider_default' && value === null) return 'Temperature: provider default (unknown)'
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Temperature: invalid record'
  if (source === 'request') return `Temperature: ${value} (request override)`
  if (source === 'target_default') return `Temperature: ${value} (target default)`
  return 'Temperature: invalid record'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isTemperatureValue(value: unknown): value is number | null {
  return value === null || typeof value === 'number' && Number.isFinite(value)
}

function readGenerationInfo(value: unknown, legacy: boolean = false): RequestGenerationInfo | undefined {
  if (!isRecord(value)) return undefined
  const temperature = legacy ? {
    requested: value.requested_temperature, resolved: value.resolved_temperature, source: value.source,
  } : value.temperature
  if (!isRecord(temperature)) return undefined
  const { requested, resolved, source } = temperature
  const ids = value.request_piece_ids === undefined ? [] : value.request_piece_ids
  if (!isTemperatureValue(requested) || !isTemperatureValue(resolved)
    || source !== 'request' && source !== 'target_default' && source !== 'provider_default'
    || !Array.isArray(ids) || !ids.every((id: unknown): id is string => typeof id === 'string')) return undefined
  return { temperature: { requested, resolved, source }, request_piece_ids: ids }
}
