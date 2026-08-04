---
name: eval-runner
description: "Use when running eval tests — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs. SKIP for skill creation or skill review requests — use the skill-creator or skill-reviewer skills instead."
metadata:
  author: brightfire
  version: "2.1"
---

# Eval Runner

The eval runner orchestrates eval test phases. Each phase is a section below.

## Variant Inference

The skill determines what to test based on the request, not the trigger source. Three independent dimensions:

**Skill versions** (what skill code to test):
- **PR referenced, no explicit skill specs** → default to skill A/B: base branch (e.g., `main@<base-hash>`) + PR head (e.g., `<pr-branch>@<head-hash>`). The variant label is `main` (base
  branch name) for the base and `pr-<number>` (e.g., `pr-123`) for the PR head.
- **Request names specific commits** → use those commits as skill variants. The variant label is the commit hash.
- **Request says "just the PR version" or similar** → single skill variant: `<pr-branch>@<hash>`. The variant label is `pr-<number>` (e.g., `pr-123`).
- **Explicit skill variant specs provided** → use them. The variant label is the branch name or commit hash provided.

The variant label identifies the source of the variant — the PR number (for PR head variants), the base branch name (for the base variant), or commit hash (for explicit specs). This label
appears in experiment names to distinguish variants, alongside the git hash for precise commit identification.

**Models** (what models to run each skill variant against):
- **Request mentions model comparison** → model A/B dimension added
- **No model mention** → single model (whatever the agent default is)

**Dataset items** (which eval cases to run):
- **No items specified** → all items in the eval.yaml
- **Specific item(s) named** → only those items (by id)

### Baseline Recency Check

**Baseline recency check**: Before adding any variant to the run matrix, check whether matching experiment results already exist in Langfuse for this dataset.  The execute phase creates
experiments using the naming convention:

```
<dataset-name>__<model-id>__<variant-label>__<git-hash>
```

Where `<variant-label>` identifies the variant source — the base branch name (e.g., `main`), the PR number (e.g., `pr-123`), or a commit ref (for explicit specs). For example:
`linear-create-eval__glm-5.2__main__a1b2c3d` (base branch `main`) or `linear-create-eval__glm-5.2__pr-123__e5f6g7h` (PR head, PR number as label).

For explicit variant specs using branch names containing `/` (e.g., `claw/vash/fix-xyz`), slashes are replaced with hyphens in experiment names (e.g., `claw-vash-fix-xyz`).

During inference, query Langfuse for experiments matching `<dataset-name>__<model-id>__<variant-label>__<git-hash>` for each requested model and each variant — using the resolved commit hash
(from the Ref Resolution step), not a wildcard. This ensures results match the current state, even if the base branch has advanced within the 7-day window.  If experiments exist for a
variant within a recent window (default: 7 days), that variant can be reused — skip running it again. The confirmation summary notes which variants are being reused and from when.

If matching experiments are missing for any requested model or variant, or are older than the window, include those variants in the run.

The check still runs for first-time runs (no prior experiments exist — nothing to reuse) and explicit re-run requests (the user is asking to re-run, so prior results are ignored unless
the user says otherwise). These aren't exclusions from the check — they're cases where the check finds nothing to reuse.

The model and dataset dimensions are orthogonal — they multiply with the remaining skill variants after the recency check. For example, a PR with prior baseline runs for 2 models:
1 skill variant (after only) × 2 models = 2 runs instead of 4.

This inference happens at the skill level before the phases run. The env setup phase receives the resolved list of (git ref, label) pairs for skill versions and handles the mechanics of
creating suffixed copies. The label is used both for directory naming in env setup and as the variant-label component in experiment names.

## Confirmation

After inferring variants, the skill presents a summary and waits for user confirmation before proceeding. The summary shows:

- Skill variants (name, git ref, short hash)
- Models to test
- Dataset items (all or specific ids)
- Baseline status: whether baseline is included or skipped (with reason — "already tested within 7 days" or "no prior runs found")
- Reused variants: whenever prior experiment results are being reused, the summary must clearly state which variants are being reused and from when (experiment creation date). The user
  can choose to override and force a re-run of any reused variant.

