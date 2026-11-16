import { makeTarget } from '@/test-utils/targetFixtures'

import { targetAccount, targetAccountLabelSuffix, targetNeedsCallerKey } from './targetAccount'

const shared = makeTarget({ target_registry_name: 'shared' })
const keySet = makeTarget({
  target_registry_name: 'key_set', requires_caller_credential: true, caller_credential_configured: true,
})
const keyMissing = makeTarget({
  target_registry_name: 'key_missing', requires_caller_credential: true, caller_credential_configured: false,
})
const noSignedInUser = makeTarget({
  target_registry_name: 'no_user', requires_caller_credential: true, caller_credential_configured: null,
})

describe('target account', () => {
  it('treats a target without the caller flag as shared', () => {
    expect(targetAccount(shared)).toBe('shared')
    expect(targetAccount(makeTarget({ target_registry_name: 'flag_off', requires_caller_credential: false })))
      .toBe('shared')
    expect(targetAccount(keySet)).toBe('yours')
  })

  it('needs the key only for a target that uses each person\'s own key and has none set', () => {
    expect(targetNeedsCallerKey(shared)).toBe(false)
    expect(targetNeedsCallerKey(keySet)).toBe(false)
    expect(targetNeedsCallerKey(keyMissing)).toBe(true)
    expect(targetNeedsCallerKey(noSignedInUser)).toBe(true)
  })

  it('labels pickers for own-key targets and leaves shared targets unmarked', () => {
    expect(targetAccountLabelSuffix(shared)).toBe('')
    expect(targetAccountLabelSuffix(keySet)).toBe(' · yours')
    expect(targetAccountLabelSuffix(keyMissing)).toBe(' · needs your key')
    expect(targetAccountLabelSuffix(noSignedInUser)).toBe(' · needs your key')
  })
})
