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
| `--langfuse-host` | No | `http://10.18.32.57:3000` | Langfuse host URL |
| `--dry-run` | No | `False` | Parse and show what would be synced without API calls |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes | Langfuse API public key |
| `LANGFUSE_SECRET_KEY` | Yes | Langfuse API secret key |

## Output

- **stderr:** Log lines in format `[ISO-timestamp] [LEVEL] message` — includes item-level operations (created/updated/archived) and progress
- **stdout:** The **last line** is the version timestamp in ISO-8601 UTC format (e.g. `2026-07-29T15:51:00.000000Z`). All other stdout is empty.
- This separation is deliberate — capture stdout for programmatic use of the timestamp, stderr for human/log review.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success (version timestamp printed to stdout) |
| 1 | Failure (missing env vars, file not found, YAML parse error, or item operation failures) |

Note: `--dry-run` always exits 0 without making API calls.

## What It Does

1. Parses eval.yaml — reads `dataset` name and `items[]` (id, input, expected_output)
2. Fetches existing ACTIVE dataset items from Langfuse
3. Upserts all items from eval.yaml by id (POST `/api/public/dataset-items` with custom id — upsert on conflict)
4. Archives items in Langfuse but not in eval.yaml (POST with `status: ARCHIVED`)
5. Waits 2 seconds for server-side processing
6. Reads the latest `updatedAt` timestamp from dataset items — this is the version timestamp
7. Prints version timestamp to stdout

## eval.yaml Schema (DEV-321)

```yaml
dataset: <langfuse-dataset-name>
items:
  - id: <unique-string-id>
    input: <prompt text sent to the agent>
    expected_output: <what a correct response looks like>
```

## Version Timestamp Usage

The version timestamp pins experiment runs to exact dataset state:
- `get_dataset(version=<timestamp>)` returns the dataset as it was at that point
- Enables concurrent test runs without interference
- T1 = before eval version, T2 = after eval version
- Both are passed to the execute phase for the 4-variant test matrix
