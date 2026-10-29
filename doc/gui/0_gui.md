# PyRIT GUI (CoPyRIT)

CoPyRIT is the browser interface for PyRIT. Use Chat for manual tests, Scanner
for automated scenarios, and the results pages to review evidence.

## Getting Started

With PyRIT installed, start the server:

```bash
pyrit_backend --auth-mode local
```

Open `http://localhost:8000` in your browser.

Local mode disables incoming-request authentication and is restricted to
loopback access. Administrator actions are disabled by default. For a trusted
local development server, set `PYRIT_ALLOW_UNAUTHENTICATED_ADMIN=true` to enable
them. Never use these settings for a network-accessible deployment.

For other setup options, see [Docker](../getting_started/install_docker.md) or the
[Azure deployment guide](https://github.com/microsoft/PyRIT/blob/main/infra/README.md).

## Home

Home shows your last-used objective target and recent attacks grouped by operation.
Open an attack to return to its conversation, or go to Registry to configure a
target. The shared labels bar sets the operator, operation, and custom labels
for new work; it does not change saved results.

![Home page with target and recent work](images/home.png)

## Chat

Select **Attacks** in the sidebar to open Chat (`/attack/chat`), the manual conversation
workspace. Select a target and write the messages yourself. You can attach media, transform content with converters,
and branch or edit conversations to try another approach. Add an objective and
select a chat scorer if you want automated assessment of responses.

To score a saved response, open the objective outcome, expand **Automated scorer**,
and select a scorer. **Apply scorer** evaluates only the latest saved assistant
response in the open conversation. It keeps an existing complete result for the
same response, scorer configuration, and objective criteria without another
evaluation. **Off** hides the action. Earlier responses, other conversations, and
human scores do not change; future responses keep the selected chat scorer.

New chats and scans start with the last objective target you used. New chats also
reuse the last chat scorer, including **Off**, unless a Registry default is set.
These choices are saved in the current browser profile after use. Opening saved
activity or editing an unsent draft does not change them.

The **Chat** tab returns to your retained draft, or your last chat if there is no draft.
Selecting **Chat** while a chat is open keeps that chat open.
Use **New attack** to start a blank draft. The **History** tab
(`/attack/history`) opens saved attacks in the same conversation viewer. Automated results
are read-only; **Continue manually** creates a separate manual attack. Export
a conversation when you need a transcript.

![Chat workspace with a prompt and target response](images/chat.png)

## Scanner

Scanner has **Launch** (`/scanner/launch`) and **History** (`/scanner/history`) tabs.
Launch is the catalog and launch point for automated scenarios.
Each scenario defines a test workflow with its techniques, data, and scoring.
Open one to select a target and adjust the settings it supports, such as
techniques, dataset selection, and concurrency.

Use the chevron on the right of a technique to inspect its identity JSON.
This does not change the checkbox selection on the left.

Review the run estimate, then launch the scan. Its run page shows progress,
errors, and results, with links to individual attacks and conversations.
Expand an attack group or objective to inspect its attempts. Technique, scorer,
dataset/seed provenance, and scenario settings use inline identity JSON.
Decided attack outcomes use **True** and **False**; execution errors remain separate.
Groups and objectives show success rates and completion counts separately.
The results page does not include a paired-outcome comparison.

**Watch live conversation** opens saved messages while an attack runs. This view
is read-only and refreshes saved messages; it has no send controls. **Cancel scan**
stops remaining scan work and keeps saved results. Ordinary manual chats do not
have scan cancellation controls.

**Rerun** opens the scenario launch page with saved settings filled in. It does
not start work or change the original run. Review or edit the settings before
launching. Recorded target/scorer identities and the scenario version are checked.
Settings that cannot be restored, including older records without a required identity,
produce an error instead of silently using current defaults. See the
[scanner guide](../scanner/0_scanner.md) for the scenario model.

![Scanner page with available scenarios](images/scanner.png)

### technique.execute

Select **technique.execute** in Scanner to run one attack technique against
one free-text objective. This is not Chat: the selected technique controls
the attack, rather than you writing each turn. You can adjust its configuration
for this run and include a baseline attack for reference.

For techniques that support conversation history, start with a stored or edited
conversation. **Use in scan** in Chat copies a conversation into this setup
page; it does not start the scan.

![technique.execute scenario and technique selection](images/technique_execute.png)

![technique.execute objective and per-run technique settings](images/technique_execute_objective.png)

## Registry

Registry prepares reusable components; it does not execute attacks. Create
targets to connect to model endpoints, converters to transform content, and
named techniques that configure existing attacks. Browse configured scorers
and select a default chat scorer; scorer setup uses initializers, not an editor
on this page. An explicit scan choice takes precedence. Otherwise, the current
adversarial-target order is a saved Registry default, the registered
`adversarial_chat` target, then last use. Objective targets do not have a Registry
default. Existing chats and runs keep their settings.

Expand a scorer, technique, or converter name to inspect its identity JSON.
Converter inputs and outputs stay visible in the table. Use the small red X
to remove a converter from the live registry; confirmation is required.

**Scenario presets** save a scenario configuration for reuse with a target
chosen at launch. **Suites** maintain ordered collections of preset definitions.
They are stored separately from live component instances.

Targets, converters, and techniques added only to the live registry must be
recreated after a backend restart or reinitialization. Use
[initializers](../code/setup/0_setup.md) for repeatable component setup.

![Registry page with registered targets and component tabs](images/registry.png)

## Datasets

Browse datasets already loaded into backend memory. Open a dataset to inspect
its examples, search text, or filter by seed type. Supported media can be
previewed on request.

Load data through PyRIT before refreshing this catalog. See
[datasets](../code/datasets/0_dataset.md) for loading options.

![Datasets page with stored dataset summaries](images/datasets.png)

## History

History is next to the feature that produced the results, not a separate sidebar
item. Use the History tab in Attacks or Scanner to filter saved results and inspect
their conversations and scores.

Scanner history opens run-level progress and results, including runs from
`technique.execute`. Its **ID** field accepts a complete conversation, attack,
finding, or scenario result ID and searches all stored runs. Hover over the field
to see the supported ID types. **Back to scan**, beside Markdown in a scan-owned
chat, returns to its run.

Supported failed or cancelled scanner runs can be resumed.
Resume retries unfinished objectives or objectives with execution errors when
the original launch configuration is available. It skips completed objectives
with non-error outcomes, including `failure`. Retrying an unsuccessful technique
is a separate setting in `technique.execute`.

Compound-attack hierarchy browsing is a separate GUI follow-up. The
[backend hierarchy API](../../pyrit/backend/README.md#scenario-result-hierarchy)
provides paged roots and ordered child expansion without clipping run statistics.
Conversation reads expose main and pruned conversations only; adversarial and
preparation contexts remain internal. Transcript bodies load only for a selected
saved conversation.

![History page with saved attacks](images/attack_history.png)

## Analytics

Analytics summarizes saved attack results across runs. Filter by operation,
target, outcome, or other metadata, then compare outcome counts and attack
success rates with bars or a heatmap. Select a chart group to narrow the report,
or open a matching attack to inspect its evidence.

This is a cross-result report, not the progress dashboard for one scanner run.

![Analytics page with outcome charts and result filters](images/analytics.png)

## Operations

Operations organize human findings for an engagement. Create an operation,
record findings with severity and description, and attach saved attacks as
evidence. A model score does not create a finding automatically.

Select an operation in the labels bar to associate new chats and scans with it.
The operation page brings its findings, linked evidence, and execution history
together.

**Export findings** on the operation page downloads the findings you select as
one Word or Markdown document to paste into a report. Evidence appears as links
to the attacks in PyRIT, without transcripts or media. Nothing is redacted, so
store and share exported files with care.

![Operations page with a test operation](images/operations.png)

## Configuration

Configuration is for backend administration, not per-chat settings. Administrators
edit the PyRIT configuration and environment sources, inspect the initializer
sequence, and manage trusted custom initializer scripts.

Saving a change does not apply it to the running backend. Reinitialize PyRIT,
when supported, or restart the backend to apply saved setup changes. This affects
all users. See [setup](../code/setup/0_setup.md#reinitializing-a-running-backend)
for requirements and limits.

![Configuration page with backend setup controls](images/configuration.png)

## Preferences

Use the icon at the top of the sidebar to show or hide navigation labels. This
choice is saved for your account in this browser. On narrow screens, the same
icon opens a navigation panel without reducing the workspace width.

Use the sidebar's **Theme** menu to change the appearance. User preferences,
such as default targets, are saved in this browser for the current account.
