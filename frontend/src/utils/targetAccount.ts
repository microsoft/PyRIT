import type { TargetInstance } from '@/types'

type TargetAccountFields = Pick<TargetInstance, 'requires_caller_credential' | 'caller_credential_configured'>

/** Whose credentials a target's requests use: the server's (`shared`) or each person's own key (`yours`). */
export type TargetAccount = 'shared' | 'yours'

export const TARGET_ACCOUNT_LABELS: Readonly<Record<TargetAccount, string>> = {
  shared: 'Shared',
  yours: 'Yours',
}

/** Why a target that uses each person's own key can't be used until this user sets theirs. */
export const NEEDS_CALLER_KEY_LABEL = 'Needs your key'

export function targetAccount(target: TargetAccountFields): TargetAccount {
  return target.requires_caller_credential === true ? 'yours' : 'shared'
}

/** Whether the target uses each person's own key and this user has not set theirs. */
export function targetNeedsCallerKey(target: TargetAccountFields): boolean {
  return targetAccount(target) === 'yours' && target.caller_credential_configured !== true
}

/** Picker label suffix for a target that uses each person's own key; empty for a shared target. */
export function targetAccountLabelSuffix(target: TargetAccountFields): string {
  if (targetAccount(target) === 'shared') return ''
  return targetNeedsCallerKey(target) ? ' · needs your key' : ' · yours'
}
