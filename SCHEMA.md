# Eval Dataset Schema

Schema definition for eval dataset files used by the agentic-testing-framework. These files define the test cases, expected behavior, and scoring rules for skill evaluation.

## File Format

Eval files are YAML, containing a dataset name, optional description, and a list of items.

```yaml
dataset: linear-create-sample-sync
description: "Sample eval dataset for testing linear-create skill."
items: [...]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `dataset` | string | Yes | Langfuse dataset name. Must be unique within the Langfuse project. |
| `description` | string | No | Human-readable description of the dataset's purpose. |
| `items` | array | Yes | List of eval items. See [Item Schema](#item-schema) below. |

---

## Item Schema

Each item represents a single test case — one prompt to send to the agent under test, with the expected behavior and scoring rules to evaluate the response.

```yaml
- id: happy-path
  input: "Propose an issue for the following: ..."
  expected_output: { ... }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier for the item within this dataset. Used for idempotent upsert/archive by `dataset_sync.py`. |
| `input` | string | Yes | The prompt sent to the agent under test. Can use YAML literal block scalar (`\|`) for multiline prompts. |
| `expected_output` | object \| string | Yes | Defines what the agent should have done and how to score it. See [Expected Output Schema](#expected-output-schema). For backward compatibility, a flat string is accepted — the harness treats it as the entire scoring guidance passed verbatim to the judge LLM. |

---

## Expected Output Schema

The `expected_output` object is the core of the eval definition. It tells the harness what the agent should have done and provides explicit rules for scoring so results are reproducible across runs.

```yaml
expected_output:
  behavior: "The agent classifies correctly, writes a tight title..."
  scoring_type: pass_fail
  scoring_criteria:
    - "Correctly classified as a Bug"
    - "Title is specific and under 80 characters"
  scoring_rules: |
    Score 10 * (passed criteria / total criteria).
    Truncated responses cap at 3.
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `behavior` | string | Yes | Prose description of what the agent should have done. Provides context to the judge LLM — not parsed programmatically. |
| `scoring_type` | string | Yes | How criteria are evaluated. Currently only `pass_fail` is supported. |
| `scoring_criteria` | array | Yes | List of criteria for evaluation. Each entry is a string (a yes/no question). |
| `scoring_rules` | string | No | Prose instructions for how to combine criterion results into a final score. Passed **verbatim** to the judge LLM. The harness does not interpret this field — it just slots it into the judge prompt template. This is what keeps scoring reproducible across runs. |

### Why `scoring_rules` is prose

The judge LLM needs explicit instructions on how to score. Without them, the same output can score a 7 on one run and a 9 on another — introducing variance into what should be a deterministic evaluation.

`scoring_rules` solves this by giving the judge LLM unambiguous scoring instructions. The harness passes this string directly to the judge prompt without interpretation. The eval author is responsible for writing clear, complete rules.

Example:

```
Score 10 * (passed criteria / total criteria).
Truncated responses cap at 3.
```

This formula self-adjusts to any number of criteria — no need to update scoring rules when criteria are added or removed.

---

## Scoring Types

The `scoring_type` field tells the harness how to evaluate each criterion. The harness reads this field first, then knows which other fields to expect and how to build the judge LLM's prompt.

### `pass_fail` (current, only supported type)

Each criterion is evaluated as a yes/no question. The judge LLM receives the list of criteria and the `scoring_rules`, evaluates the agent's response against each criterion, and produces a final score.

```yaml
expected_output:
  scoring_type: pass_fail
  scoring_criteria:
    - "Correctly classified as a Bug"
    - "Title is specific and under 80 characters"
    - "Uses the Bug description template structure"
  scoring_rules: "Score 10 * (passed criteria / total criteria)."
```

Additional scoring types (e.g., `rubric`, `similarity`, `exact_match`, `custom`) may be added in the future. The `scoring_type` field is designed to be extensible — new types will be documented here as they are implemented.

---

## Backward Compatibility

The harness and `dataset_sync.py` accept both formats:

**Old format (flat strings):**
```yaml
- id: happy-path
  input: "Propose an issue for..."
  expected_output: "The agent classifies correctly. Score 10 if all pass..."
```

**New format (structured objects):**
```yaml
- id: happy-path
  input: "Propose an issue for..."
  expected_output:
    behavior: "The agent classifies correctly..."
    scoring_type: pass_fail
    scoring_criteria: [...]
    scoring_rules: "Score 10 * (passed criteria / total criteria)."
```

When `expected_output` is a string, the harness passes it verbatim to the judge LLM. When it's an object, the harness assembles the judge prompt from the structured fields. Both formats can coexist in the same dataset.

---

## Multiline Fields

In YAML eval files, use the literal block scalar (`|`) for long prose fields. This preserves newlines for readability in the source file while producing a single string value when parsed:

```yaml
items:
  - id: happy-path
    input: |
      Propose an issue for the following: the Meta listing import in
      Clinical Media keeps timing out when we try to sync more than 500
      listings at once.
    expected_output:
      behavior: |
        The agent classifies correctly, writes a tight title, uses the Bug
        description template, checks projects, matches to "Meta Ads
        Integration", presents confirmation.
      scoring_type: pass_fail
      scoring_criteria:
        - "Correctly classified as a Bug"
        - "Title is specific and under 80 characters"
      scoring_rules: |
        Score 10 * (passed criteria / total criteria).
        Truncated responses cap at 3.
```

The parsed string retains newlines (`\n`), which is fine — the judge LLM reads multi-line instructions better than one long line.
