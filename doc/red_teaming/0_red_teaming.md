# Learn AI Red Teaming

New to AI red teaming and not sure where to start? This section is for you. It is not a PyRIT tutorial. It is the advice we give when someone asks us how to get into the field, and it applies whichever tools you end up using.

The short version:

1. **Understand the system before you attack it.** A handful of questions up front decides whether your testing is useful.
2. **Mix human-led and automated red teaming.** Automation helps you scale. It does not tell you that your system is safe.
3. **Learn by doing, and write down what you learn.** Set up an endpoint, attack it, make it harder, attack it again.

If you would rather start with guides, talks, or hands-on practice, go straight to [Learning Resources](#learning-resources) at the end of this page.

## What AI Red Teaming Is

AI red teaming means probing an AI system to uncover its failure modes, including the ones that affect benign users and not only the ones an adversary would exploit. That covers security failures such as prompt injection or data exfiltration, and safety failures such as harmful content or bad advice to a vulnerable user. Both adversarial and benign use can produce harmful output, so both are in scope.

Two things it is not:

- **It is not measurement.** Finding five examples of a failure tells you the failure exists. It does not tell you how often it happens. Red teaming finds the problems; systematic measurement tells you how common they are, and mitigation work addresses them. "AI red teaming is not safety benchmarking" is one of the eight lessons in *Lessons From Red Teaming 100 Generative AI Products* [@bullwinkel2025airtlessons].
- **It is not finished after one round.** Models change, defenses change, and attackers adapt. The same paper closes with "The work of securing AI systems will never be complete."

## Questions to Ask Before You Start

Microsoft's [Planning red teaming for large language models (LLMs) and their applications](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/red-teaming) is a good overview of what to think through up front. It is generic, which is why it still applies years after it was first written. The questions below follow its structure.

**What is the system, and what can it do?**
A bare model behind an API fails differently than an application with a system prompt, retrieval over private documents, and tools that take actions. List what the system can read, what it can do, and who uses it. That list tells you what could go wrong. "Understand what the system can do and where it is applied" is the first lesson in [@bullwinkel2025airtlessons] for a reason.

**What are you worried about?**
Write down the harms that matter for this system in this context. If you do not have a list yet, start with open-ended exploration and build one from what you find. Later rounds can then probe each item on the list and add new ones as they surface.

**Which layer are you testing?**
You can test the base model through its API, the full application through its user interface, or both. You can test with mitigations in place and without them. These give different answers, so record which one you tested. Test as close to the production setup as you can before you draw conclusions about production.

**Who should do the testing?**
A group with mixed backgrounds finds more than a group of similar people. Security experience helps with jailbreaks and injection. Subject matter expertise helps with everything else: a medical expert sees problems in a health chatbot that a security engineer does not. People who use the product the way ordinary users do find the failures that ordinary users will hit.

**How will you record what you find?**
Decide this before you start. At minimum: the date, the exact input, the output, the configuration you tested, and enough of an identifier to reproduce it. A shared spreadsheet is enough. A finding you cannot reproduce or explain to someone else is not much of a finding.

**Are you allowed to test it?**
Only test systems you own or have explicit permission to test, and read the usage policies of any hosted model you use.

## Mix Human-Led and Automated Red Teaming

Many people go straight to automated runs, often because they do not have subject matter expertise on hand. We always advocate for a mix of human-led and automated red teaming.

:::{important}
If you run automation without inspecting the results and iterating on what you find, you will delude yourself into thinking that your system is safe. AI helps you scale, but it is not perfect. The attacks will miss areas that could be attacked. The scorers will misjudge some responses.
:::

Each approach covers for the other's weaknesses:

| | Human-led | Automated |
|---|---|---|
| **Good at** | Finding new kinds of failures. Judging context, tone, and severity. Applying domain and cultural knowledge. | Scale and repetition. Covering many known techniques. Re-testing after every change. |
| **Weak at** | Scale and consistency. It is slow and tiring. | It only tries what someone already thought of. Its judgments are sometimes wrong. It cannot tell you what it did not test. |
| **In PyRIT** | [GUI](../gui/0_gui.md) | [Scanner](../scanner/0_scanner.md) and [Framework](../code/framework.md) |

Here is what goes wrong when you only automate:

- **The attacks miss things.** Datasets and attack techniques encode what someone already knew to look for. The risks specific to your application, its tools, its data, and its users are not in a generic dataset.
- **The scorers misjudge.** Automated scoring usually means one model judging another model's output. Judges flag harmless responses and let harmful ones through. Our blog post [Scoring Scorers](../blog/2026_04_14_scoring_scorers.md) describes how we found this out ourselves, and [Scorer Metrics](../code/scoring/4_scorer_metrics.ipynb) shows how to check how well a scorer agrees with human labels.
- **A single number hides all of this.** An attack success rate depends on the objectives you picked, the techniques you ran, how many attempts each got, and the judge. A low number means that this particular run did not find much. It does not mean there is nothing to find.

Automation is still worth it, and [@bullwinkel2025airtlessons] says so too: "Automation can help cover more of the risk landscape." It also says "The human element of AI red teaming is crucial." Both are true at once.

A loop that works in practice:

1. **Explore by hand.** Talk to the system. Find out how it behaves, where it pushes back, and what gets through.
2. **Automate what you learned.** Turn the approaches that worked into repeatable runs, and widen them with more objectives and variations.
3. **Read the results.** Open the conversations, the failures as well as the successes. Check whether you agree with the scorer.
4. **Feed it back.** What you notice becomes the next round of manual probing. Fixed issues become regression tests.

You also do not need sophisticated attacks to get started. "You don't have to compute gradients to break an AI system" is another lesson from [@bullwinkel2025airtlessons]: simple, hand-written prompts often work as well as sophisticated methods, and they are the right place to begin.

(learn-by-doing)=
## Learn by Doing

Reading only gets you so far. The fastest way to learn is to set something up, break it, defend it, and break it again, while keeping notes. One round looks like this:

1. **Set up an endpoint you are allowed to test.** Any chat model works: a hosted model you have an API key for, or an open model you run yourself. See [Installation](../getting_started/README.md) and [Targets](../code/targets/0_prompt_targets.md) for how to connect PyRIT to it.
2. **Pick an objective with a clear success condition.** A good first one: give the model a system prompt that contains a made-up secret and tells it never to reveal the secret, then try to extract it. You know exactly when you have succeeded, and nothing harmful is involved.
3. **Try it by hand.** Ask directly. Rephrase. Role play. Ask for the answer in another language or encoding. Spread the request over several turns. The [GUI](../gui/0_gui.md) is built for this kind of manual work. Note what worked and what did not.
4. **Make it harder.** Add a defense. Rewrite the system prompt to be more defensive. Add an output check that blocks any response containing the secret. Put a content filter or an input classifier such as [Prompt Shields](../code/targets/prompt_shield_target.ipynb) in front of the model.
5. **Try again.** What changed? Which of your earlier attacks stopped working? Which still work? Does a small variation get around the new defense, such as asking for the secret spelled backwards when the output check looks for the exact string?
6. **Write it down.** Record the setup, what you tried, what happened, and why you think it happened. This is the step people skip, and it is the one that turns an afternoon of poking at a model into knowledge you keep.
7. **Automate what you learned.** Apply [converters](../code/converters/0_converters.ipynb) to vary your prompts, send them in bulk with a [single-turn attack](../code/executor/1_single_turn.ipynb), and judge the responses with a [scorer](../code/scoring/0_scoring.ipynb). Then read the conversations and check whether the scorer got them right. PyRIT keeps every prompt, response, and score in [memory](../code/memory/0_memory.md), so you can go back to them.
8. **Repeat with something new.** Try a [multi-turn attack](../code/executor/2_multi_turn.ipynb). Try a different modality. Hide your instruction in a document the system reads instead of typing it yourself. Give the system a tool and see what you can make it do.

A table is enough for your notes:

| Date | Target and defenses | Objective | What I tried | Result | What I learned |
|---|---|---|---|---|---|
| | | | | | |

If you want ready-made challenges instead of your own setup, [Learning Resources](#learning-resources) lists places to practice.

## Building Your Skills

People come to AI red teaming from security, machine learning, the social sciences, and from fields that have nothing to do with computing. There is no single path. A few things help whichever way you arrive:

- **Learn how AI applications are built.** System prompts, retrieval, tools, agents. Most real findings are about the whole system, not the model alone, and "LLMs amplify existing security risks and introduce new ones" [@bullwinkel2025airtlessons].
- **Use what you already know.** Domain expertise, languages, and cultural knowledge all find failures that generic testing misses. You do not need to be a programmer to do human-led red teaming well.
- **Practice writing findings up.** A finding matters once someone else can reproduce it, understand why it matters, and act on it.
- **Read new research and reproduce it.** Reproducing a published attack against your own endpoint teaches more than reading about ten of them.
- **Do it in public.** Lab write-ups and open source contributions show what you can do. PyRIT welcomes [contributions](../contributing/README.md), and you do not need to be an experienced red teamer to make one.

The reading in [Learning Resources](#learning-resources) is free and online. The hands-on parts need access to a model, which can be an open model on your own machine.

(learning-resources)=
## Learning Resources

A short list to go with this page, last reviewed in November 2026. Spotted something that belongs here? [Open an issue or a pull request](../contributing/README.md).

### Start Here

- **[Planning red teaming for large language models (LLMs) and their applications](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/red-teaming)** (Microsoft Learn). The questions to ask before you test anything: who tests, what to test, at which layer, and how to record results.
- **[Lessons From Red Teaming 100 Generative AI Products](https://arxiv.org/abs/2501.07238)** [@bullwinkel2025airtlessons]. Eight lessons and five case studies from the Microsoft AI Red Team. For the short version, read [3 takeaways from red teaming 100 generative AI products](https://www.microsoft.com/en-us/security/blog/2025/01/13/3-takeaways-from-red-teaming-100-generative-ai-products/).

### Guides and Frameworks

- **[OWASP GenAI Red Teaming Guide](https://genai.owasp.org/resource/genai-red-teaming-guide/)** (2025) and **[GenAI Red Teaming Manual v1.0](https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/)** (2026). The guide is an orientation. The manual is an eight-phase methodology from planning to remediation that covers models, applications, multimodal interfaces, and agents.
- **[OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/)** and **[OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)**. Common vocabulary for the most frequent risks. Useful as a checklist of things to consider, not as a test plan.
- **[MITRE ATLAS](https://atlas.mitre.org/)**. A knowledge base of adversary tactics and techniques against AI systems, modeled on MITRE ATT&CK, with real-world case studies.
- **[NIST AI 600-1: Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)** (2024). The generative AI companion to the NIST AI Risk Management Framework. Good for understanding how red teaming fits into risk management as a whole.

### From the Microsoft AI Red Team

- **[Microsoft AI Red Team on Microsoft Learn](https://learn.microsoft.com/en-us/security/ai-red-team/)**. Hub page for threat modeling guidance, the failure mode taxonomy, and the AI bug bar.
- **[Microsoft AI Red Team posts on the Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/author/microsoft-ai-red-team/)**. The team's author page, with the most recent posts first.
- **[Updating the taxonomy of failure modes in agentic AI systems: What a year of red teaming taught us](https://www.microsoft.com/en-us/security/blog/2026/06/04/updating-taxonomy-failure-modes-agentic-ai-systems-year-red-teaming-taught-us/)** (blog, 2026).
- **[Mitigating Skeleton Key, a new type of generative AI jailbreak technique](https://www.microsoft.com/en-us/security/blog/2024/06/26/mitigating-skeleton-key-a-new-type-of-generative-ai-jailbreak-technique/)** (blog, 2024) [@microsoft2024skeletonkey].
- **[How Microsoft discovers and mitigates evolving attacks against AI guardrails](https://www.microsoft.com/en-us/security/blog/2024/04/11/how-microsoft-discovers-and-mitigates-evolving-attacks-against-ai-guardrails/)** (blog, 2024). Introduces Crescendo and Spotlighting.
- **[Announcing Microsoft's open automation framework to red team generative AI systems](https://www.microsoft.com/en-us/security/blog/2024/02/22/announcing-microsofts-open-automation-framework-to-red-team-generative-ai-systems/)** (blog, 2024). The post that announced PyRIT.
- **[Microsoft AI Red Team building future of safer AI](https://www.microsoft.com/en-us/security/blog/2023/08/07/microsoft-ai-red-team-building-future-of-safer-ai/)** (blog, 2023). How AI red teaming differs from traditional red teaming.
- **[Lessons From Red Teaming 100 Generative AI Products](https://www.youtube.com/watch?v=qi2DneFkRf4)** (talk, BlueHat 2024).
- **[Red Teaming LLMs and GenAI with PyRIT](https://talkpython.fm/episodes/show/521/red-teaming-llms-and-genai-with-pyrit)** (podcast, Talk Python To Me, episode 521).

### Practice

Places to try attacks against systems that were built to be attacked.

:::{important}
We do not endorse or recommend these sites, their operators, or any product or service they offer. They are listed as examples only. Any interaction is between you and the operator: read their terms, and decide for yourself what you share with them.
:::

- **[HackAPrompt](https://www.hackaprompt.com/)**. A prompt injection competition. PyRIT has a [HackAPrompt target](../code/targets/hack_a_prompt_target.ipynb) if you want to attack the challenges with code.
- **[Agent Breaker](https://play.lakera.ai/agent-breaker)** (Lakera). Browser-based challenges against mock AI applications.
- **[Web LLM attacks](https://portswigger.net/web-security/llm-attacks)** (PortSwigger Web Security Academy). Labs on attacking LLMs that are integrated into web applications, for people coming from web security.
- **[AI Village](https://aivillage.org/)**. A community for hands-on AI security education and red team practice, known for its events at DEF CON.
- **Your own endpoint.** The exercise in [Learn by Doing](#learn-by-doing) needs nothing but a model you are allowed to test.

## Where to Go Next

- 📦 [Installation](../getting_started/README.md): install and configure PyRIT
- 🖥️ [GUI](../gui/0_gui.md): human-led red teaming in the browser
- 🔍 [Scanner](../scanner/0_scanner.md): automated runs from the command line
- ❓ [FAQ](../faq.md): short answers to common questions about using PyRIT
- 💬 [Discord](https://discord.gg/9fMpq3tc8u): ask questions and share what you are learning
