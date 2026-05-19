# Using Uncensored Models in PyRIT

## Problem

OpenAI's GPT-4o and other commercial models refuse to perform adversarial tasks due to safety guardrails. This limits red-teaming capabilities in PyRIT. When attempting prompt injection strategies, these models cite "unable to assist with this task" and fine-tuning with adversarial examples gets blocked by moderation systems.

## Solution: Alternative Models

| Model | Source | Setup Difficulty | Safeguards | Best For |
|-------|--------|------------------|-----------|----------|
| Llama 2 Uncensored | HuggingFace | Easy | Low | Testing jailbreaks |
| Mistral 7B | HuggingFace | Easy | Medium | Fast adversarial testing |
| Dolphin 2.5 Mixtral | HuggingFace | Medium | Low | Complex reasoning attacks |

## Setup Instructions

### Llama 2 Uncensored

1. Install Ollama from https://ollama.ai
2. Run: `ollama pull llama2-uncensored`
3. Start Ollama service: `ollama serve`
4. In PyRIT, configure as local endpoint on port 11434

### Mistral 7B

1. Install Ollama
2. Run: `ollama pull mistral`
3. Start Ollama service: `ollama serve`
4. Use as local inference endpoint

### Dolphin 2.5 Mixtral

1. Install Ollama
2. Run: `ollama pull dolphin-mixtral`
3. Start Ollama service
4. Configure in PyRIT with local endpoint

## Trade-offs

- Local models require GPU for reasonable speed (8GB+ VRAM recommended)
- Fewer safeguards means ethical responsibility on user
- May produce lower quality outputs than GPT-4o
- Local deployment offers privacy benefits
- No API costs, but compute costs higher

## Recommendations

Start with **Mistral 7B** for balance of speed, capability, and ease of setup. Use Llama 2 Uncensored if you need more aggressive adversarial behavior. Dolphin Mixtral for complex multi-turn attacks.

## References

See PyRIT documentation on PromptTarget configuration for integrating these models into red-teaming workflows.
