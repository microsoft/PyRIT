/**
 * Client-side heuristic detector for common secret patterns.
 *
 * Used by the feedback dialog to warn users before they paste a real API key,
 * token, or other credential into what will become a public GitHub issue.
 *
 * The rules are intentionally a high-signal subset — false positives are
 * tolerable (the user can submit anyway), false negatives are the worse
 * failure mode. We never block submission; the dialog only requires
 * confirmation.
 *
 * No actual matched substrings are returned — only the rule that matched and
 * how many times — so the caller cannot accidentally log the secret.
 */

export interface SecretRule {
  id: string
  label: string
  pattern: RegExp
}

export interface SecretMatch {
  ruleId: string
  label: string
  count: number
}

/**
 * Order matters only to the extent that we de-dupe by ruleId, not by
 * substring. A single secret value may legitimately match multiple rules
 * (e.g. an Anthropic key technically begins with `sk-` too); we use
 * negative lookaheads where overlap would be noisy.
 */
const RULES: SecretRule[] = [
  {
    id: 'anthropic-api-key',
    label: 'Anthropic API key',
    pattern: /\bsk-ant-[A-Za-z0-9_-]{20,}/g,
  },
  {
    id: 'openai-api-key',
    label: 'OpenAI API key',
    // Negative lookahead so we don't also flag Anthropic keys as OpenAI.
    pattern: /\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}/g,
  },
  {
    id: 'github-pat',
    label: 'GitHub personal access token',
    pattern: /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b/g,
  },
  {
    id: 'google-api-key',
    label: 'Google API key',
    pattern: /\bAIza[0-9A-Za-z_-]{35}\b/g,
  },
  {
    id: 'aws-access-key',
    label: 'AWS access key ID',
    pattern: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g,
  },
  {
    id: 'slack-token',
    label: 'Slack token',
    pattern: /\bxox[abprs]-[0-9A-Za-z-]{10,}/g,
  },
  {
    id: 'stripe-key',
    label: 'Stripe key',
    pattern: /\b(?:sk|pk|rk)_(?:live|test)_[0-9a-zA-Z]{24,}\b/g,
  },
  {
    id: 'jwt',
    label: 'JSON Web Token (JWT)',
    pattern: /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g,
  },
  {
    id: 'pem-private-key',
    label: 'PEM private key',
    pattern: /-----BEGIN [A-Z ]*PRIVATE KEY-----/g,
  },
  {
    id: 'azure-storage-key',
    label: 'Azure Storage account key',
    pattern: /AccountKey=[A-Za-z0-9+/=]{40,}/gi,
  },
  {
    id: 'azure-sas-token',
    label: 'Azure SAS token',
    pattern: /[?&]sig=[A-Za-z0-9%+/=_-]{20,}/gi,
  },
  {
    id: 'bearer-token',
    label: 'Bearer token',
    pattern: /\bBearer\s+[A-Za-z0-9._~+/-]{20,}=*/gi,
  },
  {
    id: 'inline-credential',
    label: 'Inline credential (password/secret/api_key = ...)',
    pattern:
      /\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret|api[_-]?token)\s*[:=]\s*["']?[A-Za-z0-9._/+=-]{6,}["']?/gi,
  },
  {
    id: 'huggingface-token',
    label: 'HuggingFace token',
    pattern: /\bhf_[A-Za-z0-9]{30,}\b/g,
  },
  {
    id: 'azure-resource-id',
    label: 'Azure resource ID',
    // Matches /subscriptions/<guid>/resourceGroups/.../providers/...
    pattern:
      /\/subscriptions\/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\/resourceGroups\/[^\s/]+\/providers\/[^\s]+/g,
  },
  {
    id: 'azure-service-endpoint',
    label: 'Azure service endpoint URL (model/AI/vault/search)',
    pattern:
      /\b(?:https?|wss?):\/\/[\w.-]+\.(?:openai\.azure\.com|cognitiveservices\.azure\.com|cognitive\.microsoft\.com|inference\.ml\.azure\.com|ml\.azure\.com|azureml\.ms|services\.ai\.azure\.com|search\.windows\.net|vault\.azure\.net|servicebus\.windows\.net|documents\.azure\.com|database\.windows\.net|api\.cognitive\.microsoft\.com)\b\S*/gi,
  },
  {
    id: 'azure-storage-url',
    label: 'Azure Storage URL',
    pattern:
      /\bhttps?:\/\/[\w-]+\.(?:blob|dfs|file|queue|table)\.core\.windows\.net\b\S*/gi,
  },
  {
    id: 'db-connection-string',
    label: 'Database connection string with credentials',
    pattern:
      /\b(?:mssql(?:\+[a-z]+)?|postgres(?:ql)?|mysql|mongodb|redis):\/\/[^\s:@]+:[^\s@]+@[^\s/]+/gi,
  },
]

/**
 * Scan `text` for likely secrets and return one entry per rule that matched
 * at least once. Returns an empty array if `text` is empty or no rules
 * matched.
 *
 * The order of returned matches is the rule definition order so the UI can
 * render a stable list.
 */
export function detectSecrets(text: string): SecretMatch[] {
  if (!text) return []
  const out: SecretMatch[] = []
  for (const rule of RULES) {
    // matchAll does not mutate the regex's lastIndex, so the module-level
    // RegExp objects are safe to reuse across calls.
    const count = Array.from(text.matchAll(rule.pattern)).length
    if (count > 0) {
      out.push({ ruleId: rule.id, label: rule.label, count })
    }
  }
  return out
}

/** Exported for tests and for any future "what do you detect?" docs page. */
export const SECRET_RULES: ReadonlyArray<SecretRule> = RULES
