API Reference
=============

:py:mod:`pyrit.analytics`
=========================

.. automodule:: pyrit.analytics
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    analyze_results
    AttackStats
    ConversationAnalytics

:py:mod:`pyrit.auth`
====================

.. automodule:: pyrit.auth
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    Authenticator
    AzureAuth
    AzureStorageAuth
    CopilotAuthenticator
    ManualCopilotAuthenticator

:py:mod:`pyrit.auxiliary_attacks`
=================================

.. automodule:: pyrit.auxiliary_attacks
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/



:py:mod:`pyrit.cli`
=======================================

.. automodule:: pyrit.cli
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/


:py:mod:`pyrit.common`
======================

.. automodule:: pyrit.common
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    apply_defaults
    apply_defaults_to_method
    combine_dict
    combine_list
    convert_local_image_to_data_url
    DefaultValueScope
    display_image_response
    download_chunk
    download_file
    download_files
    download_specific_files
    get_available_files
    get_global_default_values
    get_httpx_client
    get_kwarg_param
    get_non_required_value
    get_random_indices
    get_required_value
    is_in_ipython_session
    make_request_and_raise_if_error_async
    print_deprecation_message
    reset_default_values
    set_default_value
    Singleton
    warn_if_set
    YamlLoadable

:py:mod:`pyrit.datasets`
========================

.. automodule:: pyrit.datasets
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    SeedDatasetProvider
    TextJailBreak



:py:mod:`pyrit.embedding`
=========================

.. automodule:: pyrit.embedding
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    OpenAITextEmbedding

:py:mod:`pyrit.exceptions`
==========================

.. automodule:: pyrit.exceptions
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    BadRequestException
    EmptyResponseException
    handle_bad_request_exception
    InvalidJsonException
    MissingPromptPlaceholderException
    PyritException
    pyrit_custom_result_retry
    pyrit_json_retry
    pyrit_target_retry
    pyrit_placeholder_retry
    RateLimitException
    remove_markdown_json

:py:mod:`pyrit.executor.attack`
===============================

.. automodule:: pyrit.executor.attack
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AttackAdversarialConfig
    AttackContext
    AttackConverterConfig
    AttackExecutor
    AttackExecutorResult
    AttackParameters
    AttackResultPrinter
    AttackScoringConfig
    AttackStrategy
    ConsoleAttackResultPrinter
    ChunkedRequestAttack
    ChunkedRequestAttackContext
    ContextComplianceAttack
    ConversationManager
    ConversationSession
    ConversationState
    CrescendoAttack
    CrescendoAttackContext
    CrescendoAttackResult
    FlipAttack
    generate_simulated_conversation_async
    ManyShotJailbreakAttack
    MarkdownAttackResultPrinter
    MultiPromptSendingAttack
    MultiPromptSendingAttackParameters
    MultiTurnAttackContext
    MultiTurnAttackStrategy
    PrependedConversationConfig
    PromptSendingAttack
    RTASystemPromptPaths
    RedTeamingAttack
    RolePlayAttack
    RolePlayPaths
    SingleTurnAttackContext
    SingleTurnAttackStrategy
    SkeletonKeyAttack
    TAPAttack
    TAPAttackContext
    TAPAttackResult
    TreeOfAttacksWithPruningAttack

:py:mod:`pyrit.executor.promptgen`
==================================

.. automodule:: pyrit.executor.promptgen
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AnecdoctorContext
    AnecdoctorGenerator
    AnecdoctorResult
    PromptGeneratorStrategy
    PromptGeneratorStrategyContext
    PromptGeneratorStrategyResult

:py:mod:`pyrit.executor.promptgen.fuzzer`
=========================================

.. automodule:: pyrit.executor.promptgen.fuzzer
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    FuzzerConverter
    FuzzerContext
    FuzzerCrossOverConverter
    FuzzerExpandConverter
    FuzzerGenerator
    FuzzerRephraseConverter
    FuzzerResult
    FuzzerResultPrinter
    FuzzerShortenConverter
    FuzzerSimilarConverter

:py:mod:`pyrit.executor.workflow`
=================================

.. automodule:: pyrit.executor.workflow
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    XPIAContext
    XPIAResult
    XPIAWorkflow
    XPIATestWorkflow
    XPIAManualProcessingWorkflow
    XPIAProcessingCallback
    XPIAStatus

:py:mod:`pyrit.identifiers`
===========================

.. automodule:: pyrit.identifiers
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AtomicAttackEvaluationIdentifier
    build_atomic_attack_identifier
    build_seed_identifier
    ChildEvalRule
    class_name_to_snake_case
    ComponentIdentifier
    compute_eval_hash
    config_hash
    EvaluationIdentifier
    Identifiable
    ScorerEvaluationIdentifier
    snake_case_to_class_name

:py:mod:`pyrit.memory`
======================

