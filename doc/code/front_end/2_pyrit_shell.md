# 2. PyRIT Shell - Interactive Command Line

PyRIT Shell provides an interactive REPL (Read-Eval-Print Loop) for running AI red teaming scenarios with fast execution and session-based result tracking.

## Quick Start

Start the shell:

```bash
pyrit_shell
```

With startup options:

```bash
# Set default database for all runs
pyrit_shell --database InMemory

# Set default log level
pyrit_shell --log-level DEBUG

# Load initializers at startup
pyrit_shell --initializers openai_objective_target load_default_datasets

# Load custom initialization scripts
pyrit_shell --initialization-scripts ./my_config.py
```

## Available Commands

Once in the shell, you have access to:

| Command | Description |
|---------|-------------|
| `list-scenarios` | List all available scenarios |
| `list-initializers` | List all available initializers |
| `run <scenario> [options]` | Run a scenario with optional parameters |
| `scenario-history` | List all previous scenario runs in this session |
| `print-scenario [N]` | Print detailed results for scenario run(s) |
| `help [command]` | Show help for a command |
| `clear` | Clear the screen |
| `exit` (or `quit`, `q`) | Exit the shell |

## Running Scenarios

The `run` command executes scenarios with the same options as `pyrit_scan`:

### Basic Usage

```bash
pyrit> run foundry.red_team_agent --initializers openai_objective_target load_default_datasets
```

### With Strategies

```bash
pyrit> run garak.encoding --initializers openai_objective_target load_default_datasets --strategies base64 rot13

pyrit> run foundry.red_team_agent --initializers openai_objective_target load_default_datasets -s jailbreak crescendo
```

### With Runtime Parameters

```bash
# Set concurrency and retries
pyrit> run foundry.red_team_agent --initializers openai_objective_target load_default_datasets --max-concurrency 10 --max-retries 3

# Add memory labels for tracking
pyrit> run garak.encoding --initializers openai_objective_target load_default_datasets --memory-labels '{"experiment":"test1","version":"v2"}'
```

### Override Defaults Per-Run

```bash
# Override database and log level for this run only
pyrit> run garak.encoding --initializers openai_objective_target load_default_datasets --database InMemory --log-level DEBUG
```

### Run Command Options

```
--initializers <name> ...       Built-in initializers to run before the scenario (REQUIRED)
--initialization-scripts <...>  Custom Python scripts to run before the scenario (alternative)
--strategies, -s <s1> <s2> ...  Strategy names to use
--max-concurrency <N>           Maximum concurrent operations
--max-retries <N>               Maximum retry attempts
--memory-labels <JSON>          JSON string of labels
--database <type>               Override default database (InMemory, SQLite, AzureSQL)
--log-level <level>             Override default log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
```

## Session History

Track and review all scenario runs in your session:

```bash
# Show all runs from this session
pyrit> scenario-history

# Print details of the most recent run
pyrit> print-scenario

# Print details of a specific run (by number from history)
pyrit> print-scenario 1

# Print all runs
pyrit> print-scenario
```

Example output:

```
pyrit> scenario-history

Scenario Run History:
================================================================================
1) foundry.red_team_agent --initializers openai_objective_target load_default_datasets --strategies base64
2) garak.encoding --initializers openai_objective_target load_default_datasets --strategies rot13
3) foundry.red_team_agent --initializers openai_objective_target load_default_datasets -s jailbreak
================================================================================

Total runs: 3

Use 'print-scenario <number>' to view detailed results for a specific run.
```

## Interactive Exploration

The shell excels at interactive testing workflows:

```bash
# Start shell with defaults
pyrit_shell --database InMemory --initializers openai_objective_target load_default_datasets

# Quick exploration
pyrit> list-scenarios
pyrit> run garak.encoding --strategies base64
pyrit> run garak.encoding --strategies rot13
pyrit> run garak.encoding --strategies morse_code

# Review and compare
pyrit> scenario-history
pyrit> print-scenario 1
pyrit> print-scenario 2
```

## Shell Benefits

- **Fast Execution**: PyRIT modules load once at startup (typically 5-10 seconds), making subsequent commands instant
- **Session Tracking**: All runs are stored in history for easy comparison
- **Interactive Workflow**: Perfect for iterative testing and debugging
- **Persistent Context**: Default settings apply across multiple runs
- **Tab Completion**: Command and argument completion (if supported by your terminal)

## Tips

1. **Set defaults at startup** to avoid repeating options:
   ```bash
   pyrit_shell --database InMemory --log-level INFO
   ```

2. **Use short strategy aliases** with `-s`:
   ```bash
   pyrit> run foundry.red_team_agent --initializers openai_objective_target load_default_datasets -s base64 rot13
   ```

3. **Review history regularly** to track what you've tested:
   ```bash
   pyrit> scenario-history
   ```

4. **Print specific results** to compare outcomes:
   ```bash
   pyrit> print-scenario 1  # baseline run
   pyrit> print-scenario 3  # modified run
   ```

## Exit the Shell

```bash
pyrit> exit
```

Or use `quit` or `q`.
