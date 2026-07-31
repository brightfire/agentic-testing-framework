---
name: eval-runner
description: "Use when running eval tests — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs."
metadata:
  author: brightfire
  version: "2.1"
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
- Langfuse host must be reachable (default: `http://localhost:3000`)

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

## Environment Setup Phase

Second phase of the eval runner. Prepares the eval environment so the harness
can run variants in isolation. The skill is **directable** — the caller
specifies which variants to set up, not hardcoded to trigger type.

### Variant Specification (Input)

The caller provides a list of variant specs. Each spec has:

- A **git ref** to fetch the skill from (e.g. `main`, `feature/improve-skill`, `commit-abc123`)
- A **label** for the suffix (e.g. `main`, `pr-123`, `commit-abc123`)

Common patterns:

| Pattern | Variants | Example |
|---------|----------|--------|
| PR default | Two copies (before + after) | `[main@hash, pr-123@hash]` |
| PR re-run after updates | Single copy (updated only) | `[pr-123@hash]` |
| Model A/B | Single copy (models vary in execute) | `[main@hash]` |
| Arbitrary commit comparison | Two arbitrary refs | `[commit-abc@hash, commit-def@hash]` |

### Procedure

For each variant spec:

1. **Fetch the skill** from the git ref: `git show <ref>:<skill-path>/SKILL.md`
   and all files in the skill directory.
2. **Create suffixed directory** in
   `~/.openclaw/workspace/eval-skills/<skill-name>-<label>-<7char-hash>/`.
   - The 7-char hash is the short hash of the git ref being fetched (for
     collision prevention).
3. **Copy all skill files** into the suffixed directory (preserving
   subdirectory structure — references/, scripts/, etc.).
4. **Check for self-references in the skill body** — After copying the
   skill files but before rewriting the name field, scan the copied
   `SKILL.md` body (everything below the frontmatter `---` delimiter) for
   any occurrence of the original skill name. Use the same whole-word regex:
   `(?<![a-z0-9-])<skill-name>(?![a-z0-9-])` (case-insensitive).
   - If the skill name appears anywhere in the body text **outside** of the
     `name:` and `description:` frontmatter fields, **abort**. Report which
     line(s) contain self-references, explain that skills should not
     self-reference by name in their body text, and tell the user to fix the
     source skill. Do not proceed with the run.
   - If no self-references are found, continue to the next step.
5. **Rewrite the `name:` field** in the copied `SKILL.md` frontmatter to
   match the suffixed directory name (e.g., `linear-create` →
   `linear-create-main-a1b2c3d`).

### Sync Integration

The sync phase always runs before env setup (even for single-variant runs) to
ensure the Langfuse dataset matches current `eval.yaml` on main. The sync phase
output (manifest paths) is passed through to the execute phase alongside the
suffix-to-variant mapping.

### Output

Structured JSON for the execute phase:

```json
{
  "skill": "<original-skill-name>",
  "variants": [
    {
      "label": "main",
      "suffix": "linear-create-main-a1b2c3d",
      "dir": "~/.openclaw/workspace/eval-skills/linear-create-main-a1b2c3d",
      "git_ref": "main",
      "git_hash": "<full-hash>"
    },
    {
      "label": "pr-123",
      "suffix": "linear-create-pr-123-e5f6g7h",
      "dir": "~/.openclaw/workspace/eval-skills/linear-create-pr-123-e5f6g7h",
      "git_ref": "feature/improve-skill",
      "git_hash": "<full-hash>"
    }
  ],
  "manifest_before": "<path-or-null>",
  "manifest_after": "<path-or-null>",
  "trigger_type": "pr | slack | manual"
}
```

### Cleanup (Gotcha)

At the END of the run (after execute phase completes), clean up only the
suffixed dirs THIS run created. Do not touch other dirs in eval-skills/.
Cleanup is the responsibility of the full eval flow, not the env setup
phase alone — env setup creates, the orchestrator cleans up after execute.

### Gotchas

1. **eval-skills dir must be configured** — `~/.openclaw/workspace/eval-skills/`
   must exist AND be listed in `skills.load.extraDirs` in the gateway config.
   If it's not configured, the suffixed skills won't appear in the agent's
   `available_skills`. Adding a new extraDir requires a gateway restart — the
   skill should NOT attempt to restart the gateway. If the dir is missing or
   not in config, report the issue and stop.

2. **Self-references in skill bodies** — Skills should not reference
   themselves by name in their body text. The env setup phase only rewrites
   the `name:` field in frontmatter — body text is left untouched. If a
   skill name appears in the body (outside frontmatter fields), env setup
   aborts with a message indicating which lines need fixing. Fix
   self-references at the source skill before re-running.

3. **Do not clean up other runs' dirs** — Only clean up dirs created by THIS
   run. Other eval runs may be active concurrently.

4. **Skill names are normalized** — OpenClaw normalizes skill names to
   `[a-z0-9-]` (lowercase, hyphens only). Suffixed names must stay within
   this charset. No dots, underscores, or uppercase.

5. **Concurrent runs** — The 7-char git hash in the suffix prevents directory
   collisions between concurrent runs testing different commits. If a
   collision still occurs (same ref, same hash), the run should detect the
   dir already exists and skip re-copying.

6. **Copying subdirectories** — Skills may have subdirectories (references/,
   scripts/, templates/, etc.). Copy the entire skill directory structure, not
   just SKILL.md. Internal relative paths in the skill body (e.g.,
   `references/foo.md`) work because the structure is preserved.

## Execute Phase

Not yet implemented.

## Report Phase

Not yet implemented.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, and eval.yaml schema.
