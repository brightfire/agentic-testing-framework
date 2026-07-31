---
name: eval-runner
description: "Use when running eval tests — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs. Currently covers the sync phase; additional phases to be added."
metadata:
  author: brightfire
  version: "2.0"
---

# Eval Runner

The eval runner orchestrates eval test phases. Each phase is a section below.

## Sync Phase

First phase of the eval runner. Syncs eval definitions to Langfuse and captures
manifest paths needed by the execute phase.

## Inputs

| Input | Source | Example |
|-------|--------|--------|
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
this is a new eval definition. Skip the before sync. `manifest_before` is `null`
— the execute phase should handle this (fewer variants, use current dataset state
as baseline).

**Edge case — deleted eval.yaml (no after version):**
If `git show <pr-head-ref>:<eval-yaml-path>` fails, the eval was removed.
Skip the after sync. This is unusual — flag it for human review rather than
proceeding.

### Sync each version to Langfuse

Run `dataset_sync.py` for each version that exists. Run sequentially —
concurrent syncs to the same dataset can interleave version timestamps.

**Prerequisites:**
- `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` environment variables must be set
- Source from `~/.openclaw/secrets/langfuse.env` if available
- Langfuse host must be reachable (default: `http://10.18.32.57:3000`)

```bash
# Sync the "before" version
python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$WORK_DIR/eval-before.yaml" \
  --output-manifest "$WORK_DIR/manifest-before.json"

# Sync the "after" version
python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$WORK_DIR/eval-after.yaml" \
  --output-manifest "$WORK_DIR/manifest-after.json"
```

The script prints sync progress logs to stdout, with the manifest file path as
the **last line of stdout**. The `--output-manifest` flag writes a JSON file
with per-item server timestamps — these manifests are passed to the execute
phase to pin experiment runs to exact dataset state.

For the full CLI interface — arguments, environment variables, exit codes,
and output format — see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).

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
manifest_before: <path to manifest-before.json or null>
manifest_after: <path to manifest-after.json or null>
```

The manifest files contain per-item server timestamps from Langfuse. Pass these
to the execute phase:
- `manifest_before` pins the "before" dataset state (skill v1 + model A, skill v1 + model B)
- `manifest_after` pins the "after" dataset state (skill v2 + model A, skill v2 + model B)

## Gotchas

- **Sync failure:** If either sync exits non-zero, abort the sync phase — do not proceed to the remaining sync. Report the error from the script output and notify the user. The execute phase needs both manifest files to produce a valid comparison.
- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's unusual and may indicate a dataset rename.
- **Python deps:** The agentic-testing-framework requires `langfuse`, `requests`, `pyyaml` — ensure the venv or system Python has these installed. Check `~/repos/agentic-testing-framework/requirements.txt`.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, and eval.yaml schema.
