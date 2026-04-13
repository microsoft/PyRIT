# Attack

The Attack is a top-level component that red team operators will interact with the most. It is responsible for telling PyRIT which endpoints to connect to and how to send prompts. It can be thought of as the component that executes an attack technique.

An Attack is made up of four components:

```{mermaid}
flowchart LR
    A(["Attack Strategy <br>"])
    A --consumes--> B(["Attack Context <br>"])
    A --takes in as parameters within __init__--> D(["Attack Configurations (Adversarial, Scoring, Converter)"])
    A --produces--> C(["Attack Result <br>"])
```

To execute an Attack, one generally follows this pattern:
1. Create an **attack context** containing state information (i.e. attack objective, memory labels, prepended conversations, seed prompts)
2. Initialize an **attack strategy** (with optional **attack configurations** for converters, scorers, and adversarial chat targets)
3. _Execute_ the attack strategy with the created context (which  includes the objective and often optionally prepended_conversations and next_message)
4. Receive and process the **attack result**

## Types of Attacks

- **Single-Turn Attacks**: Single-turn attacks typically send prompts to a target endpoint to try to achieve a specific objective within a single turn. These attack strategies evaluate the target response using optional scorers to determine if the objective has been met.

- **Multi-Turn Attacks**: Multi-turn attacks introduce an iterative attack process where an adversarial chat model generates prompts to send to a target system, attempting to achieve a specified objective over multiple turns. This strategy evaluates the response using a scorer to determine if the objective has been met and continues iterating until the objective is met or a maximum numbers of turns is attempted. These types of attacks tend to work better than single-turn attacks in eliciting harm if a target endpoint keeps track of conversation history. Nonetheless, multi-turn attacks can be useful on targets that only accept individual prompts as opposed to conversations. The Tree of Attacks with Pruning [@mehrotra2023tap] strategy is a good example that was developed for this use case.

Single-turn attacks differ from multi-turn attacks because:
1. They do not require an adversarial configuration (this is where you would set the adversarial chat target in multi-turn attacks)
2. The objective of the attack is attempted within one (additional) turn. Some attacks prepare the conversation by sending a predetermined set of messages (potentially multiple turns) that align with the attack strategy before the user's first new prompt is sent.


## Component Diagrams
See the below diagrams for more details of the components:
```{mermaid}
flowchart LR
    subgraph AttackStrategy["AttackStrategy(Strategy)"]
        S_psa1["FlipAttack"]
        S_psa2["ContextComplianceAttack"]
        S_psa3["ManyShotJailbreakAttack"]
        S_psa4["RolePlayAttack"]
        S_psa5["SkeletonKeyAttack"]
        S_psa["PromptSendingAttack"]
        S_single["SingleTurnAttackStrategy (ABC)"]
        S_c["CrescendoAttack"]
        S_r["RedTeamingAttack"]
        s_t["TreeOfAttacksWithPruningAttack (aka TAPAttack)"]
        S_multi["MultiTurnAttackStrategy (ABC)"]
    end

    S_psa --> S_psa1
    S_psa --> S_psa2
    S_psa --> S_psa3
    S_psa --> S_psa4
    S_psa --> S_psa5
    S_single --> S_psa
    S_multi --> S_c
    S_multi --> S_r

```

```{mermaid}
flowchart LR
    subgraph AttackContext["AttackContext(StrategyContext) <br>(attack/core/attack_strategy.py)"]
        C_s["SingleTurnAttackContext <br>(attack/single_turn/single_turn_attack_strategy.py)"]
        a["conversation_id"]
        b["seed_group"]
        c["..."]
        C_m["MultiTurnAttackContext <br>(attack/multi_turn/multi_turn_attack_strategy.py)"]
        A["custom_prompt"]
        B["..."]
        C_o["objective"]
        C_mem["memory_labels"]
        C_rel["related_conversations"]
        C_st["start_time"]
    end

    C_s-->C_o
    C_s-->C_mem
    C_s-->C_rel
    C_s-->C_st
    C_m-->B
    C_m-->A
    C_s-->c
    C_s-->a
    C_s-->b
    C_m-->C_o
    C_m-->C_mem
    C_m-->C_rel
    C_m-->C_st
```

```{mermaid}
flowchart LR
    subgraph AttackConfig["Attack Configurations"]
        Adv["AttackAdversarialConfig"]
        Adv_target["target"]
        Adv_sys["system_prompt_path"]
        Adv_seed["seed_prompt"]
        Scoring["AttackScoringConfig"]
        Scoring_obj["objective_scorer"]
        Scoring_ref["refusal_scorer"]
        Scoring_aux["auxiliary_scorers"]
        Scoring_misc["..."]
        Convert["AttackConverterConfig(StrategyConverterConfig)"]
        Convert_req["request_converters"]
        Convert_resp["response_converters"]
    end

    Adv-->Adv_target
    Adv-->Adv_sys
    Adv-->Adv_seed
    Convert-->Convert_req
    Convert-->Convert_resp
    Scoring-->Scoring_obj
    Scoring-->Scoring_ref
    Scoring-->Scoring_aux
    Scoring-->Scoring_misc
```

```{mermaid}
flowchart LR
    subgraph AttackResult["AttackResult(StrategyResult) <br>(pyrit/models/attack_result.py)"]
        a["conversation_id"]
        b["objective"]
        c["attack_identifier"]
        d["last_response"]
        e["last_score"]
        f["executed_turns"]
        g["execution_time_ms"]
        h["outcome"]
        i["outcome_reason"]
        j["related_conversations"]
        k["metadata"]
    end
```

The following subsections of this documentation will illustrate the different kinds of attacks within PyRIT.