.. automodule:: pyrit.memory
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AttackResultEntry
    AzureSQLMemory
    CentralMemory
    EmbeddingDataEntry
    MemoryInterface
    MemoryEmbedding
    MemoryExporter
    PromptMemoryEntry
    SeedEntry
    SQLiteMemory

:py:mod:`pyrit.message_normalizer`
==================================

.. automodule:: pyrit.message_normalizer
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    MessageListNormalizer
    MessageStringNormalizer
    GenericSystemSquashNormalizer
    TokenizerTemplateNormalizer
    ConversationContextNormalizer
    ChatMessageNormalizer

:py:mod:`pyrit.models`
======================

.. automodule:: pyrit.models
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    ALLOWED_CHAT_MESSAGE_ROLES
    AudioPathDataTypeSerializer
    AzureBlobStorageIO
    BinaryPathDataTypeSerializer
    ChatMessage
    ChatMessagesDataset
    ChatMessageRole
    ChatMessageListDictContent
    construct_response_from_request
    ConversationReference
    ConversationStats
    ConversationType
    DataTypeSerializer
    data_serializer_factory
    DiskStorageIO
    EmbeddingData
    EmbeddingResponse
    EmbeddingSupport
    EmbeddingUsageInformation
    ErrorDataTypeSerializer
    get_all_harm_definitions
    group_conversation_message_pieces_by_sequence
    group_message_pieces_into_conversations
    HarmDefinition
    ImagePathDataTypeSerializer
    AllowedCategories
    AttackOutcome
    AttackResult
    Message
    MessagePiece
    NextMessageSystemPromptPaths
    PromptDataType
    PromptResponseError
    QuestionAnsweringDataset
    QuestionAnsweringEntry
    QuestionChoice
    ScaleDescription
    ScenarioIdentifier
    ScenarioResult
    Score
    ScoreType
    Seed
    SeedAttackGroup
    SeedAttackTechniqueGroup
    SeedDataset
    SeedGroup
    SeedObjective
    SeedPrompt
    SeedSimulatedConversation
    SeedType
    SimulatedTargetSystemPromptPaths
    sort_message_pieces
    StorageIO
    StrategyResult
    TextDataTypeSerializer
    UnvalidatedScore
    VideoPathDataTypeSerializer


:py:mod:`pyrit.prompt_converter`
================================

.. automodule:: pyrit.prompt_converter
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AddImageTextConverter
    AddImageVideoConverter
    AddTextImageConverter
    AnsiAttackConverter
    AsciiArtConverter
    AsciiSmugglerConverter
    AskToDecodeConverter
    AtbashConverter
    AudioEchoConverter
    AudioFrequencyConverter
    AudioSpeedConverter
    AudioVolumeConverter
    AudioWhiteNoiseConverter
    AzureSpeechAudioToTextConverter
    AzureSpeechTextToAudioConverter
    Base2048Converter
    Base64Converter
    BinAsciiConverter
    BinaryConverter
    BrailleConverter
    CaesarConverter
    CharacterSpaceConverter
    CharSwapConverter
    CodeChameleonConverter
    ColloquialWordswapConverter
    ConverterResult
    DenylistConverter
    DiacriticConverter
    EcojiConverter
    EmojiConverter
    FirstLetterConverter
    FlipConverter
    get_converter_modalities
    HumanInTheLoopConverter
    ImageCompressionConverter
    IndexSelectionStrategy
    InsertPunctuationConverter
    JsonStringConverter
    KeywordSelectionStrategy
    LeetspeakConverter
    LLMGenericTextConverter
    MaliciousQuestionGeneratorConverter
    MathObfuscationConverter
    MathPromptConverter
    MorseConverter
    NatoConverter
    NegationTrapConverter
    NoiseConverter
    PDFConverter
    PersuasionConverter
    PositionSelectionStrategy
    PromptConverter
    ProportionSelectionStrategy
    QRCodeConverter
    RandomCapitalLettersConverter
    RandomTranslationConverter
    RangeSelectionStrategy
    RegexSelectionStrategy
    RepeatTokenConverter
    ROT13Converter
    ScientificTranslationConverter
    SearchReplaceConverter
    SelectiveTextConverter
    SneakyBitsSmugglerConverter
    StringJoinConverter
    SuffixAppendConverter
    SuperscriptConverter
    TemplateSegmentConverter
    TenseConverter
    TextJailbreakConverter
    TextSelectionStrategy
    TokenSelectionStrategy
    ToneConverter
    ToxicSentenceGeneratorConverter
    TranslationConverter
    TransparencyAttackConverter
    UnicodeConfusableConverter
    UnicodeReplacementConverter
    UnicodeSubstitutionConverter
    UrlConverter
    VariationConverter
    VariationSelectorSmugglerConverter
    WordDocConverter
    WordIndexSelectionStrategy
    WordKeywordSelectionStrategy
    WordPositionSelectionStrategy
    WordProportionSelectionStrategy
    WordRegexSelectionStrategy
    WordSelectionStrategy
    ZalgoConverter
    ZeroWidthConverter

