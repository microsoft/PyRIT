# PyRIT Scanner

PyRIT (Python Risk Identification Tool for generative AI) is an open-source framework that helps security professionals proactively identify risks in generative AI systems. The scanner is the primary way to run security assessments — it executes [Scenarios](../code/scenarios/0_scenarios.ipynb) against a target AI system and reports results.

## How It Works

A PyRIT scan has three key ingredients:

1. **A Scenario** — defines *what* to test (e.g., content harms, jailbreaks, encoding probes). Scenarios bundle attack strategies, datasets, and scoring into a reusable package.
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
pyrit_scan foundry.red_team_agent --target openai_chat --initializers target load_default_datasets --strategies base64
```

## Built-in Scenarios

PyRIT ships with scenarios organized into three families:

| Family | Scenarios | Documentation |
|--------|-----------|---------------|
| **AIRT** | ContentHarms, Psychosocial, Cyber, Jailbreak, Leakage, Scam | [AIRT Scenarios](airt.ipynb) |
| **Foundry** | RedTeamAgent | [Foundry Scenarios](foundry.ipynb) |
| **Garak** | Encoding | [Garak Scenarios](garak.ipynb) |

Each scenario page shows how to run it with minimal configuration.

## For Developers

If you want to **build custom scenarios** or understand the programming model behind scenarios, see the [Scenarios Programming Guide](../code/scenarios/0_scenarios.ipynb). For details on attack strategies, dataset configuration, and advanced programmatic usage, see [Scenario Parameters](../code/scenarios/1_scenario_parameters.ipynb).
