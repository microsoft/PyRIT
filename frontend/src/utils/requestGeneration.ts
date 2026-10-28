import type { MessagePieceRequest, RequestGenerationConfig, RequestGenerationInfo } from '@/types'

const REQUEST_GENERATION_KEY = 'request_generation_config'
const GENERATION_INFO_KEY = '_pyrit_generation'

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
    if (index === 0 && temperature !== undefined) {
      const config: RequestGenerationConfig = { temperature }
      metadata[REQUEST_GENERATION_KEY] = config
    }
    return { ...piece, prompt_metadata: Object.keys(metadata).length ? metadata : undefined }
  })
}

export function temperatureDescription(metadata?: Record<string, unknown> | null): string {
  const value = metadata?.[GENERATION_INFO_KEY]
  if (value == null) return 'Temperature: not recorded'
  const info = readGenerationInfo(value)
  if (!info) return 'Temperature: invalid record'
  const { resolved, source } = info.temperature
  if (source === 'provider_default' && resolved === null) return 'Temperature: provider default (unknown)'
  if (typeof resolved !== 'number' || !Number.isFinite(resolved)) return 'Temperature: invalid record'
  if (source === 'request') return `Temperature: ${resolved} (request override)`
  if (source === 'target_default') return `Temperature: ${resolved} (target default)`
  return 'Temperature: invalid record'
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
