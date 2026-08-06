# Report Format Reference

Detailed reference for the eval-runner Report Phase output format, verdict criteria, and improvement suggestion generation.

## JSON Output Schema

`eval_report.py --json` produces the following structure:

```json
{
  "dataset": "<dataset-name>",
  "variants": [
    {
      "label": "<prefix>",
      "composite": {"avg": <float>, "min": <float>, "max": <float>, "pass_rate": <float>},
      "dimensions": {
        "<score_name>": {"avg": <float>, "min": <float>, "max": <float>, "pass_rate": <float>, "count": <int>}
      },
      "items": {
        "<item_id>": {
          "avg": <float>,
          "dimensions": {"<score_name>": {"avg": <float>, "count": <int>}},
          "run_count": <int>
        }
      },
      "total_runs": <int>
    }
  ],
  "deltas": {
    "per_item": {
      "<item_id>": {
        "composite": <float>,
        "dimensions": {"<score_name>": <float>}
      }
    },
    "overall": {
      "composite": <float>,
      "dimensions": {"<score_name>": <float>}
    }
  }
}
```

### Field Descriptions

| Field | Type | Description |
|-------|------|-------------|
| `dataset` | string | Langfuse dataset name |
| `variants[].label` | string | Experiment name prefix identifying the variant |
| `variants[].composite` | object | Aggregate stats across all dimensions |
| `variants[].composite.avg` | float | Mean of all scores for this variant |
| `variants[].composite.min` | float | Minimum score |
| `variants[].composite.max` | float | Maximum score |
| `variants[].composite.pass_rate` | float | Percentage of scores >= 10.0 |
| `variants[].dimensions` | object | Per-dimension breakdown keyed by score_name |
| `variants[].dimensions[].count` | int | Number of scores in this dimension |
| `variants[].items` | object | Per-item scores keyed by item_id |
| `variants[].items[].run_count` | int | Number of runs for this item |
| `variants[].total_runs` | int | Total number of scores across all items and dimensions |
| `deltas.per_item` | object | Per-item deltas: variant[n] avg - variant[0] avg |
| `deltas.overall` | object | Overall deltas across all items |
| `deltas.overall.composite` | float | Overall composite delta |
| `deltas.overall.dimensions` | object | Per-dimension overall deltas |

### Delta Computation

Deltas are computed as `variant[n] - variant[0]` for each item and dimension, where `variant[0]` is the baseline (typically the base branch). When there are multiple non-baseline variants, the delta is the average of all non-baseline variants minus the baseline.

## Verdict Criteria

The verdict uses a configurable threshold (default **0.5 points** on a 0–10 scale where 10.0 is passing) to identify meaningful changes. Pass `--threshold <value>` to eval_report.py to adjust sensitivity.

| Verdict | Criteria |
|---------|----------|
| **Improvement** | No variant regressed significantly (delta > -threshold on any dimension or item), AND at least one variant improved significantly (delta > +threshold) |
| **Regression** | Any variant regressed significantly (delta < -threshold) on composite or any dimension |
| **Neutral** | All deltas within ±threshold — no significant changes |

The skill should note the threshold used in the report so the reader understands what "significant" means.

## Report Template (Markdown)

```markdown
## Eval Results: <skill-name> — <dataset-name>

### Variants
| Variant | Composite | <dim-1> | <dim-2> | <dim-3> |
|---------|-----------|---------|---------|---------|
| <label-a> | <avg> | <avg> | <avg> | <avg> |
| <label-b> | <avg> | <avg> | <avg> | <avg> |
| Delta | <+/-delta> | <+/-delta> | <+/-delta> | <+/-delta> |

### Per-Item Deltas
| Item | <label-a> | <label-b> | Delta | Notes |
|------|-----------|-----------|-------|-------|
| <item-id> | <avg> | <avg> | <+/-delta> | ⚠️ Regression in <dimension> |

### Verdict: <Improvement | Regression | Neutral>

<Explanation>

### Improvement Suggestions
- **<item-id>** (<dimension>): <suggestion>
  - Related change: <diff reference>
```

