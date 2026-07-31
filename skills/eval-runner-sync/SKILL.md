---
name: eval-runner-sync
description: "Sync phase of the eval runner. Syncs before/after eval.yaml versions to Langfuse and captures version timestamps for the execute phase."
metadata:
  author: brightfire
  version: "1.3"
---

# Eval Runner — Sync Phase

First phase of the eval runner. Syncs eval definitions to Langfuse and captures
version timestamps needed by the execute phase.

## Inputs

| Input | Source | Example |
|-------|--------|---------|
| Skill name | PR diff (directory containing modified eval.yaml) | `linear-create` |
| PR head ref | PR metadata | `feature/improve-linear-create` |
| Base ref | PR metadata (typically `main`) | `main` |
| eval.yaml path | Relative path within the repo | `skills/linear-create/eval.yaml` |

## Procedure

### Extract both versions of eval.yaml

Use `git show` to extract each version to a temp file. This avoids modifying
the working tree and works regardless of current checkout state.

```bash
# Create a working directory for temp files (collision-safe)
WORK_DIR=$(mktemp -d /tmp/eval-sync.XXXXXX)

# Before version (from base branch, typically main)
git show <base-ref>:<eval-yaml-path> > "$WORK_DIR/eval-before.yaml"

# After version (from PR head branch)
git show <pr-head-ref>:<eval-yaml-path> > "$WORK_DIR/eval-after.yaml"
```

**Edge case — new eval.yaml (no before version):**
If `git show <base-ref>:<eval-yaml-path>` fails (file doesn't exist on main),
this is a new eval definition. Skip the T1 sync. T1 is `null` — the execute
phase should handle this (fewer variants, use current dataset state as baseline).

**Edge case — deleted eval.yaml (no after version):**
If `git show <pr-head-ref>:<eval-yaml-path>` fails, the eval was removed.
Skip T2 sync. This is unusual — flag it for human review rather than proceeding.

### Sync each version to Langfuse

Run `dataset_sync.py` for each version that exists. Run sequentially —
concurrent syncs to the same dataset can interleave version timestamps.

**Prerequisites:**
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` environment variables must be set
- Source from `~/.openclaw/secrets/langfuse.env` if available
- Langfuse host must be reachable (default: `http://localhost:3000`)

```bash
# Sync the "before" version → manifest path is last line of stdout
# All log output goes to stdout; manifest path is printed last
~/repos/agentic-testing-framework/.venv/bin/python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$WORK_DIR/eval-before.yaml" \
  --output-manifest "$WORK_DIR/manifest-before.json" > "$WORK_DIR/sync-before.log" 2>&1
MANIFEST_BEFORE=$(tail -1 "$WORK_DIR/sync-before.log")

# Verify — abort if manifest path is empty (sync failed or manifest not written)
[ -z "$MANIFEST_BEFORE" ] && { echo "T1 sync failed"; cat "$WORK_DIR/sync-before.log"; rm -rf "$WORK_DIR"; exit 1; }

# Sync the "after" version → manifest path is last line of stdout
~/repos/agentic-testing-framework/.venv/bin/python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$WORK_DIR/eval-after.yaml" \
  --output-manifest "$WORK_DIR/manifest-after.json" > "$WORK_DIR/sync-after.log" 2>&1
MANIFEST_AFTER=$(tail -1 "$WORK_DIR/sync-after.log")

# Verify — abort if manifest path is empty
[ -z "$MANIFEST_AFTER" ] && { echo "T2 sync failed"; cat "$WORK_DIR/sync-after.log"; rm -rf "$WORK_DIR"; exit 1; }
```

**Important:** The sync script logs all progress to stdout and prints the
manifest file path as the **last line of stdout** when `--output-manifest`
is passed. Redirect all stdout to a log file, then extract the manifest
path with `tail -1`. The manifest JSON contains per-item server
timestamps — these are passed to the execute phase to pin experiment
runs to exact dataset state.

For the full CLI interface — arguments, environment variables, exit codes,
and output format — see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).

### Verify sync

Read the stdout log output to verify item-level operations completed
successfully. The manifest file should also exist at the captured path.

Check for:
- Items upserted (created/updated)
- Items archived
- Manifest file exists and is valid JSON

```bash
# Verify manifest files exist and are valid JSON
[ -f "$MANIFEST_BEFORE" ] && python3 -c "import json; json.load(open('$MANIFEST_BEFORE'))" && echo "manifest-before OK"
[ -f "$MANIFEST_AFTER" ] && python3 -c "import json; json.load(open('$MANIFEST_AFTER'))" && echo "manifest-after OK"
```

### Clean up temp files

```bash
rm -rf "$WORK_DIR"
```

## Output

Return a structured result for the execute phase:

```
dataset: <langfuse-dataset-name from eval.yaml>
skill: <skill-name>
eval_yaml_path: <path within repo>
manifest_before: <path or null>
manifest_after: <path>
```

Manifest files are JSON containing per-item server timestamps. Pass these
to the execute phase to pin experiment runs to exact dataset state.

## Gotchas

- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's unusual and may indicate a dataset rename.
- **Python deps:** The agentic-testing-framework requires `langfuse`, `requests`, `pyyaml`, `pydantic` — check `~/repos/agentic-testing-framework/requirements.txt`. Use the venv at `~/repos/agentic-testing-framework/.venv` if it exists. If no venv exists, create one (`python3 -m venv ~/repos/agentic-testing-framework/.venv`) and install deps (`~/repos/agentic-testing-framework/.venv/bin/pip install -r ~/repos/agentic-testing-framework/requirements.txt`). Always activate the venv before running `dataset_sync.py`.
- **Sync failure behavior:** If the "before" sync (T1) fails, abort the entire sync phase — the execute phase needs both timestamps to produce a valid comparison. Report the error and the sync log contents. Do not attempt the "after" sync if T1 failed. (The validation step in the procedure handles this programmatically.)

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, and eval.yaml schema.
