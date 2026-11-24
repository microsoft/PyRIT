import { useMemo, useRef, useState } from 'react'

import {
  Button,
  MessageBar,
  MessageBarActions,
  MessageBarBody,
  Spinner,
  Text,
} from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'
import { useMatch, useNavigate } from 'react-router'

import ConfirmDialog from '@/components/ConfirmDialog'
import { NEW_PRESET_ROUTE, PRESETS_ROUTE, presetEditorRoutePath } from '@/components/ScenarioPresets/presetRoutes'
import {
  memberScenarioName,
  otherSuitesUsing,
  suiteMembers,
  suiteNeedsAttention,
  type SuiteMember,
} from '@/components/ScenarioSuites/suiteMembers'
import { useScenarioSuiteLibrary } from '@/hooks/useScenarioSuiteLibrary'
import { type SuiteMutationOutcome, useScenarioSuiteMutations } from '@/hooks/useScenarioSuiteMutations'
import type { ScenarioSuiteResponse } from '@/types'

import AddSuiteScenarioDialog from './AddSuiteScenarioDialog'
import EditSuiteDialog from './EditSuiteDialog'
import NewSuiteDialog from './NewSuiteDialog'
import RegistrySuiteCard from './RegistrySuiteCard'
import { useScenarioPresetRegistryStyles } from './ScenarioPresetRegistry.styles'

interface SharedEdit {
  readonly scenarioName: string
  readonly presetName: string
  readonly otherSuites: string[]
}


/** Presets that need attention come first so they are fixed before anything else. */
function compareForReview(left: ScenarioSuiteResponse, right: ScenarioSuiteResponse): number {
  const attention = Number(suiteNeedsAttention(right)) - Number(suiteNeedsAttention(left))
  return attention !== 0 ? attention : left.suite.name.localeCompare(right.suite.name)
}

function attentionMessage(count: number): string {
  return count === 1
    ? "1 preset needs attention and won't show in the Scanner until fixed."
    : `${count} presets need attention and won't show in the Scanner until fixed.`
}

/**
 * The Registry's Scenario presets page. A preset (backend scenario suite) is a saved set
 * of scenarios that the Scanner launches together; this page creates presets and adds,
 * edits, and removes the scenarios inside them.
 */
