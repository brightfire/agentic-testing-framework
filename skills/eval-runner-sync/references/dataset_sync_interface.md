# Dataset Sync Script Interface Reference

**Script:** `~/repos/agentic-testing-framework/src/dataset_sync.py`
**Issue:** DEV-543

## CLI Usage

```bash
python src/dataset_sync.py --file <path-to-eval.yaml> [options]
```

### Arguments

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--file` | Yes | — | Path to the eval.yaml file to sync |
| `--langfuse-host` | No | `http://localhost:3000` | Langfuse host URL |
| `--dry-run` | No | `False` | Parse and show sync plan without write calls |
| `--output-manifest` | No | — | Path to write a JSON manifest file with per-item timestamps |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes | Langfuse API public key |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse API secret key |

## Output

- **stdout/stderr:** Log lines with item-level operation counts (upserted/archived/failed)
- **Manifest file** (when `--output-manifest` is provided): JSON file with this structure:

```json
{
  "dataset": "<langfuse-dataset-name>",
  "synced_at": "<ISO-8601 UTC timestamp>",
  "items": [
    {
      "id": "<namespaced-api-id>",
      "timestamp": "<ISO-8601 UTC server timestamp>"
    }
  ]
}
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (all items synced, manifest written if requested) |
| 1 | Failure (missing env vars, file not found, YAML parse error, item failures, or manifest write failure) |

