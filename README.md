# agentic-testing-framework

Evaluation harness for testing OpenClaw agent skills via Langfuse datasets.

## Components

- `src/eval_harness.py` — Runs batch evaluations: sends dataset item prompts to an OpenClaw agent, records traces and scores in Langfuse
- `src/eval_report.py` — Aggregates and reports on experiment scores, with per-item breakdowns and comparison support

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
| `--langfuse-host` | Langfuse host URL (default: http://localhost:3000) |
| `--agent` | OpenClaw agent ID (default: main) |
| `--model` | Model override (e.g. `anthropic/claude-opus-4-8`) |
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