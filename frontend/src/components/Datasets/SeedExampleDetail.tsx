import { useState } from 'react'
import type { ReactNode, Ref } from 'react'

import { Badge, Button, Text } from '@fluentui/react-components'
import { ClipboardRegular } from '@fluentui/react-icons'

import type { SeedCondition, SeedExampleDetailResponse, SeedRecord } from '@/types'

import { useSeedExampleDetailStyles } from './SeedExampleDetail.styles'

const MISSING_LABEL = 'Missing'
const NONE_LABEL = 'None'
const COPY_FAILED_MESSAGE = 'Could not copy to the clipboard.'
const MEDIA_DATA_TYPES = new Set(['image_path', 'audio_path', 'video_path', 'binary_path'])

interface SeedExampleDetailProps {
  detail: SeedExampleDetailResponse
  headingRef?: Ref<HTMLHeadingElement>
}

function memberKeys(members: readonly SeedRecord[]): string[] {
  const seen = new Map<string, number>()
  const keys: string[] = []
  for (const member of members) {
    const count = seen.get(member.id) ?? 0
    seen.set(member.id, count + 1)
    keys.push(count === 0 ? member.id : `${member.id}#${count}`)
  }
  return keys
}

function generationConversationId(metadata: Record<string, unknown> | null): string | null {
  if (metadata == null) return null
  const value = metadata.generation_conversation_id
  return typeof value === 'string' && value.length > 0 ? value : null
}

function formatStoredJson(value: unknown): string {
  return JSON.stringify(value, null, 2) ?? MISSING_LABEL
}

interface FieldProps {
  label: string
  children: ReactNode
}

function Field({ label, children }: FieldProps) {
  const styles = useSeedExampleDetailStyles()
  return (
    <>
      <dt className={styles.term}>{label}</dt>
      <dd className={styles.detail}>{children}</dd>
    </>
  )
}

function StoredText({ value }: { value: string | null }) {
  if (value == null || value === '') return MISSING_LABEL
  return value
}

function StoredStrings({ values }: { values: readonly string[] | null }) {
  const styles = useSeedExampleDetailStyles()
  if (values == null) return MISSING_LABEL
  if (values.length === 0) return NONE_LABEL
  return (
    <ul className={styles.list}>
      {values.map((value, index) => (
        <li key={`${index}:${value}`} className={styles.value}>{value}</li>
      ))}
    </ul>
  )
}

function HarmCategories({ categories }: { categories: readonly string[] | null }) {
  const styles = useSeedExampleDetailStyles()
  if (categories == null) return MISSING_LABEL
  if (categories.length === 0) return NONE_LABEL
  return (
    <span className={styles.badges}>
      {categories.map((category, index) => (
        <Badge key={`${index}:${category}`} className={styles.valueBadge} appearance="outline">{category}</Badge>
      ))}
    </span>
  )
}

function Conditions({ conditions }: { conditions: SeedCondition[] | null }) {
  const styles = useSeedExampleDetailStyles()
  if (conditions == null) return MISSING_LABEL
  if (conditions.length === 0) return NONE_LABEL
  return (
    <ul className={styles.list}>
      {conditions.map((condition, index) => (
        <li key={`${condition.condition_type}:${index}`}>
          <Text weight="semibold">{condition.condition_type || MISSING_LABEL}</Text>
          <pre className={styles.value}>{formatStoredJson(condition)}</pre>
        </li>
      ))}
    </ul>
  )
}

interface MemberViewProps {
  member: SeedRecord
  position: number
  copiedKey: string | null
  failedKey: string | null
  onCopy: (key: string, value: string) => void
}

