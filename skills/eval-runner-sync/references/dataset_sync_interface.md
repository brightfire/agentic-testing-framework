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
| `--dry-run` | No | `False` | Parse and show sync plan without write calls (read-only GET for archive preview if credentials available) |
| `--output-manifest` | No | — | Path to write a JSON manifest file with per-item timestamps |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes | Langfuse API public key |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse API secret key |

## Output

- **stdout/stderr:** Log lines in format `[ISO-timestamp] [LEVEL] message` — includes item-level operations (upserted/archived/failed) and progress
- **Manifest file** (when `--output-manifest` is provided): JSON file with the structure below

### Manifest JSON Structure

```json
{
  "dataset": "<langfuse-dataset-name>",
  "synced_at": "<ISO-8601 UTC timestamp>",
  "items": [
    {
      "id": "<namespaced-api-id>",
      "timestamp": "<ISO-8601 UTC timestamp from server>"
    }
  ]
}
```

- `dataset` — the Langfuse dataset name from eval.yaml
- `synced_at` — when the sync run started (ISO-8601 UTC)
- `items[].id` — namespaced API ID (`{dataset_name}:{item_id}`) used by Langfuse
- `items[].timestamp` — server-side `updatedAt` timestamp for each item, used for version pinning

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (all items synced, manifest written if requested) |
| 1 | Failure (missing env vars, file not found, YAML parse error, item operation failures, or manifest write failure) |

Note: `--dry-run` always exits 0 without making API calls.

## What It Does

1. Parses eval.yaml — reads `dataset` name and `items[]` (id, input, expected_output)
2. Creates the dataset in Langfuse if it doesn't exist (idempotent POST)
3. Upserts all items from eval.yaml by namespaced id (POST `/api/public/dataset-items`)
4. Fetches fresh snapshot of existing items after upserts
5. Archives items in Langfuse but not in eval.yaml (POST with `status: ARCHIVED`)
6. Captures per-item server timestamps from response bodies (falls back to HTTP Date header, then local clock)
7. Writes the manifest JSON file if `--output-manifest` was provided

## eval.yaml Schema (DEV-321)

```yaml
dataset: <langfuse-dataset-name>
description: <optional dataset description>
items:
  - id: <unique-string-id>
    input: <prompt text sent to the agent>
    expected_output: <what a correct response looks like>
```

## Manifest Usage

The manifest file is consumed by the eval execute phase to pin dataset state
for the 4-variant test matrix:
- Before manifest → pins dataset state for skill v1 runs (model A + model B)
- After manifest → pins dataset state for skill v2 runs (model A + model B)
- Each item's server timestamp identifies the exact version of that item to use
