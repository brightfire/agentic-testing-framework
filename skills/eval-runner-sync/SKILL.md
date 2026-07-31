---
name: eval-runner-sync
description: "Sync phase of the eval runner. Syncs before/after eval.yaml versions to Langfuse and captures version timestamps for the execute phase."
metadata:
  author: brightfire
  version: "1.2"
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
# Before version (from base branch, typically main)
git show <base-ref>:<eval-yaml-path> > /tmp/eval-before.yaml

# After version (from PR head branch)
git show <pr-head-ref>:<eval-yaml-path> > /tmp/eval-after.yaml
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
- Langfuse host must be reachable (default: `http://10.18.32.57:3000`)

```bash
# Sync the "before" version → captures T1 + manifest
# stdout = version timestamp (last line), stderr = sync log with item counts
T1=$(python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file /tmp/eval-before.yaml \
  --output-manifest /tmp/manifest-before.json 2>/tmp/sync-before.log)

# Sync the "after" version → captures T2 + manifest
T2=$(python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file /tmp/eval-after.yaml \
  --output-manifest /tmp/manifest-after.json 2>/tmp/sync-after.log)
```

**Important:** The sync script prints log output to stderr and the version
timestamp as the **last line of stdout**. The `$(...)` capture gets stdout
(T1/T2); the `2>` redirect saves stderr (sync logs with item counts) for
verification in the next step. The `--output-manifest` flag writes a JSON
file with per-item server timestamps — these manifests are passed to the
execute phase to pin experiment runs to exact dataset state.

For the full CLI interface — arguments, environment variables, exit codes,
and output format — see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).

### Verify sync

Read the sync log files to verify item-level operations completed successfully:

- Items created
- Items updated
- Items archived

```bash
# Example: grep for operation counts in the sync logs
grep -E 'created|updated|archived' /tmp/sync-before.log
grep -E 'created|updated|archived' /tmp/sync-after.log
```

### Clean up temp files

```bash
rm -f /tmp/eval-before.yaml /tmp/eval-after.yaml /tmp/sync-before.log /tmp/sync-after.log
```

## Output

Return a structured result for the execute phase:

```
dataset: <langfuse-dataset-name from eval.yaml>
T1: <before-version-timestamp or null>  (created: X, updated: Y, archived: Z)
T2: <after-version-timestamp or null>   (created: X, updated: Y, archived: Z)
skill: <skill-name>
eval_yaml_path: <path within repo>
manifest_before: /tmp/manifest-before.json
manifest_after: /tmp/manifest-after.json
```

Both timestamps are ISO-8601 UTC strings (e.g. `2026-07-29T15:51:00.000000Z`).
Pass these to the execute phase:
- T1 pins the "before" dataset state (skill v1 + model A, skill v1 + model B)
- T2 pins the "after" dataset state (skill v2 + model A, skill v2 + model B)

## Gotchas

- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's unusual and may indicate a dataset rename.
- **Python deps:** The agentic-testing-framework requires `langfuse`, `requests`, `pyyaml` — ensure the venv or system Python has these installed. Check `~/repos/agentic-testing-framework/requirements.txt`.
- **Sync failure behavior:** If the "before" sync (T1) fails, abort the entire sync phase — the execute phase needs both timestamps to produce a valid comparison. Report the error and the sync log contents. Do not attempt the "after" sync if T1 failed.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, and eval.yaml schema.