The user can:
- **Confirm** — proceed to pre-flight checks
- **Adjust** — modify any dimension (add/remove skill variants, change models, change dataset items, force re-test of any variant including reused ones) and re-confirm

Only after confirmation does the skill proceed to pre-flight checks and the phases.

If the user says "run same test again" or "re-run the previous eval", the skill skips variant inference and confirmation, reusing the previous variant spec directly. It proceeds straight to
pre-flight checks. The recency check still applies — the rerun reuses prior results for any variant with matching experiments within the recency window, unless the user explicitly requests
a full re-run ("re-run everything") or matching experiments are missing.



## Ref Resolution

Before pre-flight checks, pin all git refs to commit hashes so subsequent phases use a fixed snapshot:

1. For each variant spec, if the git ref is a branch name (not a commit hash), resolve it: `git fetch origin <ref> && git rev-parse origin/<ref>` (or `git ls-remote origin <ref>`).
2. Replace the branch ref with the resolved commit hash in the variant spec.
3. All subsequent phases (pre-flight, sync, env setup) use the pinned commit hash — never the branch name.

## Pre-flight Checks

Before anything else, verify that all variant skills are safe to test.  Self-references in skill bodies would break the eval (the suffixed copy would reference the original name, not itself),
so check early — there's no reason to sync anything to Langfuse if we can't run the variants.

### Procedure

Before checking variants, verify the eval environment:

1. **Check eval-skills configuration** — `~/.openclaw/workspace/eval-skills/` must exist AND be listed in `skills.load.extraDirs` in the gateway config.  If not, report the issue and stop
   — do not proceed to sync or env setup.

For each variant spec:

1. **Fetch the SKILL.md** from the git ref: `git show <ref>:<skill-path>/SKILL.md`.
2. **Extract the body** — everything below the frontmatter `---` delimiter.
3. **Scan for self-references** using the whole-word regex: `(?<![a-z0-9-])<skill-name>(?![a-z0-9-])` (case-insensitive).
   - The skill name is the `name:` field from the frontmatter.
   - Check only the body text — the `name:` and `description:` frontmatter fields are excluded from this check.
4. **If any self-reference is found**, **abort the entire run** — do not proceed to sync or env setup. Report which skill(s) and line(s) contain self-references, and tell the user to fix
   the source skill before re-running.
5. **If all variants pass**, proceed to the Sync Phase.

## Sync Phase

First phase of the eval runner. Syncs eval definitions to Langfuse and captures manifest paths needed by the execute phase.

## Inputs

| Input | Source | Example |
|-------|--------|--------|
| Skill name | PR diff (directory containing modified eval.yaml) | `linear-create` |
| PR head ref | PR metadata | `feature/improve-linear-create` |
| Base ref | PR metadata (typically `main`) | `main` |
| eval.yaml path | Relative path within the repo | `skills/linear-create/eval.yaml` |

## Procedure

### Extract both versions of eval.yaml

Sync every variant that the Variant Inference section determined should run. If inference determined the baseline is being reused (baseline recency check passed), skip the before version — it already exists in Langfuse. If inference determined the baseline needs to run, extract and sync it. In short: sync what inference produces, nothing more, nothing less.

Use `git show` to extract each version to a temp file. This avoids modifying the working tree and works regardless of current checkout state.

```bash
# Create a working directory for temp files (collision-safe)
WORK_DIR=$(mktemp -d /tmp/eval-sync.XXXXXX)

# Before version (from base branch, typically main)
git show <base-ref>:<eval-yaml-path> > "$WORK_DIR/eval-before.yaml"

# After version (from PR head branch)
git show <pr-head-ref>:<eval-yaml-path> > "$WORK_DIR/eval-after.yaml"
```

