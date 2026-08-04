---
name: eval-runner
description: "Use when evaluating a skill or running tests for a skill — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs. SKIP for skill creation, editing, or auditing requests — use the skill-creator or skill-reviewer skills instead."
metadata:
  author: brightfire
  version: "2.2"
---

# Eval Runner

The eval runner orchestrates eval test phases. Each phase is a section below.

## Variant Inference

The skill determines what to test based on the request, not the trigger source. Three independent dimensions:

**Skill versions** (what skill code to test):
- **PR referenced, no explicit skill specs** → default to skill A/B: base branch (e.g., `main@<base-hash>`) + PR head (e.g., `<pr-branch>@<head-hash>`). The variant label is `main`
  (base branch name) for the base and `pr-<number>` (e.g., `pr-123`) for the PR head.
- **Branch named, no PR referenced** → default to skill A/B: base branch (e.g., `main@<base-hash>`) + named branch (e.g., `<branch>@<head-hash>`). The variant label is the base branch name (e.g., `main`) for the base and the normalized branch name for the named branch (slashes replaced with hyphens, e.g., `claw-vash-fix-xyz`).
- **Request names specific commits** → use those commits as skill variants. The variant label is the commit hash.
- **Request says "just the PR version" or similar** → single skill variant: `<pr-branch>@<hash>`. The variant label is `pr-<number>` (e.g., `pr-123`).
- **Explicit skill variant specs provided** → use them. The variant label is the branch name or commit hash provided.
- **Request asks for "past N commits" on a branch** → resolve the branch, list the last N commit hashes via `git rev-list --max-count=N <branch>`, and create N skill variants — one per commit. The variant label for each is the short commit hash (7 chars). For example, "past 5 commits on main" produces 5 variants with labels like `a1b2c3d`, `e5f6g7h`, etc.

The variant label identifies the source of the variant — the PR number (for PR head variants), the base branch name (for the base variant), or commit hash (for explicit specs).
This label appears in experiment names to distinguish variants, alongside the git hash for precise commit identification.

**Models** (what models to run each skill variant against):
- **Request mentions model comparison, or names a specific model** → model A/B dimension added. Naming a single specific model (e.g., "test against claude-sonnet-4-6") implies an A/B comparison with the agent default as model A and the named model as model B.
- **No model mention** → single model (whatever the agent default is)

**Dataset items** (which eval cases to run):
- **No items specified** → all items in the eval.yaml
- **Specific item(s) named** → only those items (by id)

Variant inference produces raw refs (branch names, PR numbers, commit hashes) and labels for each variant. Ref resolution happens after inference, pinning those raw refs to commit hashes. The env setup phase receives the resolved list of (git ref, label) pairs for skill versions and handles the
mechanics of creating suffixed copies. The label is used both for directory naming in env setup and as the variant-label component in experiment names.

## Ref Resolution

After variant inference, pin all git refs to commit hashes so subsequent phases use a fixed snapshot:

1. For each variant spec, fetch from origin to ensure the ref is available locally: `git fetch origin "<ref>"`. If the ref is a branch name (not a commit hash), resolve it to a commit hash: `git rev-parse "origin/<ref>"` (or `git ls-remote origin "<ref>"`). If the ref is already a commit hash, `git fetch origin "<ref>"` ensures the commit is present in the local clone.
2. Replace the branch ref with the resolved commit hash in the variant spec.
3. All subsequent phases (recency check, pre-flight, sync, env setup) use the pinned commit hash — never the branch name.

## Recency Check

After variant inference builds the full matrix of combinations (skill variants × models × dataset items), check whether matching experiment results already exist in Langfuse. Prune any combinations that have recent results from the run matrix.

**Baseline recency check**: Before adding any variant to the run matrix, check whether matching experiment results already exist in Langfuse for this dataset.  The execute phase
creates experiments using the naming convention:

```
<dataset-name>__<model-id>__<variant-label>__<git-hash>__<item-scope>
```

Where `<variant-label>` identifies the variant source — the base branch name (e.g., `main`), the PR number (e.g., `pr-123`), or a commit ref (for explicit specs). `<item-scope>` is
`all` when all dataset items are used (the default case), or an 8-character hex hash for subset runs. The hash is computed as: sort the item IDs lexicographically, join with `|` (pipe), take the first 8 characters of the SHA-256 hex digest of the resulting string. For example, items `['c', 'a', 'b']` → `a|b|c` → `sha256('a|b|c')[:8]`. For example:
`linear-create-eval__openrouter-z-ai-glm-5.2__main__a1b2c3d__all` (base branch `main`, all items) or `linear-create-eval__openrouter-z-ai-glm-5.2__pr-123__e5f6g7h__all` (PR head, all items).

