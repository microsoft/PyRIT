import type { MessagePieceRequest } from '@/types'

const REQUEST_TEMPERATURE_KEY = 'request_temperature'
const TEMPERATURE_INFO_KEY = '_pyrit_temperature'

/** The toolbar selects the next send; copied historical metadata never selects it. */
export function withRequestTemperature(pieces: MessagePieceRequest[], input: string): MessagePieceRequest[] {
  const temperature = input.trim() === '' ? undefined : Number(input)
  if (temperature !== undefined && (!Number.isFinite(temperature) || temperature < 0 || temperature > 2)) {
    throw new Error('Temperature must be a finite number between 0 and 2.')
  }
  return pieces.map((piece: MessagePieceRequest, index: number) => {
    const metadata = { ...piece.prompt_metadata }
    delete metadata[REQUEST_TEMPERATURE_KEY]
    delete metadata[TEMPERATURE_INFO_KEY]
    if (index === 0 && temperature !== undefined) metadata[REQUEST_TEMPERATURE_KEY] = temperature
    return { ...piece, prompt_metadata: Object.keys(metadata).length ? metadata : undefined }
  })
}

export function temperatureDescription(metadata?: Record<string, unknown> | null): string {
  const info = metadata?.[TEMPERATURE_INFO_KEY]
  if (!info || typeof info !== 'object') return 'Temperature: not recorded'
  if (!('resolved_temperature' in info) || !('source' in info)) return 'Temperature: invalid record'
  const value = info.resolved_temperature
  if (info.source === 'provider_default' && value === null) return 'Temperature: provider default (unknown)'
  if (typeof value !== 'number' || !Number.isFinite(value)) return 'Temperature: invalid record'
  if (info.source === 'request') return `Temperature: ${value} (request override)`
  if (info.source === 'target_default') return `Temperature: ${value} (target default)`
  return 'Temperature: invalid record'
}
