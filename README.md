# agentic-testing-framework

Evaluation harness for testing OpenClaw agent skills via Langfuse datasets.

## Components

- `src/eval_harness.py` — Runs batch evaluations: sends dataset item prompts to an OpenClaw agent, records traces and scores in Langfuse
- `src/eval_report.py` — Aggregates and reports on experiment scores, with per-item breakdowns and comparison support
- `src/dataset_sync.py` — Syncs an eval.yaml file to a Langfuse dataset (upsert by id, archive removed items, returns version timestamp)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables

- `LANGFUSE_PUBLIC_KEY` — Langfuse API public key
- `LANGFUSE_SECRET_KEY` — Langfuse API secret key

## Usage

### Run an evaluation

```bash
python src/eval_harness.py \
  --dataset linear-skill-evaluation \
  --run-name linear-improved \
  --prompt-prefix "Read the linear-improved skill from available_skills. Then " \
  --repeat 10 \
  --timeout 180 \
  --experiment-concurrency 5 \
  --item-concurrency 3
```

### eval.yaml schema

Eval files support two `expected_output` formats:

#### Structured format (recommended)

```yaml
dataset: linear-create-sample-sync
description: "Sample eval dataset"
items:
  - id: happy-path
    input: |
      Propose an issue for the following: the Meta listing import in
      Clinical Media keeps timing out when we try to sync more than 500
      listings at once. Do NOT create the issue.
    expected_output:
      behavior: |
        The agent classifies correctly, writes a tight title, uses the Bug
        description template, checks projects, matches to "Meta Ads
        Integration", presents confirmation.
      scoring_type: pass_fail
      scoring_criteria:
        - "Correctly classified as a Bug"
        - "Title is specific and under 80 characters"
        - "Uses the Bug description template structure"
        - "Project matched to 'Meta Ads Integration'"
        - "Confirmation summary presented before creation"
      scoring_rules: |
        Score 10 if all criteria pass.
        Subtract 1.5 for each failed criterion.
        Truncated responses cap at 3.
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `behavior` | yes | Prose description of expected agent behavior |
| `scoring_type` | yes | How to evaluate (`pass_fail`, `rubric`, `similarity`, `exact_match`, `custom` — only `pass_fail` implemented so far) |
| `scoring_criteria` | yes for `pass_fail` | List of criterion strings (pass/fail checklist items) |
| `scoring_rules` | no | Prose instructions for the judge LLM on combining criteria into a final score |

#### Legacy flat-string format (still supported)

```yaml
items:
  - id: happy-path
    input: "Prompt text here"
    expected_output: "What a correct response looks like"
```

### Sync an eval.yaml to Langfuse

```bash
# Sync items from an eval.yaml file to a Langfuse dataset
python src/dataset_sync.py --file skills/linear-create/eval.yaml

# Dry run (parse and show sync plan without write calls;
# makes a read-only GET for archive preview if credentials are available)
python src/dataset_sync.py --file skills/linear-create/eval.yaml --dry-run

# Dry run with no API calls at all (skip archive preview)
python src/dataset_sync.py --file skills/linear-create/eval.yaml --dry-run --no-preview
```

The script accepts both YAML (`.yaml`) and JSON (`.json`) files. It prints a version timestamp (ISO-8601 UTC) as its final output line on success. Use this with `get_dataset(version=<timestamp>)` to pin experiment runs to the exact dataset state.

### Generate a report

```bash
# All experiments for a dataset
python src/eval_report.py --dataset linear-skill-evaluation

# Filter by time range
python src/eval_report.py --dataset linear-skill-evaluation \
  --since 2026-07-22T15:10:00Z \
  --until 2026-07-22T16:00:00Z

# Per-item breakdown
python src/eval_report.py --dataset linear-skill-evaluation --per-item
```

## Options

### eval_harness.py

| Flag | Description |
|------|-------------|
| `--dataset` | Langfuse dataset name |
| `--run-name` | Experiment run name prefix |
| `--prompt-prefix` | Text prepended to each dataset item prompt |
| `--model` | Model override (e.g. `anthropic/claude-opus-4-8`) |
| `--repeat` | Number of experiment runs (default: 1) |
| `--timeout` | Per-item timeout in seconds (default: 120) |
| `--experiment-concurrency` | Parallel experiments (default: 3) |
| `--item-concurrency` | Parallel items within an experiment (default: 2) |
| `--langfuse-host` | Langfuse host URL (default: http://10.18.32.57:3000) |
| `--agent` | OpenClaw agent ID (default: main) |
| `--item-id` | Run only a specific dataset item by ID (partial match) |

### eval_report.py

| Flag | Description |
|------|-------------|
| `--dataset` | Langfuse dataset name |
| `--prefix` | Filter experiments by name prefix |
| `--since` | Only scores after this ISO timestamp |
| `--until` | Only scores before this ISO timestamp |
| `--per-item` | Show per-item aggregation |
| `--compare` | Compare two batches by prefix (2 args) |
| `--langfuse-host` | Langfuse host URL |

### dataset_sync.py

| Flag | Description |
|------|-------------|
| `--file` | Path to the eval.yaml file to sync (required) |
| `--langfuse-host` | Langfuse host URL (default: http://10.18.32.57:3000) |
| `--dry-run` | Parse and show sync plan without write calls (read-only GET for archive preview if credentials available) |
| `--no-preview` | Skip read-only archive preview in dry-run mode (no API calls at all) |