`<model-id>` is the full provider-qualified model ID (e.g., `openrouter/z-ai/glm-5.2`) with `/` replaced by `-` (e.g., `openrouter-z-ai-glm-5.2`). This prevents collisions between providers that share the same leaf model name.

For explicit variant specs using branch names containing `/` (e.g., `claw/vash/fix-xyz`), slashes are replaced with hyphens in experiment names (e.g., `claw-vash-fix-xyz`).

During the recency check, query Langfuse for experiments whose names **start with** `<dataset-name>__<model-id>__<variant-label>__<git-hash>__<item-scope>` for each requested model and each variant — using the resolved commit hash, not a wildcard. The harness appends ` - <timestamp>` (and optionally ` - <run_idx>/<total>` for repeats) to experiment names, so the lookup must use a prefix match, not an exact match. The item-scope must match exactly (no wildcard) — `all` for full-dataset runs, or the specific item-scope hash for subset runs. This ensures results match the current state, even if the base branch has advanced within the 7-day window. If experiments exist for a variant within a recent window (default: 7 days), that variant can be reused — skip running it again. The confirmation summary notes which variants are being reused and from when.

If matching experiments are missing for any requested model or variant, or are older than the window, include those variants in the run.

The check still runs for first-time runs (no prior experiments exist — nothing to reuse). For reruns, the recency check runs normally — if nothing has changed since the prior run
(same commit hash), the bot should indicate there are no changes and ask the user to confirm they want to force a re-run. If the user confirms, prior results are excluded and all
requested variants execute.

The model and dataset dimensions are orthogonal — they multiply with the remaining skill variants after the recency check. For example, a PR with prior baseline runs for 2 models:
1 skill variant (after only) × 2 models = 2 runs instead of 4.

## Confirmation

After variant inference and the recency check, the skill presents a summary of the pruned run matrix and waits for user confirmation before proceeding. The summary shows:

- Skill variants (name, git ref, short hash)
- Models to test
- Dataset items (all or specific ids)
- Baseline status: whether baseline is included or skipped (with reason — "already tested within 7 days" or "no prior runs found")
- Reused variants: whenever prior experiment results are being reused, the summary must clearly state which variants are being reused and from when (experiment creation date). The
  user can choose to override and force a re-run of any reused variant.

The user can:
- **Confirm** — proceed to pre-flight checks
- **Adjust** — modify any dimension (add/remove skill variants, change models, change dataset items, force re-test of any variant including reused ones) and re-confirm, re-running the recency check if variants change

Only after confirmation does the skill proceed to pre-flight checks and the phases.

If the user says "run same test again" or "re-run the previous eval", the skill skips variant inference — the user is confirming the previous variant set. The recency check still
runs. If nothing has changed (same commit hash, all variants already tested within the recency window), the bot indicates there are no changes since the prior run and asks the user
to confirm they want to force a re-run. If the user confirms, prior results are excluded and all requested variants execute. If some variants have changed or are new, only those
run — unchanged variants are reused.



## Pre-flight Checks

Before anything else, verify that all variant skills are safe to test.  Self-references in skill bodies would break the eval (the suffixed copy would reference the original name,
not itself), so check early — there's no reason to sync anything to Langfuse if we can't run the variants.

### Procedure

For each variant spec:

1. **Identify skill-relevant files.** Fetch SKILL.md from the git ref: `git show <ref>:<skill-path>/SKILL.md`. Read its body and collect all file references — paths matching patterns like `references/foo.md`, `scripts/bar.py`, `templates/baz.txt`, or Markdown links like `[text](references/foo.md)`. Then recursively follow references in those files to find transitive dependencies (a referenced file may link to another file), up to depth 3. Fetch each transitively-referenced file from the git ref and repeat the process until no new files are found or depth 3 is reached. The skill-relevant file set is: SKILL.md itself plus the full closure of files reachable through reference chains. Exclude convention/meta files (AGENTS.md, CLAUDE.md, LICENSE, .gitignore, etc.) even if referenced. Exclude `scripts/` files — references in scripts are code comments, not instructional content the LLM follows. Do NOT include files in the skill directory that aren't reachable from SKILL.md through reference chains.
2. **Scan skill-relevant files for self-references.** Fetch each identified file from the git ref via `git show <ref>:<skill-path>/<file>`. Scan each file's content using the whole-word regex: `(?<![a-z0-9-])<skill-name>(?![a-z0-9-])` (case-insensitive). For SKILL.md, scan the body excluding frontmatter; for all other files, scan the entire content. The skill name is the `name:` field from the SKILL.md frontmatter.
3. **If any self-reference is found**, **abort the entire run** — do not proceed to sync or env setup. Report which skill(s) and line(s) contain self-references, and tell the user
   to fix the source skill before re-running.
