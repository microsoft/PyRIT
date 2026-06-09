# 10. Memory Schema Diagram

Our memory contains multiple components. This diagram  shows a mapping of our database schema and how our components map together! The arrows indicate the values that map one database to another.

```{mermaid}
flowchart LR
 subgraph EmbeddingData["EmbeddingData"]
        E_id["id (UUID)"]
        E_embedding["embedding (NULL)"]
        E_embedding_type_name["embedding_type_name (VARCHAR)"]
  end
 subgraph SeedPromptEntries["SeedPromptEntries"]
        S_id["id (UUID)"]
        S_value["value (VARCHAR)"]
        S_value_sha256["value_sha256 (VARCHAR)"]
        S_data_type["data_type (VARCHAR)"]
        S_name["name (VARCHAR)"]
        S_dataset_name["dataset_name (VARCHAR)"]
        S_harm_categories["harm_categories (VARCHAR)"]
        S_description["description (VARCHAR)"]
        S_authors["authors (VARCHAR)"]
        S_groups["groups (VARCHAR)"]
        S_source["source (VARCHAR)"]
        S_date_added["date_added (TIMESTAMP)"]
        S_added_by["added_by (VARCHAR)"]
        S_prompt_metadata["prompt_metadata (VARCHAR)"]
        S_parameters["parameters (VARCHAR)"]
        S_prompt_group_id["prompt_group_id (UUID)"]
        S_sequence["sequence (INTEGER)"]
  end
 subgraph PromptMemoryEntries["PromptMemoryEntries"]
        P_role["role (VARCHAR)"]
        P_id["id (UUID)"]
        P_original_value["original_value (VARCHAR)"]
        P_original_value_sha256["original_value_sha256 (VARCHAR)"]
        P_original_value_data_type["original_value_data_type (VARCHAR)"]
        P_conversation_id["conversation_id (VARCHAR)"]
        P_sequence["sequence (INTEGER)"]
        P_timestamp["timestamp (TIMESTAMP)"]
        P_labels["labels (VARCHAR)"]
        P_prompt_metadata["prompt_metadata (VARCHAR)"]
        P_converter_identifiers["converter_identifiers (VARCHAR)"]
        P_prompt_target_identifier["prompt_target_identifier (VARCHAR)"]
        P_attack_identifier["attack_identifier (VARCHAR)"]
        P_response_error["response_error (VARCHAR)"]
        P_converted_value_data_type["converted_value_data_type (VARCHAR)"]
        P_converted_value["converted_value (VARCHAR)"]
        P_converted_value_sha256["converted_value_sha256 (VARCHAR)"]
        P_original_prompt_id["original_prompt_id (UUID)"]
  end
 subgraph ScoreEntries["ScoreEntries"]
        Sc_id["id (UUID)"]
        Sc_prompt_request_response_id["prompt_request_response_id (VARCHAR)"]
        Sc_score_value["score_value (VARCHAR)"]
        Sc_score_value_description["score_value_description (VARCHAR)"]
        Sc_score_type["score_type (VARCHAR)"]
        Sc_score_category["score_category (VARCHAR)"]
        Sc_score_rationale["score_rationale (VARCHAR)"]
        Sc_score_metadata["score_metadata (VARCHAR)"]
        Sc_scorer_class_identifier["scorer_class_identifier (VARCHAR)"]
        Sc_timestamp["timestamp (TIMESTAMP)"]
        Sc_task["task (VARCHAR)"]
  end
    S_value_sha256 -- N:N relationship to query --> P_original_value_sha256
    P_id -- 1:N relationship to query --> Sc_prompt_request_response_id

    style S_value_sha256 fill:#ff8800ff
    style P_id fill:#14a519ff
    style P_original_value_sha256 fill:#ff8800ff
    style Sc_prompt_request_response_id fill:#14a519ff
    linkStyle 0 stroke:#ff8800ff,fill:none
    linkStyle 1 stroke:#14a519ff
```