:py:mod:`pyrit.prompt_normalizer`
=================================

.. automodule:: pyrit.prompt_normalizer
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    PromptNormalizer
    PromptConverterConfiguration
    NormalizerRequest

:py:mod:`pyrit.prompt_target`
=============================

.. automodule:: pyrit.prompt_target
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AzureBlobStorageTarget
    AzureMLChatTarget
    CopilotType
    CrucibleTarget
    GandalfLevel
    GandalfTarget
    get_http_target_json_response_callback_function
    get_http_target_regex_matching_callback_function
    HTTPTarget
    HTTPXAPITarget
    HuggingFaceChatTarget
    HuggingFaceEndpointTarget
    limit_requests_per_minute
    OpenAICompletionTarget
    OpenAIChatAudioConfig
    OpenAIImageTarget
    OpenAIChatTarget
    OpenAIResponseTarget
    OpenAIVideoTarget
    OpenAITTSTarget
    OpenAITarget
    PlaywrightCopilotTarget
    PlaywrightTarget
    PromptChatTarget
    PromptShieldTarget
    PromptTarget
    RealtimeTarget
    TargetCapabilities
    TextTarget
    WebSocketCopilotTarget

:py:mod:`pyrit.score`
=====================

.. automodule:: pyrit.score
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AudioFloatScaleScorer
    AudioTrueFalseScorer
    AzureContentFilterScorer
    BatchScorer
    ConsoleScorerPrinter
    ContentClassifierPaths
    ConversationScorer
    create_conversation_scorer
    DecodingScorer
    FloatScaleScoreAggregator
    FloatScaleScorer
    FloatScaleScorerAllCategories
    FloatScaleScorerByCategory
    FloatScaleThresholdScorer
    GandalfScorer
    HarmHumanLabeledEntry
    HarmScorerEvaluator
    HarmScorerMetrics
    HumanInTheLoopScorerGradio
    HumanLabeledDataset
    HumanLabeledEntry
    InsecureCodeScorer
    LikertScaleEvalFiles
    LikertScalePaths
    MarkdownInjectionScorer
    MetricsType
    ObjectiveHumanLabeledEntry
    ObjectiveScorerEvaluator
    ObjectiveScorerMetrics
    PlagiarismMetric
    PlagiarismScorer
    PromptShieldScorer
    QuestionAnswerScorer
    RefusalScorerPaths
    RegistryUpdateBehavior
    Scorer
    ScorerEvalDatasetFiles
    ScorerEvaluator
    ScorerMetrics
    ScorerMetricsWithIdentity
    ScorerPrinter
    ScorerPromptValidator
    get_all_harm_metrics
    get_all_objective_metrics
    SelfAskCategoryScorer
    SelfAskGeneralFloatScaleScorer
    SelfAskGeneralTrueFalseScorer
    SelfAskLikertScorer
    SelfAskQuestionAnswerScorer
    SelfAskRefusalScorer
    SelfAskScaleScorer
    SelfAskTrueFalseScorer
    SubStringScorer
    TrueFalseAggregatorFunc
    TrueFalseCompositeScorer
    TrueFalseInverterScorer
    TrueFalseQuestion
    TrueFalseQuestionPaths
    TrueFalseScoreAggregator
    TrueFalseScorer
    VideoFloatScaleScorer
    VideoTrueFalseScorer

:py:mod:`pyrit.scenario`
=========================

.. automodule:: pyrit.scenario
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    AtomicAttack
    DatasetConfiguration
    Scenario
    ScenarioCompositeStrategy
    ScenarioStrategy

:py:mod:`pyrit.scenario.airt`
=============================

.. automodule:: pyrit.scenario.airt
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    ContentHarms
    ContentHarmsStrategy
    Cyber
    CyberStrategy

:py:mod:`pyrit.scenario.foundry`
================================

.. automodule:: pyrit.scenario.foundry
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    FoundryScenario
    FoundryStrategy
    RedTeamAgent

:py:mod:`pyrit.scenario.garak`
==============================

.. automodule:: pyrit.scenario.garak
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    Encoding
    EncodingStrategy

:py:mod:`pyrit.setup`
=====================

.. automodule:: pyrit.setup
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    initialize_pyrit_async
    AZURE_SQL
    SQLITE
    IN_MEMORY

:py:mod:`pyrit.setup.initializers`
==================================

.. automodule:: pyrit.setup.initializers
    :no-members:
    :no-inherited-members:

.. autosummary::
    :nosignatures:
    :toctree: _autosummary/

    PyRITInitializer
    AIRTInitializer
    TargetInitializer
    ScorerInitializer
    SimpleInitializer
    LoadDefaultDatasets
    ScenarioObjectiveListInitializer