4. **If all variants pass**, proceed to the Sync Phase.

## Sync Phase

First phase of the eval runner. Syncs eval definitions to Langfuse and captures manifest paths needed by the execute phase.

## Inputs

| Input | Source | Example |
|-------|--------|--------|
| Skill name | Variant specs from Variant Inference | `linear-create` |
| Variant specs | Variant Inference phase | `main@a1b2c3d`, `pr-123@e5f6g7h` |
| eval.yaml path | Relative path within the repo | `skills/linear-create/eval.yaml` |

## Procedure

### Extract eval.yaml for each variant

Sync every variant that remains after the Recency Check prunes the run matrix. For reruns, if the user confirmed a forced re-run (nothing changed but user wants to re-run anyway),
sync all variants. If the recency check found some variants unchanged, only sync the new or changed variants. In general: sync what the recency check produces, nothing more, nothing less.

Use `git show` to extract each version to a temp file. This avoids modifying the working tree and works regardless of current checkout state.

Normalize the variant label to `[a-z0-9-]` before using it as a filename (replace `/` with `-`, lowercase, strip dots and underscores). For example, `claw/vash/fix-xyz` → `claw-vash-fix-xyz` → `eval-claw-vash-fix-xyz.yaml`.

```bash
# Create a working directory for temp files (collision-safe)
WORK_DIR=$(mktemp -d /tmp/eval-sync.XXXXXX)

# For each variant spec from inference (variant-label, git-ref):
# Normalize the label to [a-z0-9-] before using it as a filename
git show "<variant-ref>:<eval-yaml-path>" > "$WORK_DIR/eval-<variant-label>.yaml"
```

**Edge case — new eval.yaml (variant's ref doesn't have the file):** If `git show "<variant-ref>:<eval-yaml-path>"` fails (file doesn't exist at that ref), this is a new eval
definition for that variant. Skip the sync for that variant. Its manifest entry is `null` — the execute phase should handle this (fewer variants, use current dataset state as
baseline).

**Edge case — deleted eval.yaml (variant's ref doesn't have the file):** If `git show "<variant-ref>:<eval-yaml-path>"` fails for a variant that should have the file, the eval was
removed at that ref. Skip the sync for that variant. This is unusual — flag it for human review rather than proceeding.

### Sync each version to Langfuse

Run `dataset_sync.py` for each version that exists. Run sequentially — concurrent syncs to the same dataset can interleave version timestamps.

```bash
# For each extracted eval file (sequentially — concurrent syncs can interleave timestamps):
python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$WORK_DIR/eval-<variant-label>.yaml" \
  --output-manifest "$WORK_DIR/manifest-<variant-label>.json"
```

The manifest file contains per-item server timestamps from Langfuse, used by the execute phase to pin experiment runs to exact dataset state. For the full manifest file contract
and CLI interface — arguments, environment variables, exit codes, and output format — see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).

### Clean up temp files

Temp eval YAML files in `$WORK_DIR` can be removed after the sync, but **manifest files must be preserved** — the execute phase reads them via `--manifest`. Either:
- Write manifests to a durable location (e.g., `~/.openclaw/workspace/eval-runs/<run-id>/manifests/`) and clean up `$WORK_DIR` after copying, or
- Keep `$WORK_DIR` alive until the cleanup phase removes it.

```bash
# Keep manifest files — do NOT rm -rf "$WORK_DIR" here
# Cleanup phase handles temp removal after execute + report complete
```

## Output

Return a structured result for the execute phase:

```
dataset: <langfuse-dataset-name from eval.yaml>
skill: <skill-name>
eval_yaml_path: <path within repo>
manifests:
  - variant: <variant-label>
    ref: <git-hash>
    manifest: <path to manifest-<variant-label>.json or null if skipped>
  - variant: <variant-label>
    ref: <git-hash>
    manifest: <path to manifest-<variant-label>.json or null if skipped>
  ...
```

The manifest files contain per-item server timestamps from Langfuse. Pass these to the execute phase — each manifest pins the dataset state for its corresponding variant.

