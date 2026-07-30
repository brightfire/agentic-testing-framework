---
name: eval-runner-sync
description: "Sync phase of the eval runner. Syncs eval.yaml versions to Langfuse and writes manifest files for the execute phase. Use when eval.yaml changes and needs to be synced to Langfuse datasets."
metadata:
  author: brightfire
  version: "1.1"
---

# Eval Runner — Sync Phase

First phase of the eval runner. Syncs eval definitions to Langfuse using `dataset_sync.py` with the `--output-manifest` flag, producing manifest files needed by the execute phase for the 4-variant test matrix.

## When This Runs

Always. Sync runs to ensure Langfuse has a current version of the eval dataset, regardless of whether `eval.yaml` changed.

## Inputs

| Input | Source | Example |
|-------|--------|---------|
| eval.yaml path | Relative path within the repo | `skills/linear-create/eval.yaml` |
| Before ref | Git ref for the previous version (typically `main`) | `main` |
| After ref | Git ref for the new version (PR branch, commit, etc.) | `feature/improve-linear-create` |

## Procedure

### 1. Activate the agentic-testing-framework venv

```bash
cd ~/repos/agentic-testing-framework
source .venv/bin/activate
```

### 2. Identify the eval.yaml to sync

Determine which `eval.yaml` file(s) to sync and the before/after git refs.

### 3. Extract both versions of eval.yaml

Use `git show` to extract each version to a temp file. This avoids modifying
the working tree and works regardless of current checkout state.

```bash
# Create a unique temp directory for this sync run
TMPDIR=$(mktemp -d /tmp/eval-sync-XXXXXX)

# Before version (from base branch, typically main)
git show <base-ref>:<eval-yaml-path> > "$TMPDIR/eval-before.yaml"

# After version (from PR head branch)
git show <pr-head-ref>:<eval-yaml-path> > "$TMPDIR/eval-after.yaml"
```

**Edge case — new eval.yaml (no before version):**
If `git show <base-ref>:<eval-yaml-path>` fails (file doesn't exist on main),
this is a new eval definition. Skip the before sync. The before manifest is
`null` — the execute phase should handle this (fewer variants, use current
dataset state as baseline).

**Edge case — deleted eval.yaml (no after version):**
If `git show <pr-head-ref>:<eval-yaml-path>` fails, the eval was removed.
Skip after sync. This is unusual — flag it for human review rather than proceeding.

### 4. Sync each version to Langfuse

Run `dataset_sync.py` for each version that exists. Run sequentially —
concurrent syncs to the same dataset can interleave item timestamps.

**Prerequisites:**
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` environment variables must be set
- Source from `~/.openclaw/secrets/langfuse.env` if available
- Langfuse host must be reachable (default: `http://10.18.32.57:3000`)
- The agentic-testing-framework venv must be initialized with dependencies installed

```bash
# Sync the "before" version → writes manifest with per-item timestamps
python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$TMPDIR/eval-before.yaml" \
  --output-manifest "$TMPDIR/manifest-before.json"

# Sync the "after" version → writes manifest with per-item timestamps
python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$TMPDIR/eval-after.yaml" \
  --output-manifest "$TMPDIR/manifest-after.json"
```

The `--output-manifest` flag writes a JSON file containing:
- `dataset` — Langfuse dataset name
- `synced_at` — ISO-8601 UTC timestamp of the sync start
- `items` — array of `{id, timestamp}` objects with per-item server timestamps

The sync script logs progress to stdout/stderr. Check the exit code to confirm
success — exit 0 means all items synced, exit 1 means failures occurred.

### 5. Verify sync results

Read the sync script's stdout/stderr output for item-level operation counts:

- Items upserted
- Items archived
- Items failed

```bash
# Verify manifests were written
cat "$TMPDIR/manifest-before.json" | python -m json.tool | head -5
cat "$TMPDIR/manifest-after.json" | python -m json.tool | head -5
```

If either sync exited non-zero, check the logs for failed items and flag the
error before proceeding to the execute phase.

### 6. Clean up temp files

```bash
rm -f "$TMPDIR/eval-before.yaml" "$TMPDIR/eval-after.yaml"
# Keep manifests — they are passed to the execute phase
```

## Output

Return a structured result for the execute phase:

```
dataset: <langfuse-dataset-name from eval.yaml>
before_manifest: $TMPDIR/manifest-before.json  (or null if new eval.yaml)
after_manifest: $TMPDIR/manifest-after.json    (or null if deleted)
skill: <skill-name>
eval_yaml_path: <path within repo>
```

Both manifest files are JSON with per-item server timestamps. Pass their paths
to the execute phase for the 4-variant test matrix:
- Before manifest pins the "before" dataset state (skill v1 + model A, skill v1 + model B)
- After manifest pins the "after" dataset state (skill v2 + model A, skill v2 + model B)

## Gotchas

- **Script location:** `~/repos/agentic-testing-framework/src/dataset_sync.py` — this is in the agentic-testing-framework repo, not in the skill directory.
- **Sequential sync:** Run before and after syncs sequentially, not in parallel. Concurrent syncs to the same dataset can interleave item timestamps.
- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's unusual and may indicate a dataset rename.
- **Env vars:** The sync script will exit 1 if `LANGFUSE_PUBLIC_KEY` or `LANGFUSE_SECRET_KEY` are not set. Source from `~/.openclaw/secrets/langfuse.env` if available.
- **Python deps:** Ensure the agentic-testing-framework venv has up-to-date dependencies. Run `pip install -r requirements.txt` from the repo root if sync fails with import errors.
- **Manifest write failure:** If the script cannot write the manifest file (e.g., permission denied), it exits non-zero. Always check the exit code, not just stdout.