function MemberView({ member, position, copiedKey, failedKey, onCopy }: MemberViewProps) {
  const styles = useSeedExampleDetailStyles()
  const valueKey = `${member.id}:value`
  const idKey = `${member.id}:id`
  const conversationId = generationConversationId(member.metadata)
  return (
    <article className={styles.member} aria-label={`Member ${position}`}>
      <h3 className={styles.memberTitle}>Member {position}</h3>
      <dl className={styles.fields}>
        <Field label="Seed type">{member.seed_type}</Field>
        <Field label="Data type">{member.data_type}</Field>
        <Field label="ID">{member.id}</Field>
        <Field label="Value hash"><StoredText value={member.value_sha256} /></Field>
        <Field label="Prompt group ID"><StoredText value={member.prompt_group_id} /></Field>
        <Field label="Role">{member.role ?? MISSING_LABEL}</Field>
        <Field label="Sequence">{member.sequence == null ? MISSING_LABEL : String(member.sequence)}</Field>
        <Field label="Name"><StoredText value={member.name} /></Field>
        <Field label="Declared parameters"><StoredStrings values={member.parameters} /></Field>
        <Field label="Template status">Template status is not stored.</Field>
        <Field label="Conditions"><Conditions conditions={member.conditions} /></Field>
        <Field label="Harm categories"><HarmCategories categories={member.harm_categories} /></Field>
        <Field label="Origin">{member.origin}</Field>
        <Field label="Source"><StoredText value={member.source} /></Field>
        <Field label="Dataset name"><StoredText value={member.dataset_name} /></Field>
        <Field label="Added by"><StoredText value={member.added_by} /></Field>
        <Field label="Date added"><StoredText value={member.date_added} /></Field>
        <Field label="Authors"><StoredStrings values={member.authors} /></Field>
        <Field label="Groups"><StoredStrings values={member.groups} /></Field>
        <Field label="Description"><StoredText value={member.description} /></Field>
        <Field label="Generation conversation">{conversationId ?? MISSING_LABEL}</Field>
        <Field label="Metadata">
          {member.metadata == null ? MISSING_LABEL : <pre className={styles.value}>{formatStoredJson(member.metadata)}</pre>}
        </Field>
        <Field label="Response JSON schema">
          {member.response_json_schema == null
            ? MISSING_LABEL
            : <pre className={styles.value}>{formatStoredJson(member.response_json_schema)}</pre>}
        </Field>
      </dl>
      {member.seed_type === 'simulated_conversation' && (
        <p className={styles.note}>Stored configuration. This view does not run it.</p>
      )}
      {MEDIA_DATA_TYPES.has(member.data_type) && (
        <p className={styles.note}>Media is not played in this view.</p>
      )}
      <pre className={styles.value} data-testid={`seed-value-${member.id}`}>{member.value}</pre>
      <div className={styles.actions}>
        <Button
          className={styles.touchTarget}
          appearance="secondary"
          icon={<ClipboardRegular />}
          onClick={() => { onCopy(valueKey, member.value) }}
        >
          {copiedKey === valueKey ? 'Copied full text' : 'Copy full text'}
        </Button>
        <Button
          className={styles.touchTarget}
          appearance="secondary"
          icon={<ClipboardRegular />}
          onClick={() => { onCopy(idKey, member.id) }}
        >
          {copiedKey === idKey ? 'Copied ID' : 'Copy ID'}
        </Button>
      </div>
      {failedKey === valueKey || failedKey === idKey ? (
        <p role="alert" className={styles.note}>{COPY_FAILED_MESSAGE}</p>
      ) : null}
    </article>
  )
}

/** Inspection view for stored members. Values stay text; nothing is executed or played. */
export default function SeedExampleDetail({ detail, headingRef }: SeedExampleDetailProps) {
  const styles = useSeedExampleDetailStyles()
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [failedKey, setFailedKey] = useState<string | null>(null)
  const keys = memberKeys(detail.members)
  const exampleCopyKey = `example:${detail.example_id}`

  const copyText = (key: string, value: string): void => {
    const write = navigator.clipboard?.writeText
    if (!write) {
      setCopiedKey(null)
      setFailedKey(key)
      return
    }
    write.call(navigator.clipboard, value).then(() => {
      setCopiedKey(key)
      setFailedKey(null)
    }).catch(() => {
      setCopiedKey(null)
      setFailedKey(key)
    })
  }

  return (
    <section className={styles.root} aria-label="Example detail">
      <h2 ref={headingRef} tabIndex={-1} className={styles.heading}>
        {detail.name ?? 'Example'}
      </h2>
      <dl className={styles.fields}>
        <Field label="Example ID">{detail.example_id}</Field>
        <Field label="Pieces">{detail.piece_count.toLocaleString('en-US')}</Field>
        <Field label="Objectives">{detail.objective_count.toLocaleString('en-US')}</Field>
      </dl>
      <div className={styles.actions}>
        <Button
          className={styles.touchTarget}
          appearance="secondary"
          icon={<ClipboardRegular />}
          onClick={() => { copyText(exampleCopyKey, detail.example_id) }}
        >
          {copiedKey === exampleCopyKey ? 'Copied example ID' : 'Copy example ID'}
        </Button>
      </div>
      {failedKey === exampleCopyKey ? <p role="alert" className={styles.note}>{COPY_FAILED_MESSAGE}</p> : null}
      {detail.members.length === 0 ? <Text>No stored members were returned.</Text> : null}
      {detail.members.map((member, index) => (
        <MemberView
          key={keys[index]}
          member={member}
          position={index + 1}
          copiedKey={copiedKey}
          failedKey={failedKey}
          onCopy={copyText}
        />
      ))}
    </section>
  )
}
