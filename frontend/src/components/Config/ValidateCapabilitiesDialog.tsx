import { useCallback, useEffect, useState } from 'react'
import {
  Dialog,
  DialogSurface,
  DialogTitle,
  DialogBody,
  DialogContent,
  DialogActions,
  Button,
  Spinner,
  MessageBar,
  MessageBarBody,
  Table,
  TableHeader,
  TableRow,
  TableHeaderCell,
  TableBody,
  TableCell,
  Text,
  tokens,
} from '@fluentui/react-components'
import {
  CheckmarkCircleFilled,
  DismissCircleFilled,
  WarningRegular,
} from '@fluentui/react-icons'
import { targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  TargetInstance,
  ValidateCapabilitiesResponse,
} from '../../types'

interface ValidateCapabilitiesDialogProps {
  open: boolean
  target: TargetInstance | null
  onClose: () => void
}

// Boolean capability rows. Narrowed to bool-typed fields so the per-row render
// never has to deal with the string[] modality fields.
type BooleanCapabilityKey =
  | 'supports_multi_turn'
  | 'supports_multi_message_pieces'
  | 'supports_json_schema'
  | 'supports_json_output'
  | 'supports_editable_history'
  | 'supports_system_prompt'

const CAPABILITY_ROWS: Array<{ key: BooleanCapabilityKey; label: string }> = [
  { key: 'supports_multi_turn', label: 'Multi-turn' },
  { key: 'supports_multi_message_pieces', label: 'Multi-message pieces' },
  { key: 'supports_json_schema', label: 'JSON Schema' },
  { key: 'supports_json_output', label: 'JSON Output' },
  { key: 'supports_editable_history', label: 'Editable history' },
  { key: 'supports_system_prompt', label: 'System prompt' },
]

/** Compare two flattened modality lists ignoring order. */
function modalitiesEqual(a: string[] | null | undefined, b: string[] | null | undefined): boolean {
  const sa = [...(a ?? [])].sort()
  const sb = [...(b ?? [])].sort()
  return JSON.stringify(sa) === JSON.stringify(sb)
}

