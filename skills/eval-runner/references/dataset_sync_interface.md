# Dataset Sync Script Interface Reference

**Script:** `~/repos/agentic-testing-framework/src/dataset_sync.py`

## CLI Usage

```bash
python src/dataset_sync.py --file <path-to-eval.yaml> [options]
```

### Arguments

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--file` | Yes | — | Path to the eval.yaml file to sync |
| `--langfuse-host` | No | `http://10.18.32.57:3000` | Langfuse host URL |
| `--dry-run` | No | `False` | Parse and show what would be synced without API calls |
| `--output-manifest` | No | — | Write a JSON manifest file (per-item timestamps) at the given path after sync |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes | Langfuse API public key |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse API secret key |

## Output

- **stdout:** Sync progress logs. When `--output-manifest <path>` is passed,
  the **last line of stdout** is the manifest file path (for programmatic
  capture). All other lines are human-readable log output.
- **stderr:** Empty (logs go to stdout).

### Manifest File

When `--output-manifest <path>` is passed, a JSON file is written containing
per-item timestamps from Langfuse server responses. The script prints the
manifest path as the last line of stdout. This file is passed to the
eval harness to pin experiment runs to exact dataset state.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (manifest file path printed to stdout) |
| 1 | Failure (missing env vars, file not found, YAML parse error, or item operation failures) |

Note: `--dry-run` always exits 0 without making API calls.

## eval.yaml Schema

```yaml
dataset: <langfuse-dataset-name>
description: <human-readable description of the eval>
items:
  - id: <unique-string-id>
    input: <prompt text sent to the agent>
    expected_output: <what a correct response looks like>
```

## Manifest File Usage

Manifest files contain per-item server timestamps from Langfuse. These are
passed to the execute phase to pin experiment runs to exact dataset state:
- `get_dataset(version=<timestamp>)` returns the dataset as it was at that point
- Enables concurrent test runs without interference
- `manifest_before` pins the "before" dataset state
- `manifest_after` pins the "after" dataset state
