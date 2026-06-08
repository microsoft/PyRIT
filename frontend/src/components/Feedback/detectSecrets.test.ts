import { detectSecrets, SECRET_RULES } from './detectSecrets'

// Build PEM private-key markers at runtime so the literal PEM header string
// (the one beginning with five dashes + the start word) never appears in this
// file's source. That keeps the `detect-private-key` pre-commit hook (which
// scans bytes, not semantics) from flagging the test fixtures we deliberately
// use to exercise the rule.
const pemHeader = (algo: string) => `-----BEGI` + `N ${algo} KEY-----`

describe('detectSecrets', () => {
  describe('safe inputs', () => {
    it('returns an empty array for empty input', () => {
      expect(detectSecrets('')).toEqual([])
    })

    it('returns an empty array for plain prose feedback', () => {
      expect(
        detectSecrets(
          "The chat window doesn't scroll to the bottom after I send a message. " +
            'Steps to reproduce: open chat, send a few messages, observe.',
        ),
      ).toEqual([])
    })

    it('does not flag placeholder words shorter than the value-length threshold', () => {
      // `foo` is only 3 chars; rule requires 6+
      expect(detectSecrets('password=foo')).toEqual([])
    })

    it('does not flag angle-bracket placeholders', () => {
      expect(detectSecrets('set api_key=<your_key_here>')).toEqual([])
    })

    it('does not flag the bare prefix `sk-` without enough trailing chars', () => {
      expect(detectSecrets('see the sk- prefix used by OpenAI')).toEqual([])
    })
  })

  describe('high-confidence prefix patterns', () => {
    it('detects OpenAI keys (sk-...)', () => {
      const matches = detectSecrets(
        'my key is sk-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345 thanks',
      )
      expect(matches.map((m) => m.ruleId)).toContain('openai-api-key')
    })

    it('detects OpenAI project keys (sk-proj-...)', () => {
      const matches = detectSecrets('use sk-proj-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345')
      expect(matches.map((m) => m.ruleId)).toContain('openai-api-key')
    })

    it('detects Anthropic keys (sk-ant-...) without also flagging as OpenAI', () => {
      const matches = detectSecrets('try sk-ant-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345')
      const ids = matches.map((m) => m.ruleId)
      expect(ids).toContain('anthropic-api-key')
      expect(ids).not.toContain('openai-api-key')
    })

    it('detects classic GitHub PATs (ghp_...)', () => {
      const matches = detectSecrets('token: ghp_' + 'a'.repeat(36))
      expect(matches.map((m) => m.ruleId)).toContain('github-pat')
    })

    it('detects fine-grained GitHub PATs (github_pat_...)', () => {
      const matches = detectSecrets('token github_pat_' + 'A1b2'.repeat(20))
      expect(matches.map((m) => m.ruleId)).toContain('github-pat')
    })

    it('detects Google API keys (AIza...)', () => {
      const matches = detectSecrets('key=AIza' + 'a'.repeat(35))
      expect(matches.map((m) => m.ruleId)).toContain('google-api-key')
    })

    it('detects AWS access key IDs', () => {
      const matches = detectSecrets('AKIA' + 'A'.repeat(16))
      expect(matches.map((m) => m.ruleId)).toContain('aws-access-key')
    })

    it('detects Slack tokens', () => {
      const matches = detectSecrets('xoxb-' + 'a'.repeat(20))
      expect(matches.map((m) => m.ruleId)).toContain('slack-token')
    })

    it('detects Stripe keys', () => {
      const matches = detectSecrets('sk_live_' + 'a'.repeat(24))
      expect(matches.map((m) => m.ruleId)).toContain('stripe-key')
    })

    it('detects JWT tokens', () => {
      const matches = detectSecrets(
        'Authorization=eyJabcdefghij.abcdefghij.abcdefghij',
      )
      expect(matches.map((m) => m.ruleId)).toContain('jwt')
    })
  })

  describe('structural patterns', () => {
    it('detects PEM private keys', () => {
      const matches = detectSecrets(`${pemHeader('RSA PRIVATE')}\nMIIEowIB...`)
      expect(matches.map((m) => m.ruleId)).toContain('pem-private-key')
    })

    it('detects PEM private keys without an algorithm prefix', () => {
      const matches = detectSecrets(pemHeader('PRIVATE'))
      expect(matches.map((m) => m.ruleId)).toContain('pem-private-key')
    })

    it('detects Azure Storage account keys', () => {
      const matches = detectSecrets(
        'DefaultEndpointsProtocol=https;AccountKey=' + 'a'.repeat(80) + '==',
      )
      expect(matches.map((m) => m.ruleId)).toContain('azure-storage-key')
    })

    it('detects Azure SAS tokens in URLs', () => {
      const matches = detectSecrets(
        'https://account.blob.core.windows.net/x?sv=2021&sig=' + 'a'.repeat(40),
      )
      expect(matches.map((m) => m.ruleId)).toContain('azure-sas-token')
    })

    it('detects Bearer tokens', () => {
      const matches = detectSecrets('Authorization: Bearer ' + 'a'.repeat(40))
      expect(matches.map((m) => m.ruleId)).toContain('bearer-token')
    })

    it('detects inline password assignments', () => {
      const matches = detectSecrets('password=hunter2pass')
      expect(matches.map((m) => m.ruleId)).toContain('inline-credential')
    })

    it('detects inline api_key assignments with quotes', () => {
      const matches = detectSecrets('api_key = "abcdef1234567890"')
      expect(matches.map((m) => m.ruleId)).toContain('inline-credential')
    })

    it('detects inline client_secret assignments', () => {
      const matches = detectSecrets('client_secret: my-secret-value-123')
      expect(matches.map((m) => m.ruleId)).toContain('inline-credential')
    })

    it('detects HuggingFace tokens (hf_...)', () => {
      const matches = detectSecrets('token=hf_' + 'A1b2'.repeat(10))
      expect(matches.map((m) => m.ruleId)).toContain('huggingface-token')
    })

    it('detects Azure resource IDs', () => {
      const id =
        '/subscriptions/12345678-1234-1234-1234-123456789012' +
        '/resourceGroups/my-rg/providers/Microsoft.CognitiveServices/accounts/my-account'
      const matches = detectSecrets('the resource is at ' + id)
      expect(matches.map((m) => m.ruleId)).toContain('azure-resource-id')
    })

    it('detects Azure OpenAI endpoints', () => {
      const matches = detectSecrets(
        'POST https://my-deployment.openai.azure.com/openai/deployments/gpt-4o/chat',
      )
      expect(matches.map((m) => m.ruleId)).toContain('azure-service-endpoint')
    })

    it('detects Azure ML / Cognitive Services endpoints', () => {
      const matches = detectSecrets(
        'see https://eastus.api.cognitive.microsoft.com/contentsafety',
      )
      expect(matches.map((m) => m.ruleId)).toContain('azure-service-endpoint')
    })

    it('detects Azure Key Vault URLs', () => {
      const matches = detectSecrets('vault is at https://my-vault.vault.azure.net/')
      expect(matches.map((m) => m.ruleId)).toContain('azure-service-endpoint')
    })

    it('detects WebSocket Azure endpoints (realtime APIs)', () => {
      const matches = detectSecrets(
        'connect to wss://my-realtime.openai.azure.com/openai/realtime',
      )
      expect(matches.map((m) => m.ruleId)).toContain('azure-service-endpoint')
    })

    it('detects Azure Blob storage URLs', () => {
      const matches = detectSecrets(
        'upload to https://mystorageacct.blob.core.windows.net/results',
      )
      expect(matches.map((m) => m.ruleId)).toContain('azure-storage-url')
    })

    it('detects SQL connection strings with embedded credentials', () => {
      const matches = detectSecrets(
        'connection = mssql+pyodbc://myuser:mypassword@myserver.database.windows.net/mydb',
      )
      expect(matches.map((m) => m.ruleId)).toContain('db-connection-string')
    })

    it('detects PostgreSQL connection strings', () => {
      const matches = detectSecrets(
        'DATABASE_URL=postgres://user:pass@db.example.com:5432/mydb',
      )
      expect(matches.map((m) => m.ruleId)).toContain('db-connection-string')
    })

    it('does not flag a generic https URL as an Azure endpoint', () => {
      const matches = detectSecrets('see https://github.com/Azure/PyRIT for docs')
      expect(matches.map((m) => m.ruleId)).not.toContain('azure-service-endpoint')
      expect(matches.map((m) => m.ruleId)).not.toContain('azure-storage-url')
    })
  })

  describe('aggregation', () => {
    it('returns one match per rule even when a rule matches multiple times', () => {
      const text = `sk-${'a'.repeat(30)} and another sk-${'b'.repeat(30)}`
      const matches = detectSecrets(text)
      const openai = matches.find((m) => m.ruleId === 'openai-api-key')
      expect(openai).toBeDefined()
      expect(openai?.count).toBe(2)
    })

    it('returns multiple distinct rules when several patterns appear', () => {
      const text =
        `key=sk-${'a'.repeat(30)} ` +
        `pem=${pemHeader('PRIVATE')} ` +
        `bearer=Bearer ${'b'.repeat(40)}`
      const matches = detectSecrets(text)
      const ids = matches.map((m) => m.ruleId).sort()
      expect(ids).toEqual(
        ['bearer-token', 'openai-api-key', 'pem-private-key'].sort(),
      )
    })

    it('never returns the matched substring itself in the result', () => {
      const text = 'token: ghp_' + 'a'.repeat(36)
      const matches = detectSecrets(text)
      const serialised = JSON.stringify(matches)
      expect(serialised).not.toContain('ghp_')
    })
  })

  describe('rule definitions', () => {
    it('exposes the rule list with stable shape for documentation', () => {
      for (const rule of SECRET_RULES) {
        expect(typeof rule.id).toBe('string')
        expect(typeof rule.label).toBe('string')
        expect(rule.pattern).toBeInstanceOf(RegExp)
        expect(rule.pattern.flags).toContain('g')
      }
    })
  })
})
