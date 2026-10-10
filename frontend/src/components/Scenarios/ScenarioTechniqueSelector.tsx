import { useId, useMemo } from 'react'

import { Checkbox, Tag, TagGroup, Text } from '@fluentui/react-components'

import type { ScenarioTechniqueSummary } from '@/types'

import { useScenarioTechniqueSelectorStyles } from './ScenarioTechniqueSelector.styles'
import { buildSelectableTechniques, type SelectableTechnique } from './scenarioConfigForm'

interface ScenarioTechniqueSelectorProps {
  techniqueOptions: ScenarioTechniqueSummary[]
  selectedTechniques: string[]
  includeBaseline: boolean
  isBaselineForbidden: boolean
  disabled: boolean
  onTechniquesChange: (techniques: string[]) => void
  onIncludeBaselineChange: (includeBaseline: boolean) => void
}

/**
 * Technique picker shared by the scenario launch form and the preset editor.
 *
 * Baseline is rendered as a pseudo-technique so an operator sees one list, but it
 * travels as its own flag because the backend models it as `include_baseline`.
 *
 * A selection may hold tokens no checkbox represents: an aggregate such as `all`, or a
 * technique carrying a converter modifier (`role_play:converter.translation_spanish`).
 * Those render as dismissible tags, because leaving them out would show every checkbox
 * cleared while the run still executes them.
 */
export default function ScenarioTechniqueSelector({
  techniqueOptions,
  selectedTechniques,
  includeBaseline,
  isBaselineForbidden,
  disabled,
  onTechniquesChange,
  onIncludeBaselineChange,
}: ScenarioTechniqueSelectorProps) {
  const styles = useScenarioTechniqueSelectorStyles()
  const titleId = useId()

  const selectableTechniques = useMemo<SelectableTechnique[]>(
    () => buildSelectableTechniques(techniqueOptions, isBaselineForbidden),
    [isBaselineForbidden, techniqueOptions],
  )

  const isTechniqueSelected = (technique: SelectableTechnique): boolean => (
    technique.isBaseline ? includeBaseline : selectedTechniques.includes(technique.name)
  )

  const tokensWithoutCheckbox = useMemo(() => {
    const checkboxNames = new Set(
      selectableTechniques.filter((technique) => !technique.isBaseline).map((technique) => technique.name),
    )
    return selectedTechniques.filter((token) => !checkboxNames.has(token))
  }, [selectableTechniques, selectedTechniques])

  const handleTechniqueChange = (technique: SelectableTechnique, checked: boolean): void => {
    if (technique.isBaseline) {
      onIncludeBaselineChange(checked)
      return
    }
    if (checked) {
      if (!selectedTechniques.includes(technique.name)) {
        onTechniquesChange([...selectedTechniques, technique.name])
      }
      return
    }
    onTechniquesChange(selectedTechniques.filter((name) => name !== technique.name))
  }

  return (
    <section className={styles.section} aria-labelledby={titleId}>
      <Text id={titleId} as="h2" size={400} weight="semibold">
        Techniques
      </Text>
      <Text size={200} className={styles.hint}>
        Select individual techniques.
      </Text>
      {selectedTechniques.length === 0 && (
        <Text className={styles.errorText} role="alert">
          Select at least one attack technique.
        </Text>
      )}
      {tokensWithoutCheckbox.length > 0 && (
        <div className={styles.pinnedTokens}>
          <Text size={200} className={styles.hint}>
            Also running, with no checkbox of their own:
          </Text>
          <TagGroup
            aria-label="Techniques without a checkbox"
            data-testid="techniques-without-checkbox"
            onDismiss={(_, data) => onTechniquesChange(
              selectedTechniques.filter((name) => name !== data.value),
            )}
          >
            {tokensWithoutCheckbox.map((token) => (
              <Tag
                key={token}
                value={token}
                dismissible
                disabled={disabled}
                size="small"
                aria-label={`Remove ${token}`}
              >
                {token}
              </Tag>
            ))}
          </TagGroup>
        </div>
      )}
      <div className={styles.techniqueList} role="group" aria-label="Techniques">
        {selectableTechniques.map((technique) => {
          const selected = isTechniqueSelected(technique)
          return (
            <div className={styles.techniqueOption} key={technique.name}>
              <Checkbox
                className={styles.selectionControl}
                label={technique.name}
                checked={selected}
                disabled={disabled || technique.disabled}
                onChange={(_, data) => handleTechniqueChange(technique, data.checked === true)}
                data-testid={technique.isBaseline ? 'baseline-checkbox' : `technique-${technique.name}`}
              />
              <div className={styles.techniqueDetails}>
                {technique.description && (
                  <Text size={200} className={styles.hint}>{technique.description}</Text>
                )}
                {technique.disabled && (
                  <Text size={200} className={styles.hint}>
                    This scenario does not support a baseline comparison.
                  </Text>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
