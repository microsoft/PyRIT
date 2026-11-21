import {
  Button,
  Field,
  Input,
  Radio,
  RadioGroup,
  Text,
  Textarea,
} from '@fluentui/react-components'
import { AddRegular, DeleteRegular } from '@fluentui/react-icons'

import {
  newDimensionDraft,
  newLevelDraft,
  type AssertDimensionDraft,
  type AssertDimensionMode,
  type AssertLevelDraft,
  type AssertSettingsDraft,
} from './assertRequestDraft'
import { usePromptGenerationStyles } from './PromptGeneration.styles'

const MIN_EXPLICIT_LEVELS = 2

interface AssertDimensionsEditorProps {
  readonly settings: AssertSettingsDraft
  readonly onChange: (changes: Partial<AssertSettingsDraft>) => void
  readonly disabled: boolean
}

function withMinimumLevels(dimension: AssertDimensionDraft): AssertDimensionDraft {
  const missing = Math.max(0, MIN_EXPLICIT_LEVELS - dimension.levels.length)
  if (missing === 0) return dimension
  return { ...dimension, levels: [...dimension.levels, ...Array.from({ length: missing }, newLevelDraft)] }
}

/** Coverage dimensions: generated from descriptions, or with explicit levels. */
export default function AssertDimensionsEditor({ settings, onChange, disabled }: AssertDimensionsEditorProps) {
  const styles = usePromptGenerationStyles()
  const explicit = settings.dimensionMode === 'explicit'

  const updateDimension = (id: string, changes: Partial<AssertDimensionDraft>): void => {
    onChange({
      dimensions: settings.dimensions.map((dimension) => dimension.id === id ? { ...dimension, ...changes } : dimension),
    })
  }
  const updateLevel = (dimension: AssertDimensionDraft, levelId: string, changes: Partial<AssertLevelDraft>): void => {
    updateDimension(dimension.id, {
      levels: dimension.levels.map((level) => level.id === levelId ? { ...level, ...changes } : level),
    })
  }
  const changeMode = (mode: AssertDimensionMode): void => {
    onChange({
      dimensionMode: mode,
      dimensions: mode === 'explicit' ? settings.dimensions.map(withMinimumLevels) : settings.dimensions,
    })
  }

  return (
    <section className={styles.section} aria-labelledby="assert-dimensions-title" data-testid="assert-dimensions">
      <div className={styles.headerIdentity}>
        <Text id="assert-dimensions-title" as="h2" size={400} weight="semibold">Coverage dimensions</Text>
        <Text size={200} className={styles.muted}>
          Optional. Seed groups are spread across every level of each dimension as well as across behavior categories,
          for example user expertise or conversation channel.
        </Text>
      </div>
      <Field label="Dimension levels">
        <RadioGroup
          layout="horizontal"
          value={settings.dimensionMode}
          disabled={disabled}
          onChange={(_, data) => {
            if (data.value === 'describe' || data.value === 'explicit') changeMode(data.value)
          }}
        >
          <Radio value="describe" label="Generate levels from descriptions" />
          <Radio value="explicit" label="Define levels explicitly" />
        </RadioGroup>
      </Field>
      {!explicit && (
        <Field label="Levels per dimension" hint="How many levels to generate for each described dimension (2 to 100).">
          <Input
            className={styles.control}
            type="number"
            min={2}
            max={100}
            value={settings.dimensionLevelCount}
            disabled={disabled}
            onChange={(_, data) => onChange({ dimensionLevelCount: data.value })}
            data-testid="assert-dimension-level-count"
          />
        </Field>
      )}
      {settings.dimensions.map((dimension, index) => {
        const label = `Dimension ${index + 1}`
        return (
          <div key={dimension.id} className={styles.repeatedItem} data-testid={`assert-dimension-${index}`}>
            <div className={styles.repeatedItemHeader}>
              <Text weight="semibold">{dimension.name.trim() || label}</Text>
              <Button
                className={styles.touchTarget}
                appearance="subtle"
                icon={<DeleteRegular />}
                disabled={disabled}
                aria-label={`Remove ${label.toLowerCase()}`}
                onClick={() => onChange({ dimensions: settings.dimensions.filter((item) => item.id !== dimension.id) })}
              />
            </div>
            <Field label={`${label} name`} required>
              <Input
                className={styles.control}
                value={dimension.name}
                disabled={disabled}
                onChange={(_, data) => updateDimension(dimension.id, { name: data.value })}
              />
            </Field>
            <Field label={explicit ? `${label} description (optional)` : `${label} description`} required={!explicit}>
              <Textarea
                className={styles.wideControl}
                resize="vertical"
                value={dimension.description}
                disabled={disabled}
                onChange={(_, data) => updateDimension(dimension.id, { description: data.value })}
              />
            </Field>
            {explicit && (
              <>
                {dimension.levels.map((level, levelIndex) => (
                  <div key={level.id} className={styles.levelRow}>
                    <Input
                      aria-label={`${label} level ${levelIndex + 1} name`}
                      placeholder="Level name"
                      value={level.name}
                      disabled={disabled}
                      onChange={(_, data) => updateLevel(dimension, level.id, { name: data.value })}
                    />
                    <Input
                      aria-label={`${label} level ${levelIndex + 1} definition`}
                      placeholder="What this level means"
                      value={level.definition}
                      disabled={disabled}
                      onChange={(_, data) => updateLevel(dimension, level.id, { definition: data.value })}
                    />
                    <Button
                      className={styles.touchTarget}
                      appearance="subtle"
                      icon={<DeleteRegular />}
                      disabled={disabled || dimension.levels.length <= MIN_EXPLICIT_LEVELS}
                      aria-label={`Remove ${label.toLowerCase()} level ${levelIndex + 1}`}
                      onClick={() => updateDimension(dimension.id, {
                        levels: dimension.levels.filter((item) => item.id !== level.id),
                      })}
                    />
                  </div>
                ))}
                <Button
                  className={styles.touchTarget}
                  appearance="secondary"
                  icon={<AddRegular />}
                  disabled={disabled}
                  onClick={() => updateDimension(dimension.id, { levels: [...dimension.levels, newLevelDraft()] })}
                >
                  Add level
                </Button>
              </>
            )}
          </div>
        )
      })}
      <div className={styles.actions}>
        <Button
          className={styles.touchTarget}
          appearance="secondary"
          icon={<AddRegular />}
          disabled={disabled}
          onClick={() => onChange({ dimensions: [...settings.dimensions, newDimensionDraft(settings.dimensionMode)] })}
          data-testid="assert-add-dimension"
        >
          Add dimension
        </Button>
      </div>
    </section>
  )
}
