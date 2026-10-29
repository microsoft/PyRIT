import { Field, Select, Text } from '@fluentui/react-components'

import type { RegisteredComponentReference, ScorerInstance } from '@/types'
import { findRegisteredScorer } from '@/utils/scorerPresentation'

import { useChatScorerPickerStyles } from './ChatScorerPicker.styles'

interface ChatScorerPickerProps {
  value: RegisteredComponentReference | null | undefined
  scorers: ScorerInstance[]
  onChange: (value: RegisteredComponentReference | null) => void
  onUseLastUsed?: () => void
  label?: string
  disabled?: boolean
  loading?: boolean
  error?: string | null
  inherited?: boolean
  usage?: 'chat' | 'scenario'
}

function scorerOptionLabel(scorer: ScorerInstance): string {
  return `${scorer.scorer_registry_name} - ${scorer.display_type ?? scorer.identifier.class_name}`
}

export default function ChatScorerPicker({
  value, scorers, onChange, onUseLastUsed, label = 'Chat scorer', disabled, loading, error, inherited = false, usage = 'chat',
}: ChatScorerPickerProps) {
  const styles = useChatScorerPickerStyles()
  const selectedScorer = findRegisteredScorer(value ?? null, scorers, inherited)
  const available = !value || selectedScorer !== undefined
  const eligibleScorers = scorers.filter((scorer: ScorerInstance) =>
    scorer.identifier.scorer_type === 'true_false' && (usage === 'scenario' || !scorer.chat_scoring_disabled_reason))
  const selected = value === undefined && onUseLastUsed ? 'last-used'
    : selectedScorer && eligibleScorers.includes(selectedScorer)
    ? `registered:${selectedScorer.scorer_registry_name}` : value ? 'unavailable' : ''
  return (
    <Field label={label}>
      <Select className={styles.select} aria-label={label} value={selected} disabled={disabled || loading}
        title={selectedScorer ? scorerOptionLabel(selectedScorer) : undefined}
        onChange={(_event, data) => {
          if (data.value === 'last-used') { onUseLastUsed?.(); return }
          if (!data.value) { onChange(null); return }
          const scorer = eligibleScorers.find((item: ScorerInstance) => `registered:${item.scorer_registry_name}` === data.value)
          if (scorer) onChange({ registry_name: scorer.scorer_registry_name, identifier_hash: scorer.identifier.hash })
        }}>
        {onUseLastUsed && <option value="last-used">Use last used</option>}
        <option value="">{usage === 'scenario' ? 'Use scenario default' : 'Off'}</option>
        {selected === 'unavailable' && value && <option value="unavailable" hidden disabled>
          {selectedScorer ? scorerOptionLabel(selectedScorer) : `Unavailable: ${value.registry_name}`}
        </option>}
        {eligibleScorers.map((scorer: ScorerInstance) => (
          <option key={scorer.scorer_registry_name} value={`registered:${scorer.scorer_registry_name}`}>
            {scorerOptionLabel(scorer)}
          </option>
        ))}
      </Select>
      {loading && <Text>Loading scorers...</Text>}
      {error && <Text role="alert">{error}</Text>}
      {!available && <Text role="alert">The saved scorer is unavailable or its configuration changed. Select a scorer or {usage === 'scenario' ? 'use the scenario default' : 'turn scoring off'}.</Text>}
    </Field>
  )
}
