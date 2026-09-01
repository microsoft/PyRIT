# PyRIT GUI (CoPyRIT)

CoPyRIT is a web-based graphical interface for PyRIT built with React and Fluent UI. It provides an interactive way to run attacks, configure targets and converters, and view results — all from a browser.

## Getting Started

There are several ways to run CoPyRIT:

### PyRIT Backend CLI

If you have PyRIT installed, use the `pyrit_backend` command to start the server. The bundled frontend is served automatically.

```bash
pyrit_backend
```

Then open `http://localhost:8000` in your browser.

Authentication-disabled local servers deny administrator operations by default. To enable configuration and initializer
administration for a trusted local development server, set `PYRIT_ALLOW_UNAUTHENTICATED_ADMIN=true`. Never use this
setting on a network-accessible deployment.

### Docker

CoPyRIT is also available as a Docker container. See the [Docker setup](https://github.com/microsoft/PyRIT/blob/main/docker/) for details.

### Azure Deployment

CoPyRIT can be deployed to Azure Container Apps with Entra authentication and managed identity. See the [Azure deployment guide](https://github.com/microsoft/PyRIT/blob/main/infra/README.md) for the full setup.

To deploy an isolated instance for an external team, see [Deploy a New Instance](../../infra/DEPLOY_NEW_INSTANCE.md).

---

## Views

CoPyRIT has three main views, accessible from the left sidebar: **Chat**, **Attack History**, and **Target Configuration**. A dark/light theme toggle is available at the bottom of the sidebar.

### Chat View

The Chat view is the primary workspace for running interactive attacks against configured targets.

<img width="1662" alt="Text-to-text chat" src="images/chat_text.png" />

#### Sending Messages

Type a message and press Enter (or click Send) to send it to the active target. The response appears below. Shift+Enter inserts a newline without sending.

#### Attachments

Click the attachment button to add images, audio, video, or documents to your message. Supported types include `image/*`, `audio/*`, `video/*`, `.pdf`, `.doc`, `.docx`, and `.txt`. Attachments are displayed as chips below the input with type icons and file sizes.

#### Multi-Modal Responses

CoPyRIT renders different response types inline:

- **Text:** Displayed as plain text
- **Images:** Rendered inline with the response
- **Audio:** Playable audio player
- **Video:** Embedded video player

<img width="1662" alt="Text-to-image response" src="images/chat_image.png" />

#### Branching Conversations

Each assistant message has four action buttons:

1. **Copy to input:** Copies the message content and attachments into the current input box.
2. **Copy to new conversation:** Creates a new conversation within the same attack and copies the message to its input.
3. **Branch conversation:** Clones the conversation up to the selected message into a new conversation within the same attack.
4. **Branch into new attack:** Creates an entirely new attack with the conversation cloned up to the selected message.

<img width="1663" alt="Branching into a new conversation" src="images/chat_branch.png" />

#### Conversations Panel

Click the panel toggle in the ribbon to open the conversations sidebar. This panel shows all conversations within the current attack, including message counts and last-message previews. You can switch between conversations, create new ones, and promote a conversation to be the "main" conversation.

#### Exporting a Conversation

Click the **Export** button in the ribbon to download the conversation that is currently displayed. Three formats are offered from the button's menu:

- **Markdown (`.md`):** A human-readable transcript with each message labeled by role. Best for reading, sharing, or pasting into reports.
- **JSON (`.json`):** A structured record of the conversation for tooling and further processing.
- **HTML (`.html`):** A single self-contained page with the images, audio, and video inside the file itself. Best for sharing a conversation as evidence, and for printing — open it and use your browser's **Print → Save as PDF**.

Every format includes the whole conversation as shown in the chat, including the system prompt shown in the banner. Scores are the exception: they are kept in the JSON export but are not written into the Markdown or HTML transcript.

Markdown records the names of attachments but never the media itself. JSON keeps media that is already inline, drops the source link for everything else, and so cannot be relied on to carry pictures either. HTML is the format to pick when the media matters. It puts each attachment it can read into the page, and lists the rest by name with the reason it was left out — media that sits on another host, which is where a deployment backed by cloud storage keeps it, cannot be read by the page and is listed as kept in remote storage; an attachment that is too large on its own is skipped; and one that no longer fits in the page is marked as having no room left. The page keeps filling after that, so an attachment later in the conversation that still fits can make it in. Files that are not images, audio, or video are never embedded. The count of what was and was not included is printed at the top of the exported page, so an incomplete export is never mistaken for a complete one. Attachment source links are deliberately left out of every export.

Exporting runs in your browser and sends nothing to the server. HTML is the one exception: it reads locally stored media back from the server so it can embed it.

Export stays available for read-only historical conversations, and is disabled while a conversation is empty, still loading, or sending. The button is disabled until there is at least one user or model message to export.

> **Note:** Exported files can contain adversarial prompts, model responses, and other sensitive material. Store and share them responsibly.

#### Labels

The labels bar in the ribbon displays the current attack's labels (e.g., `operator`, `operation`). Labels are key-value pairs that help organize and filter attacks. You can add, edit, and remove labels inline. The `operator` and `operation` labels are required and cannot be removed.

Clicking the `operation` label opens a picker listing the operations already recorded in memory, so you can choose one without typing it from memory. Typing a name that doesn't exist yet offers to create it. Very long lists show the first 200 and say how many are left, so type to narrow them. The operation you pick is applied to attacks you start from then on; it does not change attacks that already exist.

#### Behavioral Guards

CoPyRIT enforces several safety guards:

- **No target selected:** When no target is configured, the input area shows a banner prompting you to configure a target.
- **Single-turn targets:** Some targets (e.g., image generators) don't track conversation history. CoPyRIT shows a warning indicator and blocks additional messages after the first turn, offering a "New Conversation" button instead.
- **Operator locking:** If you open a historical attack created by a different operator, the conversation is read-only. You can use "Continue with your target" to branch into a new attack with your own target.
- **Cross-target locking:** If the active target differs from the target used in a historical attack, sending is blocked. Use "Continue with your target" to branch with your current target.

### Attack History

The History view lists all past attacks with filtering and pagination.

<img width="1664" alt="Attack history view" src="images/history.png" />

#### Filters

Filter attacks by:

- **Attack type:** The class of attack used (e.g., `PromptSendingAttack`)
- **Outcome:** Success, failure, or undetermined
- **Converter:** Which converters were applied
- **Operator:** Who ran the attack
- **Operation:** The operation label
- **Custom labels:** Free-form key:value label filtering with auto-complete

Click "Reset" to clear all filters.

#### Attack Table

The table displays:

| Column | Description |
|--------|-------------|
| Status | Outcome badge (success/failure/undetermined) |
| Attack Type | The attack class name |
| Target | Target type and model name |
| Operator | Who ran the attack |
| Operation | Operation label |
| Msgs | Total message count |
| Convs | Number of conversations |
| Converters | Converter badges (truncated with tooltip) |
| Labels | Additional label badges |
| Created / Updated | Timestamps |
| Last Message | Preview of the most recent message |

Click any row to open the attack in the Chat view.

#### Pagination

Results are paginated (25 per page) with "First" and "Next" navigation buttons.

### Target Configuration

The Configuration view manages the targets available for attacks.

<img width="1636" alt="Target configuration" src="images/config.png" />

#### Target Table

Lists all registered targets with their type, endpoint, and model name. Click "Set Active" to select a target for use in the Chat view. The active target is highlighted with an "Active" badge.

#### Creating Targets

Click "New Target" to open the creation dialog. Fill in:

- **Target Type** (required): Select from `OpenAIChatTarget`, `OpenAICompletionTarget`, `OpenAIImageTarget`, `OpenAIVideoTarget`, `OpenAITTSTarget`, `OpenAIResponseTarget`, or `AzureMLChatTarget`
- **Endpoint URL** (required): Your Azure OpenAI, OpenAI API, or Azure ML endpoint
- **Model / Deployment Name** (optional): e.g., `gpt-4o`, `dall-e-3`, `Llama-3.2-3B-Instruct`
- **API Key** (optional): Stored in memory only (not persisted to disk)

For `AzureMLChatTarget`, additional fields are available: **Max New Tokens**, **Temperature**, **Top P**, and **Repetition Penalty**.

#### Auto-Populating Targets

Targets can also be auto-populated by adding the `target` initializer to your `~/.pyrit/.pyrit_conf` file. This reads endpoints from your `.env` and `.env.local` files. See [.pyrit_conf_example](https://github.com/microsoft/PyRIT/blob/main/.pyrit_conf_example) for details.

### Configuration Editor

The **Configuration** page provides administrator-only editing for the files and scripts used to configure PyRIT. It has three tabs:

- **PyRIT Configuration** edits the active `.pyrit_conf` YAML file. The source may be a local file or an Azure Blob URI. Saving validates the configuration before replacing it.
- **Environment & Secrets** lists the configured local dotenv files and Azure Key Vault bootstrap secrets. Content is loaded only after selecting a source. Saves validate the dotenv document and reject the update if the source changed since it was loaded.
- **Custom Initializers** registers or removes Python initializer scripts. This tab requires `allow_custom_initializers: true`; scripts are stored in the configured local directory or Azure Blob container and must define a concrete `PyRITInitializer` subclass.

Use **Reload** to discard local edits and fetch the latest source content. Saved configuration and environment changes take effect after restarting PyRIT. Custom initializer scripts execute under the backend service identity, so only trusted administrators should manage them.

### Initializers

The **Initializers** page (in the left navigation) lets you review and extend how PyRIT sets itself up at startup — for example, the `target` initializer's `tags` and `auto_group` settings.

The page has two sections:

- **Baseline initializers** are read-only. They come from your active configuration file (`~/.pyrit/.pyrit_conf`) and run first, in order.
- **Additional initializers** are added in the GUI and saved to the memory database. They run after the baseline, in the order shown. You can add more than one initializer of the same type — each is its own invocation.

Use **Apply now** to re-run a single initializer immediately against the running backend — handy for picking up an environment or setting change without a restart. Saved additional initializers and `.pyrit_conf` edits otherwise take effect the next time the backend starts.

---

## Connection Health

CoPyRIT monitors the backend connection and shows a status banner:

- **Disconnected (red):** Unable to reach the backend. Check that the server is running.
- **Degraded (yellow):** Connection is unstable.
- **Reconnected (green):** Briefly shown after a successful reconnection, then auto-dismissed.