**Edge case — new eval.yaml (no before version):** If `git show <base-ref>:<eval-yaml-path>` fails (file doesn't exist on main), this is a new eval definition. Skip the before
sync. `manifest_before` is `null` — the execute phase should handle this (fewer variants, use current dataset state as baseline).

**Edge case — deleted eval.yaml (no after version):** If `git show <pr-head-ref>:<eval-yaml-path>` fails, the eval was removed.  Skip the after sync. This is unusual — flag it for human
review rather than proceeding.

### Sync each version to Langfuse

Run `dataset_sync.py` for each version that exists. Run sequentially — concurrent syncs to the same dataset can interleave version timestamps.

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

The script prints sync progress logs to stdout, with the manifest file path as the **last line of stdout**. The `--output-manifest` flag writes a JSON file with per-item server timestamps
— these manifests are passed to the execute phase to pin experiment runs to exact dataset state.

For the full CLI interface — arguments, environment variables, exit codes, and output format — see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).

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

The manifest files contain per-item server timestamps from Langfuse. Pass these to the execute phase:
- `manifest_before` pins the "before" dataset state (skill v1 + model A, skill v1 + model B)
- `manifest_after` pins the "after" dataset state (skill v2 + model A, skill v2 + model B)

## Gotchas

- **Sync failure:** If either sync exits non-zero, abort the sync phase — do not proceed to the remaining sync. Report the error from the script output and notify the user. The execute
  phase needs both manifest files to produce a valid comparison.
- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's unusual and
  may indicate a dataset rename.
- **Python deps:** The agentic-testing-framework requires `langfuse`, `requests`, `pyyaml` — ensure the venv or system Python has these installed. Check
  `~/repos/agentic-testing-framework/requirements.txt`.

## Environment Setup Phase

Second phase of the eval runner. Prepares the eval environment so the harness can run variants in isolation.

### Procedure

For each variant spec:

1. **Fetch the latest** — If the git ref is a branch or PR head (not a fixed commit hash), always `git fetch origin <ref>` first to ensure you have the latest state. This handles the test
   → iterate → retest scenario where the branch has been updated since the last run. Then fetch the skill from the (now up-to-date) git ref: `git show <ref>:<skill-path>/SKILL.md` and all
   files in the skill directory.
2. **Create suffixed directory** in `~/.openclaw/workspace/eval-skills/<skill-name>-<label>-<7char-hash>/`.
   - The 7-char hash is the short hash of the git ref being fetched (for collision prevention).
3. **Copy all skill files** into the suffixed directory (preserving subdirectory structure — references/, scripts/, etc.).
4. **Rewrite the `name:` field** in the copied `SKILL.md` frontmatter to match the suffixed directory name (e.g., `linear-create` → `linear-create-main-a1b2c3d`).

### Output

List of suffixed directories created in `~/.openclaw/workspace/eval-skills/`, each with:

- Suffixed skill name (e.g., `linear-create-main-a1b2c3d`)
- Directory path
- Git ref and label it was created from

### Gotchas

1. **eval-skills dir configuration** — Checked during pre-flight (above) — the eval-skills dir must exist and be in `skills.load.extraDirs`. Adding a new extraDir requires a gateway restart
   — the skill should NOT attempt to restart the gateway.

2. **Self-references in skill bodies** — Checked during the pre-flight phase (above). If any are found, the run aborts before env setup.  Fix self-references at the source skill before re-running.

3. **Skill names are normalized** — OpenClaw normalizes skill names to `[a-z0-9-]` (lowercase, hyphens only). Suffixed names must stay within this charset. No dots, underscores, or uppercase.

4. **Concurrent runs** — The 7-char git hash in the suffix prevents directory collisions between concurrent runs testing different commits. If a collision still occurs (same ref, same hash),
   the run should detect the dir already exists and skip re-copying.

5. **Copying subdirectories** — Skills may have subdirectories (references/, scripts/, templates/, etc.). Copy the entire skill directory structure, not just SKILL.md. Internal relative
   paths in the skill body (e.g., `references/foo.md`) work because the structure is preserved.

## Execute Phase

Not yet implemented.

## Report Phase

Not yet implemented.

## Cleanup

After the execute and report phases complete, remove the suffixed directories created during Environment Setup. Track which directories were created during env setup and `rm -rf` only
those. Do not remove directories created by other concurrent runs. If the run aborts after env setup (e.g., execute phase failure), cleanup should still run.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, and eval.yaml schema.
