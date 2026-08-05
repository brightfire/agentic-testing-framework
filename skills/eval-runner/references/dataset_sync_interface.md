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
| `--items` | No | — | Comma-separated list of item IDs to sync. Only these items are upserted and included in the manifest. If omitted, all items in eval.yaml are synced. |

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

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (manifest file path printed to stdout) |
| 1 | Failure (missing env vars, file not found, YAML parse error, or item operation failures) |