/** Render a green check, red X, or amber em-dash for one row. */
function MatchIndicator({ kind }: { kind: 'match' | 'mismatch' | 'not-probed' }) {
  if (kind === 'match') {
    return (
      <span style={{ color: tokens.colorPaletteGreenForeground1, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <CheckmarkCircleFilled fontSize={16} aria-hidden />
        <Text size={200}>match</Text>
      </span>
    )
  }
  if (kind === 'mismatch') {
    return (
      <span style={{ color: tokens.colorPaletteRedForeground1, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <DismissCircleFilled fontSize={16} aria-hidden />
        <Text size={200}>mismatch</Text>
      </span>
    )
  }
  return (
    <span style={{ color: tokens.colorPaletteDarkOrangeForeground1, display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <Text size={200} style={{ fontWeight: 'bold' }}>—</Text>
      <Text size={200}>not probed</Text>
    </span>
  )
}

/** Format a modality list for display. */
function formatModalities(list: string[] | null | undefined): string {
  if (!list || list.length === 0) return '—'
  return list.join(', ')
}

export default function ValidateCapabilitiesDialog({
  open,
  target,
  onClose,
}: ValidateCapabilitiesDialogProps) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ValidateCapabilitiesResponse | null>(null)

  // Reset state on close (R5): the useEffect below depends on
  // `target?.target_registry_name`, so re-clicking Validate on the SAME row
  // would not re-fire without an explicit state reset — and the user would see
  // stale results with no spinner.
  const handleClose = useCallback(() => {
    setResult(null)
    setError(null)
    setLoading(false)
    onClose()
  }, [onClose])

  // Cancellation flag skips React state updates if the dialog closes/reopens
  // for a different target while the request is still in flight. Note: this
  // does NOT cancel the backend request — the per-target backend lock
  // prevents the worst symptom (concurrent races); proper request
  // cancellation via AbortController is captured as a follow-up.
  useEffect(() => {
    if (!open || !target) return

    let cancelled = false
    setLoading(true)
    setError(null)
    setResult(null)

    targetsApi
      .validateCapabilities(target.target_registry_name)
      .then((data) => {
        if (!cancelled) setResult(data)
      })
      .catch((err) => {
        if (!cancelled) setError(toApiError(err).detail)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: only re-fire when the target's registry name actually changes, not on every parent re-render with a new TargetInstance object reference
  }, [open, target?.target_registry_name])

  if (!target) return null

  const declared = result?.declared
  const observed = result?.observed
  // Types that appear ONLY inside non-probeable combinations. The engine ORs
  // these back into observed.input_modalities (line 778), making them appear
  // confirmed in the cells even though they were never tested. Hide them from
  // the Input modalities row so the cells show only what was actually probed;
  // the "Not probed (no asset)" row below already lists them separately.
  const nonProbeableTypes = new Set(
    (result?.non_probeable_input_modalities ?? []).flatMap(combo => combo.split('+')),
  )
  const declaredProbeableInputs = (declared?.supported_input_modalities ?? []).filter(
    t => !nonProbeableTypes.has(t),
  )
  const observedProbeableInputs = (observed?.supported_input_modalities ?? []).filter(
    t => !nonProbeableTypes.has(t),
  )
  const inputMatch = modalitiesEqual(declaredProbeableInputs, observedProbeableInputs)

  return (
    <Dialog
      open={open}
      onOpenChange={(_, data) => {
        if (!data.open) handleClose()
      }}
    >
      <DialogSurface style={{ maxWidth: '720px' }}>
        <DialogBody>
          <DialogTitle>Validate capabilities: {target.target_registry_name}</DialogTitle>
          <DialogContent>
            {loading && (
              <div
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 12,
                  padding: '24px 0',
                }}
              >
                <Spinner label="Probing target — this can take up to a couple of minutes..." />
                <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
                  Sending live test requests; results may take a few seconds.
                </Text>
              </div>
            )}
            {error && !loading && (
              <MessageBar intent="error" style={{ marginBottom: 12 }}>
                <MessageBarBody>{error}</MessageBarBody>
              </MessageBar>
            )}
            {result && !loading && !error && (
              <>
                {(target.inner_targets ?? []).length > 0 && (
                  <MessageBar intent="warning" style={{ marginBottom: 12 }}>
                    <MessageBarBody>
                      This is a composite target. Validation tests aggregate routing behavior, not each
                      inner endpoint independently.
                    </MessageBarBody>
                  </MessageBar>
                )}
                <Table aria-label="Capabilities comparison" size="small" style={{ marginBottom: 16 }}>
                  <TableHeader>
                    <TableRow>
                      <TableHeaderCell style={{ width: '36%' }}>Capability</TableHeaderCell>
                      <TableHeaderCell>Declared</TableHeaderCell>
                      <TableHeaderCell>Observed</TableHeaderCell>
                      <TableHeaderCell>Match</TableHeaderCell>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {CAPABILITY_ROWS.map(({ key, label }) => {
                      const dval = declared ? declared[key] : false
                      const oval = observed ? observed[key] : false
                      const kind: 'match' | 'mismatch' = dval === oval ? 'match' : 'mismatch'
                      return (
                        <TableRow key={key}>
                          <TableCell>{label}</TableCell>
                          <TableCell>{dval ? 'yes' : 'no'}</TableCell>
                          <TableCell>{oval ? 'yes' : 'no'}</TableCell>
                          <TableCell>
                            <MatchIndicator kind={kind} />
                          </TableCell>
                        </TableRow>
                      )
                    })}
                    <TableRow>
                      <TableCell>Input modalities</TableCell>
                      <TableCell>{formatModalities(declaredProbeableInputs)}</TableCell>
                      <TableCell>{formatModalities(observedProbeableInputs)}</TableCell>
                      <TableCell>
                        <MatchIndicator kind={inputMatch ? 'match' : 'mismatch'} />
                      </TableCell>
                    </TableRow>
                    {result.non_probeable_input_modalities.length > 0 && (
                      <TableRow data-testid="not-probed-row">
                        <TableCell>
                          <Text size={200} style={{ color: tokens.colorNeutralForeground3 }}>
                            Not probed (no asset)
                          </Text>
                        </TableCell>
                        <TableCell colSpan={2}>
                          <Text size={200}>
                            {result.non_probeable_input_modalities.join(', ')}
                          </Text>
                        </TableCell>
                        <TableCell>
                          <MatchIndicator kind="not-probed" />
                        </TableCell>
                      </TableRow>
                    )}
                    <TableRow>
                      <TableCell>Output modalities</TableCell>
                      <TableCell>{formatModalities(declared?.supported_output_modalities)}</TableCell>
                      <TableCell>{formatModalities(observed?.supported_output_modalities)}</TableCell>
                      <TableCell>
                        <MatchIndicator kind="not-probed" />
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
                {result.warnings.length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {result.warnings.map((w, idx) => (
                      <MessageBar key={idx} intent="warning" icon={<WarningRegular />}>
                        <MessageBarBody>{w}</MessageBarBody>
                      </MessageBar>
                    ))}
                  </div>
                )}
              </>
            )}
          </DialogContent>
          <DialogActions>
            <Button appearance="secondary" onClick={handleClose}>
              Close
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
