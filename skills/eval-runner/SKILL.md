---
name: eval-runner
description: "Use when running eval tests — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs."
metadata:
  author: brightfire
  version: "2.1"
---

# Eval Runner

The eval runner orchestrates eval test phases. Each phase is a section below.

## Variant Inference

The skill determines what to test based on the request, not the trigger source. Three independent dimensions:

**Skill versions** (what skill code to test):
- **PR referenced, no explicit skill specs** → default to skill A/B: `main@<base-hash>` + `<pr-head>@<head-hash>`
- **Request names specific commits** → use those commits as skill variants
- **Request says "just the PR version" or similar** → single skill variant: `<pr-head>@<hash>`
- **Explicit skill variant specs provided** → use them

**Models** (what models to run each skill variant against):
- **Request mentions model comparison** → model A/B dimension added
- **No model mention** → single model (whatever the agent default is)

**Dataset items** (which eval cases to run):
- **No items specified** → all items in the eval.yaml
- **Specific item(s) named** → only those items (by id)

**Baseline recency check** (PR-triggered only):
Before adding the baseline (before/main) variant to the run matrix, check
whether baseline experiments already exist in Langfuse for this PR's dataset.
The execute phase creates experiments using the naming convention:

```
<dataset-name>__<skill-name>__<variant-label>__<model-id>__<git-hash>
```

For example: `eval-runner__linear-create__before__glm-5.2__a1b2c3d`

During inference, query Langfuse for experiments matching
`<dataset-name>__<skill-name>__before__*__*` (the baseline pattern). If
experiments exist for all requested models within a recent window (default:
7 days), skip the baseline variant — only test the after (PR head) variant.
The confirmation summary notes "baseline already tested, skipping."

If baseline experiments are missing for any requested model, or are older
than the window, include the baseline variant for those models only.

This check does NOT apply to:
- First-time PR runs (no prior experiments exist)
- Explicit "re-run baseline" requests from the user
- Non-PR triggers (direct skill vs model comparisons with no PR context)

The model and dataset dimensions are orthogonal — they multiply with the
remaining skill variants after the baseline check. For example, a PR with
prior baseline runs for 2 models: 1 skill variant (after only) × 2 models =
2 runs instead of 4.

This inference happens at the skill level before the phases run. The env setup
phase receives the resolved list of (git ref, label) pairs for skill versions
and handles the mechanics of creating suffixed copies.

## Confirmation

After inferring variants, the skill presents a summary and waits for user
confirmation before proceeding. The summary shows:

- Skill variants (name, git ref, short hash)
- Models to test
- Dataset items (all or specific ids)
- Baseline status: whether baseline is included or skipped (with reason —
  "already tested within 7 days" or "no prior runs found")

The user can:
- **Confirm** — proceed to pre-flight checks
- **Adjust** — modify any dimension (add/remove skill variants, change models, change dataset items, force baseline re-test) and re-confirm
- **Cancel** — abort the run

Only after confirmation does the skill proceed to pre-flight checks and the phases.

If the user says "run same test again" or "re-run the previous eval", the skill
skips variant inference and confirmation, reusing the previous variant spec
directly. It proceeds straight to pre-flight checks. The baseline recency check
still applies — the rerun does not re-test the baseline unless the user
explicitly requests it ("re-run including baseline") or the previous run's
baseline experiments are missing.

## Pre-flight Checks

Before anything else, verify that all variant skills are safe to test.
Self-references in skill bodies would break the eval (the suffixed copy would
reference the original name, not itself), so check early — there's no reason
to sync anything to Langfuse if we can't run the variants.

### Procedure

For each variant spec:

1. **Fetch the SKILL.md** from the git ref: `git show <ref>:<skill-path>/SKILL.md`.
2. **Extract the body** — everything below the frontmatter `---` delimiter.
3. **Scan for self-references** using the whole-word regex:
   `(?<![a-z0-9-])<skill-name>(?![a-z0-9-])` (case-insensitive).
   - The skill name is the `name:` field from the frontmatter.
   - Check only the body text — the `name:` and `description:` frontmatter
     fields are excluded from this check.
4. **If any self-reference is found**, **abort the entire run** — do not
   proceed to sync or env setup. Report which skill(s) and line(s) contain
   self-references, and tell the user to fix the source skill before
   re-running.
5. **If all variants pass**, proceed to the Sync Phase.

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

## Environment Setup Phase

Second phase of the eval runner. Prepares the eval environment so the harness
can run variants in isolation.

### Procedure

For each variant spec:

1. **Fetch the latest** — If the git ref is a branch or PR head (not a fixed
   commit hash), always `git fetch origin <ref>` first to ensure you have the
   latest state. This handles the test → iterate → retest scenario where the
   branch has been updated since the last run. Then fetch the skill from the
   (now up-to-date) git ref: `git show <ref>:<skill-path>/SKILL.md` and all
   files in the skill directory.
2. **Create suffixed directory** in
   `~/.openclaw/workspace/eval-skills/<skill-name>-<label>-<7char-hash>/`.
   - The 7-char hash is the short hash of the git ref being fetched (for
     collision prevention).
3. **Copy all skill files** into the suffixed directory (preserving
   subdirectory structure — references/, scripts/, etc.).
4. **Rewrite the `name:` field** in the copied `SKILL.md` frontmatter to
   match the suffixed directory name (e.g., `linear-create` →
   `linear-create-main-a1b2c3d`).

### Output

List of suffixed directories created in `~/.openclaw/workspace/eval-skills/`, each with:

- Suffixed skill name (e.g., `linear-create-main-a1b2c3d`)
- Directory path
- Git ref and label it was created from

### Gotchas

1. **eval-skills dir must be configured** — `~/.openclaw/workspace/eval-skills/`
   must exist AND be listed in `skills.load.extraDirs` in the gateway config.
   If it's not configured, the suffixed skills won't appear in the agent's
   `available_skills`. Adding a new extraDir requires a gateway restart — the
   skill should NOT attempt to restart the gateway. If the dir is missing or
   not in config, report the issue and stop.

2. **Self-references in skill bodies** — Skills should not reference
   themselves by name in their body text. This is checked during the
   pre-flight phase (before sync) — if any variant's SKILL.md body contains
   the skill name outside of frontmatter fields, the entire run aborts
   before syncing to Langfuse. Fix self-references at the source skill
   before re-running.

3. **Skill names are normalized** — OpenClaw normalizes skill names to
   `[a-z0-9-]` (lowercase, hyphens only). Suffixed names must stay within
   this charset. No dots, underscores, or uppercase.

4. **Concurrent runs** — The 7-char git hash in the suffix prevents directory
   collisions between concurrent runs testing different commits. If a
   collision still occurs (same ref, same hash), the run should detect the
   dir already exists and skip re-copying.

5. **Copying subdirectories** — Skills may have subdirectories (references/,
   scripts/, templates/, etc.). Copy the entire skill directory structure, not
   just SKILL.md. Internal relative paths in the skill body (e.g.,
   `references/foo.md`) work because the structure is preserved.

## Execute Phase

Not yet implemented.

## Report Phase

Not yet implemented.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, and eval.yaml schema.
