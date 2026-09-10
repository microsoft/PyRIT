# PyRIT Scanner

PyRIT (Python Risk Identification Tool for generative AI) is an open-source framework that helps security professionals proactively identify risks in generative AI systems. The scanner is the primary way to run security assessments — it executes [Scenarios](../code/scenarios/0_scenarios.ipynb) against a target AI system and reports results.

## How It Works

A PyRIT scan has three key ingredients:

1. **A Scenario** — defines *what* to test (e.g., content harms, jailbreaks, encoding probes). Scenarios bundle attack techniques, datasets, and scoring into a reusable package.
2. **A Target** — the AI system you're testing (e.g., an OpenAI endpoint, an Azure OpenAI deployment, a custom HTTP endpoint).
3. **Configuration** — connects the scanner to your target and registers the components it needs (targets, scorers, datasets). See [Configuration](../getting_started/configuration.md).

## Running Scans

PyRIT provides two command-line interfaces:

| Tool | Best For | Documentation |
|------|----------|---------------|
| **`pyrit_scan`** | Automated, single-command execution. CI/CD pipelines, batch processing, reproducible runs. | [pyrit_scan](1_pyrit_scan.ipynb) |
| **`pyrit_shell`** | Interactive exploration. Rapid iteration, comparing results across runs, debugging scenarios. | [pyrit_shell](2_pyrit_shell.md) |

### Quick Example

```bash
# Run the Foundry RedTeamAgent scenario against your configured target
pyrit_scan run foundry.red_team_agent --target openai_chat --initializers target --techniques base64
```

### Connecting to CoPyRIT

Point a local configuration file at the remote backend:

```yaml
server:
  url: https://copyrit.example.com/
  auth_mode: auto
```

Then use the file without changing the default configuration in `~/.pyrit`:

```bash
pyrit_scan --config-file ./.pyrit_conf list-scenarios
```

The CLI reads the server's public authentication configuration. Automatic mode uses an
interactive Entra device code with the exact Microsoft Graph `User.Read` scope and an encrypted
persistent token cache. Use `--auth-mode device_code` to require this flow or `--auth-mode none`
to disable authentication discovery.

`--auth-mode azure_cli` is an explicit compatibility mode. The Azure CLI application can issue
a Graph token with permissions beyond `User.Read`, and the CLI sends that token to the backend.
Prefer automatic device-code authentication.

## Built-in Scenarios

PyRIT ships with scenarios organized into the following families:

| Family | Scenarios | Documentation |
|--------|-----------|---------------|
| **Adaptive** | TextAdaptive | [Adaptive Scenarios](adaptive.ipynb) |
| **AIRT** | RapidResponse, Psychosocial, Cyber, Jailbreak, Multilingual, Leakage, Scam | [AIRT Scenarios](airt.ipynb) |
| **Benchmark** | AdversarialBenchmark | [Benchmark Scenarios](benchmark.ipynb) |
| **Foundry** | RedTeamAgent | [Foundry Scenarios](foundry.ipynb) |
| **Garak** | Encoding, FigStep, WebInjection, Doctor, SystemPromptExtraction, PackageHallucination, AudioAchillesHeel | [Garak Scenarios](garak.ipynb) |

Each scenario page shows how to run it with minimal configuration.

## For Developers

If you want to **build custom scenarios** or understand the programming model behind scenarios, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb). For details on attack techniques, dataset configuration, and advanced programmatic usage, see [Common Scenario Parameters](../code/scenarios/1_common_scenario_parameters.ipynb).