## Improvement Suggestion Generation

For each item where any dimension's delta < -threshold (default 0.5):

1. **Identify the regressed dimension** — which `score_name` has a delta < -threshold (default 0.5)
2. **Obtain the skill diff** — run `git diff <base-ref> <head-ref> -- <skill-path>` to see what changed between variants
3. **Review the diff** — look for changes that could affect the regressed dimension:
   - `task_completion` regressions: look for removed steps, weakened instructions, or changes that could cause the agent to skip required actions
   - `accuracy` regressions: look for changes to factual content, criteria, or evaluation logic
   - `efficiency` regressions: look for added steps, increased verbosity, or changes that could slow down execution
4. **Suggest a specific fix** — reference the exact change in the diff and suggest a modification
5. **Note unrelated regressions** — if the regression is in a dimension unrelated to the skill changes, note that it may be noise (e.g., evaluator variance, model non-determinism)

### Obtaining the Skill Diff

```bash
# For PR-triggered runs
git diff <base-ref> <head-ref> -- <skill-path>

# Example
git diff main claw/vash/skill-improvement -- skills/linear-create/
```

The diff shows what changed in the skill between the two variants. This is essential for generating targeted improvement suggestions.

### Examples of Improvement Suggestions

**Example 1: task_completion regression**

```
- **item-3** (task_completion): The skill removed the "verify output" step from the procedure. This likely caused the agent to skip verification, leading to incomplete outputs.
  - Related change: Removed lines 45-47 in SKILL.md that instructed verification.
  - Suggestion: Re-add the verification step or ensure the replacement procedure includes an equivalent check.
```

**Example 2: accuracy regression, unrelated to skill changes**

```
- **item-5** (accuracy): Score dropped by 3.2 points, but the skill diff shows no changes to factual content or evaluation criteria. The regression may be evaluator noise or model non-determinism.
  - Related change: Only formatting changes in SKILL.md (whitespace, line wrapping).
  - Suggestion: Re-run the eval to confirm. If the regression persists, investigate model behavior changes.
```

**Example 3: efficiency regression**

```
- **item-1** (efficiency): The skill added a new "gather context" step that adds 3 additional tool calls per run. This increased latency without improving output quality.
  - Related change: Added "Read the skill's references/ directory before starting" at line 12.
  - Suggestion: Consider making the context-gathering optional or moving it to a conditional step that only runs when needed.
```

## CLI Flags (eval_report.py)

| Flag | Description |
|------|-------------|
| `--dataset <name>` | Langfuse dataset name (required) |
| `--prefix <prefix>` | Filter experiments by name prefix (single-variant mode) |
| `--compare <a> <b>` | Compare exactly 2 experiment batches (legacy) |
| `--variants <p1> <p2> ...` | Compare N experiment name prefixes (multi-variant mode) |
| `--by-dimension` | Group scores by score_name (dimension) |
| `--json` | Output structured JSON instead of text tables |
| `--per-item` | Show per-dataset-item aggregation (single-variant mode) |
| `--since <iso>` | Only include scores after this timestamp |
| `--until <iso>` | Only include scores before this timestamp |
| `--threshold <float>` | Delta threshold for flagging improvements/regressions (default: 0.5) |
| `--langfuse-host <url>` | Langfuse host URL (default: http://localhost:3000) |

### Flag Compatibility

- `--variants` and `--compare` are mutually exclusive — error if both are passed
- `--by-dimension` works with `--variants`, `--compare`, and `--prefix` modes
- `--json` works with `--variants`, `--compare`, and `--prefix` modes
- `--per-item` works with `--prefix` mode (single-variant)
- `--prefix` works in single-variant mode; ignored when `--variants` or `--compare` is used