## Gotchas

- **Sync failure:** If any sync exits non-zero, abort the sync phase — do not proceed to the remaining syncs. Report the error from the script output and notify the user. The
  execute phase needs all manifest files to produce a valid comparison.
- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's
  unusual and may indicate a dataset rename.
- **Python deps:** The agentic-testing-framework requires `langfuse`, `requests`, `pyyaml` — ensure the venv or system Python has these installed. Check
  `~/repos/agentic-testing-framework/requirements.txt`.
- **Langfuse credentials:** `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` must be set — source from `~/.openclaw/secrets/langfuse.env` if available. The Langfuse host must be
  reachable (default: `http://localhost:3000`).

## Environment Setup Phase

Second phase of the eval runner. Prepares the eval environment so the harness can run variants in isolation.

### Procedure

For each variant spec:

1. **Fetch the latest** — If the git ref is a branch or PR head (not a fixed commit hash), always `git fetch origin "<ref>"` first to ensure you have the latest state. This handles
   the test → iterate → retest scenario where the branch has been updated since the last run. Then fetch the skill from the (now up-to-date) git ref: `git show
   "<ref>:<skill-path>/SKILL.md"` and all files in the skill directory.
2. **Create suffixed directory** in `~/.openclaw/workspace/eval-skills/<skill-name>-<label>-<7char-hash>-<4char-random>/`.
   - Normalize the label to `[a-z0-9-]` before constructing the directory name: replace `/` with `-`, lowercase, and strip dots and underscores (e.g., `claw/vash/fix-xyz` → `claw-vash-fix-xyz`).
   - The 7-char hash is the short hash of the git ref being fetched (for collision prevention).
3. **Copy all skill files** into the suffixed directory (preserving subdirectory structure — references/, scripts/, etc.).
4. **Rewrite the `name:` field** in the copied `SKILL.md` frontmatter to match the suffixed directory name (e.g., `linear-create` → `linear-create-main-a1b2c3d-x7k2`).

### Output

List of suffixed directories created in `~/.openclaw/workspace/eval-skills/`, each with:

- Suffixed skill name (e.g., `linear-create-main-a1b2c3d-x7k2`)
- Directory path
- Git ref and label it was created from

### Gotchas

1. **eval-skills not configured** — If `~/.openclaw/workspace/eval-skills/` is missing or not listed in `skills.load.extraDirs` in the gateway config, environment setup will fail.
   Check this first if env setup errors. Once configured, this is unlikely to fail again.

2. **Self-references in skill bodies** — Checked during the pre-flight phase (above). If any are found, the run aborts before env setup.  Fix self-references at the source skill
   before re-running.

3. **Skill names are normalized** — OpenClaw normalizes skill names to `[a-z0-9-]` (lowercase, hyphens only). Suffixed names must stay within this charset. No dots, underscores, or
   uppercase.

4. **Concurrent runs** — Each run should ALWAYS create its own unique directory by appending a short random suffix (e.g., 4 chars) after the hash:
   `<skill-name>-<label>-<7char-hash>-<4char-random>`. This ensures cleanup is always safe — no two runs share a directory, so one run's cleanup cannot remove another run's files.

5. **Copying subdirectories** — Skills may have subdirectories (references/, scripts/, templates/, etc.). Copy the entire skill directory structure, not just SKILL.md. Internal
   relative paths in the skill body (e.g., `references/foo.md`) work because the structure is preserved.

## Execute Phase

Third phase of the eval runner. Invokes the eval harness for each variant in the pruned run matrix, directing the agent to the appropriate suffixed skill via an attestation prefix. Silent on success — results are passed to the report phase. Loud on failure — if execution fails outright (harness crash, all items failed, unable to start), report back to the originating channel immediately as an error notification.

### Inputs

| Input | Source | Description |
|-------|--------|-------------|
| Dataset name | Sync phase output | Langfuse dataset name from eval.yaml |
| Manifests | Sync phase output | Per-variant manifest paths (each pins the dataset version via --manifest) |
| Suffixed skills | Env setup output | List of (suffixed skill name, directory path, variant label, git hash) |
| Run matrix | Recency check output | Pruned list of (skill variant × model) combinations to execute |
| Dataset items | Variant inference | All items or specific item IDs |
| Repeat count | User request or default | Number of repeats per variant (default: 1) |

### Procedure

#### 1. Construct experiment names

