# Eval Dataset Schema

Schema definition for eval dataset files used by the agentic-testing-framework. These files define the test cases, expected behavior, and scoring rules for skill evaluation.

## File Format

Eval files are JSON or YAML, containing a dataset name, optional description, and a list of items.

```json
{
  "dataset": "linear-create-sample-sync",
  "description": "Sample eval dataset for testing linear-create skill.",
  "items": [...]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `dataset` | string | Yes | Langfuse dataset name. Must be unique within the Langfuse project. |
| `description` | string | No | Human-readable description of the dataset's purpose. |
| `items` | array | Yes | List of eval items. See [Item Schema](#item-schema) below. |

---

## Item Schema

Each item represents a single test case — one prompt to send to the agent under test, with the expected behavior and scoring rules to evaluate the response.

```json
{
  "id": "happy-path",
  "input": "Propose an issue for the following: ...",
  "expected_output": { ... }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique identifier for the item within this dataset. Used for idempotent upsert/archive by `dataset_sync.py`. |
| `input` | string | Yes | The prompt sent to the agent under test. Can use YAML literal block scalar (`\|`) for multiline prompts. |
| `expected_output` | object \| string | Yes | Defines what the agent should have done and how to score it. See [Expected Output Schema](#expected-output-schema). For backward compatibility, a flat string is accepted — the harness treats it as the entire scoring guidance passed verbatim to the judge LLM. |

---

## Expected Output Schema

The `expected_output` object is the core of the eval definition. It tells the harness what the agent should have done and provides explicit rules for scoring so results are reproducible across runs.

```json
{
  "behavior": "The agent classifies correctly, writes a tight title...",
  "scoring_type": "pass_fail",
  "scoring_criteria": [
    "Correctly classified as a Bug",
    "Title is specific and under 80 characters"
  ],
  "scoring_rules": "Score 10 * (passed criteria / total criteria).\nTruncated responses cap at 3."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `behavior` | string | Yes (structured) | Prose description of what the agent should have done. Provides context to the judge LLM — not parsed programmatically. |
| `scoring_type` | string | Yes (structured) | How criteria are evaluated. See [Scoring Types](#scoring-types). |
| `scoring_criteria` | array | Depends on type | List of criteria for evaluation. For `pass_fail`, each entry is a string (a yes/no question). Can also be an object with a `scoring_type` override — see [Per-Criterion Overrides](#per-criterion-overrides). |
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

### `pass_fail` (default)

Each criterion is evaluated as a yes/no question. The judge LLM receives the list of criteria and the `scoring_rules`, evaluates the agent's response against each criterion, and produces a final score.

This is the most common scoring type for skill evals.

```json
{
  "scoring_type": "pass_fail",
  "scoring_criteria": [
    "Correctly classified as a Bug",
    "Title is specific and under 80 characters",
    "Uses the Bug description template structure"
  ],
  "scoring_rules": "Score 10 * (passed criteria / total criteria)."
}
```

### Future scoring types

The schema is designed to accommodate additional scoring types without structural changes:

| Type | Description | Criteria format |
|------|-------------|-----------------|
| `pass_fail` | Yes/no checklist (current) | List of strings |
| `rubric` | Judge assigns a score from a defined range | List of strings as qualitative descriptors |
| `similarity` | Score based on semantic/string similarity to `behavior` | No criteria needed |
| `exact_match` | Deterministic check — output must match `behavior` exactly | No criteria needed |
| `custom` | Evaluator code handles it | Passthrough — harness doesn't interpret |

New types are added by the harness implementing a new judge prompt template for that type. The schema doesn't need to change.

---

## Per-Criterion Overrides

By default, all criteria in an item use the item-level `scoring_type`. Individual criteria can override this by providing an object instead of a string:

```json
{
  "scoring_type": "pass_fail",
  "scoring_criteria": [
    "Correctly classified as a Bug",
    "Title is specific and under 80 characters",
    {
      "scoring_type": "rubric",
      "description": "Description quality is clear and actionable",
      "scale": [1, 2, 3, 4, 5]
    }
  ],
  "scoring_rules": "Score 10 * (passed criteria / total criteria). Rubric criterion weighted equally with pass/fail criteria."
}
```

In this example, the first two criteria are evaluated as yes/no. The third is evaluated on a 1-5 rubric scale. The `scoring_rules` prose tells the judge LLM how to combine all three into a final score.

The harness detects per-criterion overrides by checking whether a criterion is a string or an object. For objects, it reads the criterion's `scoring_type` and builds the judge prompt accordingly. For strings, it uses the item-level default.

---

## Backward Compatibility

The harness and `dataset_sync.py` accept both formats:

**Old format (flat strings):**
```json
{
  "id": "happy-path",
  "input": "Propose an issue for...",
  "expected_output": "The agent classifies correctly. Score 10 if all pass..."
}
```

**New format (structured objects):**
```json
{
  "id": "happy-path",
  "input": "Propose an issue for...",
  "expected_output": {
    "behavior": "The agent classifies correctly...",
    "scoring_type": "pass_fail",
    "scoring_criteria": [...],
    "scoring_rules": "Score 10 if all pass..."
  }
}
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