export default function ScenarioPresetRegistry() {
  const styles = useScenarioPresetRegistryStyles()
  const navigate = useNavigate()
  // Links such as the Scanner's Create a preset open the New preset dialog through this route.
  const creating = useMatch(NEW_PRESET_ROUTE) !== null
  const library = useScenarioSuiteLibrary()
  const mutations = useScenarioSuiteMutations(library.presets, library.reload)
  const newPresetButton = useRef<HTMLButtonElement>(null)
  const addScenarioButtons = useRef(new Map<string, HTMLButtonElement>())
  const [openNames, setOpenNames] = useState<ReadonlySet<string>>(new Set())
  const [newOpen, setNewOpen] = useState(false)
  // Dialogs keep only the preset's name and read it from the latest load, so a retry
  // after a conflict sends the version and scenarios the reload brought in.
  const [addingName, setAddingName] = useState<string | null>(null)
  const [editingName, setEditingName] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<ScenarioSuiteResponse | null>(null)
  const [sharedEdit, setSharedEdit] = useState<SharedEdit | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  // Sorted once per load, so a card does not jump while the user works in it.
  const suites = useMemo(() => [...library.suites].sort(compareForReview), [library.suites])
  const scenarioOptions = useMemo(() => [...library.scenarios.values()], [library.scenarios])
  const attentionNames = suites.filter(suiteNeedsAttention).map((item) => item.suite.name)
  const currentSuite = (name: string | null): ScenarioSuiteResponse | undefined => (
    name === null ? undefined : library.suites.find((item) => item.suite.name === name)
  )
  const adding = currentSuite(addingName)
  const editing = currentSuite(editingName)
  const showNewPreset = newOpen || creating

  const setCardOpen = (name: string, open: boolean): void => {
    setOpenNames((current) => {
      const next = new Set(current)
      if (open) next.add(name)
      else next.delete(name)
      return next
    })
  }

  const closeNewPreset = (): void => {
    setNewOpen(false)
    if (creating) navigate(PRESETS_ROUTE, { replace: true })
  }

  const reportOutcome = (outcome: SuiteMutationOutcome): void => {
    setActionError(outcome.status === 'done' ? null : outcome.error)
  }

  const handleEditMember = (item: ScenarioSuiteResponse, member: SuiteMember): void => {
    const otherSuites = otherSuitesUsing(member.presetName, item.suite.name, library.suites)
    if (otherSuites.length === 0) {
      navigate(presetEditorRoutePath(member.presetName))
      return
    }
    setSharedEdit({ scenarioName: memberScenarioName(member), presetName: member.presetName, otherSuites })
  }

  const handleDelete = async (item: ScenarioSuiteResponse): Promise<void> => {
    setDeleting(null)
    const outcome = await mutations.deleteSuite(item)
    reportOutcome(outcome)
    if (outcome.status === 'done') newPresetButton.current?.focus()
  }

  const empty = !library.loading && !library.error && suites.length === 0

  return (
    <div className={styles.root} data-testid="scenario-preset-registry">
      <Text as="h1" size={600} weight="semibold">Scenario presets</Text>
      <div className={styles.intro}>
        <Text size={300} className={styles.subtitle}>
          A scenario preset is a saved set of scenarios that the Scanner launches together.
        </Text>
        {!empty && (
          <Button
            ref={newPresetButton}
            className={styles.touchTarget}
            appearance="primary"
            icon={<AddRegular />}
            disabled={library.loading}
            onClick={() => setNewOpen(true)}
          >
            New preset
          </Button>
        )}
      </div>

      {actionError && (
        <MessageBar intent="error">
          <MessageBarBody>{actionError}</MessageBarBody>
        </MessageBar>
      )}
      {library.estimateError && (
        <MessageBar intent="warning">
          <MessageBarBody>{library.estimateError}</MessageBarBody>
        </MessageBar>
      )}
      {attentionNames.length > 0 && (
        <MessageBar intent="warning" data-testid="attention-banner">
          <MessageBarBody>{attentionMessage(attentionNames.length)}</MessageBarBody>
          <MessageBarActions>
            <Button
              className={styles.touchTarget}
              onClick={() => setOpenNames((current) => new Set([...current, ...attentionNames]))}
            >
              Show
            </Button>
          </MessageBarActions>
        </MessageBar>
      )}
      {library.error && (
        <MessageBar intent="error">
          <MessageBarBody>Couldn&apos;t load presets. {library.error}</MessageBarBody>
          <MessageBarActions>
            <Button className={styles.touchTarget} onClick={library.reload}>Retry</Button>
          </MessageBarActions>
        </MessageBar>
      )}

      {library.loading ? (
        <div className={styles.centeredState}>
          <Spinner label="Loading presets..." />
        </div>
      ) : empty ? (
        <div className={styles.centeredState} data-testid="registry-presets-empty-state">
          <Text as="h2" size={400} weight="semibold">No presets yet</Text>
          <Text size={300}>Create a preset, then add the scenarios it should run.</Text>
          <Button
            ref={newPresetButton}
            className={styles.touchTarget}
            appearance="primary"
            icon={<AddRegular />}
            onClick={() => setNewOpen(true)}
          >
            New preset
          </Button>
        </div>
      ) : (
        <div className={styles.cards}>
          {suites.map((item) => (
            <RegistrySuiteCard
              key={item.suite.name}
              item={item}
              members={suiteMembers(item, library.presets, library.scenarios)}
              sharedWith={(presetName) => otherSuitesUsing(presetName, item.suite.name, library.suites)}
              estimatesLoading={library.estimatesLoading}
              open={openNames.has(item.suite.name)}
              busy={mutations.busy}
              onOpenChange={(open) => setCardOpen(item.suite.name, open)}
              addScenarioRef={(node) => {
                if (node) addScenarioButtons.current.set(item.suite.name, node)
                else addScenarioButtons.current.delete(item.suite.name)
              }}
              onAddScenario={(item) => setAddingName(item.suite.name)}
              onEdit={(item) => setEditingName(item.suite.name)}
              onDelete={setDeleting}
              onEditMember={handleEditMember}
              onRemoveMember={(suite, member) => {
                void mutations.removeEntry(suite, member.entryId).then((outcome) => {
                  reportOutcome(outcome)
                  // The removed row takes the focused button with it; keep focus on the same card.
                  if (outcome.status === 'done') addScenarioButtons.current.get(suite.suite.name)?.focus()
                })
              }}
            />
          ))}
        </div>
      )}

      {showNewPreset && (
        <NewSuiteDialog
          scenarios={scenarioOptions}
          onCreate={mutations.createSuite}
          onCreated={(name) => {
            closeNewPreset()
            setCardOpen(name, true)
          }}
          onDismiss={closeNewPreset}
        />
      )}
      {adding && (
        <AddSuiteScenarioDialog
          suiteName={adding.suite.name}
          scenarios={scenarioOptions}
          onAdd={async (scenarioName, savedPresetName) => {
            const outcome = await mutations.addScenario(adding, scenarioName, savedPresetName)
            if (outcome.status === 'done') setCardOpen(adding.suite.name, true)
            return outcome
          }}
          onDismiss={() => setAddingName(null)}
        />
      )}
      {editing && (
        <EditSuiteDialog
          suiteName={editing.suite.name}
          description={editing.suite.description ?? ''}
          onSave={(description) => mutations.updateDescription(editing, description)}
          onDismiss={() => setEditingName(null)}
        />
      )}
      <ConfirmDialog
        open={deleting !== null}
        title={`Delete ${deleting?.suite.name ?? ''}?`}
        confirmLabel="Delete"
        onConfirm={() => { if (deleting) void handleDelete(deleting) }}
        onCancel={() => setDeleting(null)}
      >
        This removes the preset. Its scenarios stay saved, and runs already started from it are unaffected.
      </ConfirmDialog>
      <ConfirmDialog
        open={sharedEdit !== null}
        title={`${sharedEdit?.scenarioName ?? ''} is shared`}
        confirmLabel="Edit for all presets"
        onConfirm={() => { if (sharedEdit) navigate(presetEditorRoutePath(sharedEdit.presetName)) }}
        onCancel={() => setSharedEdit(null)}
      >
        This scenario is also used in {sharedEdit?.otherSuites.join(', ')}. Your changes will apply to every
        preset that uses it.
      </ConfirmDialog>
    </div>
  )
}