For each (skill variant × model) combination in the pruned run matrix, construct the base experiment name following the naming convention defined in the Recency Check section: `<dataset-name>__<model-id>__<variant-label>__<git-hash>__<item-scope>`. When no model override is specified, use the agent's current default model ID.

The harness automatically appends ` - <timestamp>` (and ` - <run_idx>/<total>` for repeats) to the experiment name at runtime. The base experiment name passed via `--run-name` must NOT include the timestamp or repeat suffix — the harness adds those.

#### 2. Construct attestation prefix

For each skill variant, construct the `--prompt-prefix` that directs the agent to read the suffixed skill created during env setup:

```
Read the <suffixed-skill-name> skill from available_skills. When you respond, the first line of the response must be the path of the skill you read. Then,
```

The suffixed skill name is the `name:` field from the copied SKILL.md (e.g., `linear-create-main-a1b2c3d-x7k2`). The trailing space after "Then," is intentional — the harness prepends this prefix directly to each dataset item's input.

For model A/B tests (Slack-triggered, single skill variant), the same suffixed skill name and attestation prefix is used for both model runs — only the `--model` flag differs between invocations.

#### 3. Invoke the harness

Run `eval_harness.py` for each (skill variant × model) combination in the pruned run matrix. Run sequentially by default to avoid gateway overload — each harness invocation spawns `openclaw agent` subprocesses that consume gateway capacity. Concurrent execution across variants is possible if the gateway has capacity, but sequential is the safe default.

```bash
# Source Langfuse credentials
source ~/.openclaw/secrets/langfuse.env 2>/dev/null

# For each (skill variant × model) combination in the pruned run matrix:
python ~/repos/agentic-testing-framework/src/eval_harness.py \
  --dataset "<dataset-name>" \
  --run-name "<base-experiment-name>" \
  --prompt-prefix "Read the <suffixed-skill-name> skill from available_skills. When you respond, the first line of the response must be the path of the skill you read. Then, " \
  --manifest "<path-to-manifest-from-sync-phase>" \
  --model "<model-id>" \
  --repeat "<repeat-count>" \
  --langfuse-host "http://localhost:3000"
```

Omit `--model` when using the agent's default model. Omit `--repeat` when the repeat count is 1. Pass `--manifest` when the sync phase produced a manifest for this variant — the harness reads the `synced_at` timestamp from the manifest and passes it to Langfuse `get_dataset(version=...)`, pinning the dataset to the exact state at sync time. When a variant's manifest is `null` (new eval.yaml, no sync needed), omit `--manifest` — the harness loads the latest dataset state.

#### 4. Filter to specific dataset items (if applicable)

If the run matrix specifies specific dataset items (not all), pass `--item-id <item-id>` to the harness. The harness supports a single `--item-id` per invocation (partial match). For multiple specific items, run the harness once per item with `--item-id`, using the same base experiment name — the harness creates separate experiment runs with distinct timestamps. Use the item-scope hash for the full item set in the base experiment name for all runs.

#### 5. Capture results

For each harness invocation, capture:
- **Experiment run name(s)** — from the harness stdout summary (the harness prints `Last run name: <name>` in the summary)
- **Completion status** — success (exit 0) or failure (non-zero exit)
- **Per-item failures** — the harness summary logs `N failed items` per run with item indices
- **Dataset run URL** — the Langfuse URL for the experiment run

### Output

Return a structured result for the report phase:

```
runs:
  - variant: <variant-label>
    model: <model-id>
    experiment_name: <base-experiment-name>
    harness_run_names: [<full names from harness stdout, including timestamp suffixes>]
    status: success | partial | failed
    failed_items: [<item indices or ids, if any>]
    error: <error message, if failed>
    dataset_run_url: <langfuse url, if available>
  - variant: <variant-label>
    model: <model-id>
    ...
```

Status values:
- `success` — all items completed, harness exit 0
- `partial` — some items failed but harness completed (exit 0 with failed items logged)
- `failed` — harness crashed (non-zero exit) or all items failed

### Failure Handling

See [`references/execute-failure-handling.md`](references/execute-failure-handling.md) for the full failure handling procedure.

## Cleanup

After the execute and report phases complete, remove the suffixed directories created during Environment Setup. Track which directories were created during env setup and `rm -rf`
only those. Do not remove directories created by other concurrent runs. If the run aborts after env setup (e.g., execute phase failure), cleanup should still run.

Also clean up the sync phase temp directory (`$WORK_DIR`) if it was preserved for the execute phase.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — Canonical CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes,
  and eval.yaml schema.
