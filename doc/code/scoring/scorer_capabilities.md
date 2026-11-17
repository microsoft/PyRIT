# Scorer capabilities

What each public scorer class accepts and guarantees, generated from the classes' own
declarations. Generating it constructs no scorer, so it needs no credentials or targets.

Scenarios that select an objective scorer from a `CompatibleScorers` pool check the same
contracts on configured scorers, through `scorer.capabilities`. Some depend on
configuration, so a configured scorer is the authority: a composite takes its evidence and
conditions from the scorers it wraps, and a message scorer evaluates `MatchesObjective`
only when it is configured to judge an objective.

- **Result** is the score family the scorer returns.
- **Evidence** is the scorable types it accepts, such as `MessageScorable`.
- **Conditions** is the condition type it evaluates, such as `ToolsCalled`.
- **Float feedback** says whether each complete true/false result keeps a float value in
  `[0, 1]`, which attacks that rank results need. TAP requires it.

Regenerate this page with `python -m build_scripts.generate_scorer_capability_table`.

| Scorer | Result | Evidence | Conditions | Float feedback |
| --- | --- | --- | --- | --- |
| `AgentThreatRulesScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `AnsiEscapeOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `AnthraxKeywordScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `AssertJudgeScorer` | true/false | ConversationScorable, MessageScorable | ViolatesAssertPolicy | No |
| `AudioFloatScaleScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | Same as its wrapped scorers | Not applicable |
| `AudioTrueFalseScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | Same as its wrapped scorers | No |
| `AzureContentFilterScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `CredentialLeakScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `DecisionScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective | Not applicable |
| `DecodingScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `DivergenceScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | DivergesFromRepetition | No |
| `EscapedAnsiOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `FentanylKeywordScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `FileWriteScorer` | true/false | MessageScorable, SurfaceScorable | ContentWritten | No |
| `FloatScaleFallbackScorer` | float scale | Same as its wrapped scorers | Same as its wrapped scorers | Not applicable |
| `FloatScaleThresholdScorer` | true/false | Same as its wrapped scorers | Same as its wrapped scorers | Always |
| `GarakExploitationScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `InsecureCodeScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `KnownBadSignatureScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `LDAPInjectionOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `LlamaGuardScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `LocalRefusalClassifierScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `LocalViolenceClassifierScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `ManualScorer` | true/false | MessageScorable | None | No |
| `MarkdownInjectionScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `MessageToolCallScorer` | true/false | MessageScorable | ToolsCalled | No |
| `MethKeywordScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `NerveAgentKeywordScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `OpenRedirectOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `OtelToolCallOrderScorer` | true/false | MessageScorable, TraceScorable | ToolsCalledInOrder | No |
| `OtelToolCallScorer` | true/false | MessageScorable, TraceScorable | ToolsCalled | No |
| `OutputMatchesScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | OutputMatches | No |
| `PackageHallucinationScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `PathTraversalOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `PhysicsConsistencyScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `PlagiarismScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `ProgramExecutionScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable, ProgramExecutionScorable | ProgramBehaves | No |
| `PromptShieldScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `QuestionAnswerScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | AnswerMatches | No |
| `RegexScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `RobloxPiiScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `SQLInjectionOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SSRFOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SSTIOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SecretDisclosureScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `SelfAskCategoryScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SelfAskGeneralFloatScaleScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `SelfAskGeneralTrueFalseScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SelfAskLikertScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `SelfAskQuestionAnswerScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | AnswerMatches | No |
| `SelfAskRefusalScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SelfAskScaleScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective | Not applicable |
| `SelfAskTrueFalseScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `ShellCommandOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `ShieldGemmaScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `StaticPromptInjectionScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SubStringScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `SystemPromptExtractionScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | Not applicable |
| `TrueFalseCompositeScorer` | true/false | Same as its wrapped scorers | Same as its wrapped scorers | No |
| `TrueFalseFallbackScorer` | true/false | Same as its wrapped scorers | Same as its wrapped scorers | When both of its scorers do |
| `TrueFalseInverterScorer` | true/false | Same as its wrapped scorers | Same as its wrapped scorers | No |
| `TrueFalseScoreSelector` | true/false | Same as its wrapped scorers | Same as its wrapped scorers | No |
| `VideoFloatScaleScorer` | float scale | ContentEntryScorable, ContentScorable, MessageScorable | Same as its wrapped scorers | Not applicable |
| `VideoTrueFalseScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | Same as its wrapped scorers | No |
| `WildGuardScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `XSSOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
| `XXEOutputScorer` | true/false | ContentEntryScorable, ContentScorable, MessageScorable | MatchesObjective, when configured to judge an objective | No |
