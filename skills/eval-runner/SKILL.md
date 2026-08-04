---
name: eval-runner
description: "Use when evaluating a skill or running tests for a skill — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs. SKIP for skill creation, editing, or auditing requests — use the skill-creator or skill-reviewer skills instead."
metadata:
  author: brightfire
  version: "2.3"
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

After inference builds the full matrix (skill variants × models × dataset items), check Langfuse for matching experiment results and prune combinations with recent results.

**Experiment naming convention:**

```
<dataset-name>__<model-id>__<variant-label>__<git-hash>__<item-scope>
```

- `<model-id>`: provider-qualified model ID with `/` → `-` (e.g., `openrouter-z-ai-glm-5.2`)
- `<variant-label>`: base branch name, `pr-<number>`, or commit ref — with `/` → `-`
- `<item-scope>`: `all` for full dataset, or 8-char SHA-256 prefix of sorted item IDs joined by `|` (e.g., items `['c','a','b']` → `a|b|c` → `sha256('a|b|c')[:8]`)
- Example: `linear-create-eval__openrouter-z-ai-glm-5.2__pr-123__e5f6g7h__all`

Query Langfuse for experiments whose names **start with** this prefix (using resolved commit hash, not wildcard). The harness appends ` - <timestamp>` and optionally ` - <run_idx>/<total>` for repeats, so use prefix match. Item-scope must match exactly. If matching experiments exist within 7 days, reuse them — skip running. Note reused variants and dates in the confirmation summary.

If experiments are missing or older than 7 days, include those variants in the run. First-time runs: check runs normally (nothing to reuse). Reruns: if nothing changed (same commit hash), tell the user and ask to confirm force re-run. If user confirms, prior results excluded, all variants execute.

Remaining skill variants × models multiply after pruning (e.g., 1 variant × 2 models = 2 runs instead of 4).

## Confirmation

After inference and the recency check, present a summary of the pruned run matrix and wait for user confirmation:

- Skill variants (name, git ref, short hash)
- Models to test
- Dataset items (all or specific ids)
- Baseline status: included or skipped (with reason)
- Reused variants: which variants are reused and from when (user can override and force re-run)

The user can **confirm** (proceed to pre-flight) or **adjust** (modify any dimension and re-confirm, re-running the recency check if variants change).

For reruns ("run same test again"), the skill skips variant inference — the user is confirming the previous variant set. The recency check still runs; if nothing changed, ask the user to confirm force re-run.

## Pre-flight Checks

Verify that all variant skills are safe to test. Self-references in skill bodies would break the eval (the suffixed copy would reference the original name, not itself), so check early — there's no reason to sync anything to Langfuse if we can't run the variants.

### Procedure

For each variant spec:

1. **Identify skill-relevant files.** Fetch SKILL.md from the git ref: `git show <ref>:<skill-path>/SKILL.md`. Read its body and collect all file references — paths matching patterns like `references/foo.md`, `scripts/bar.py`, `templates/baz.txt`, or Markdown links like `[text](references/foo.md)`. Then recursively follow references in those files to find transitive dependencies (a referenced file may link to another file), up to depth 3. Fetch each transitively-referenced file from the git ref and repeat the process until no new files are found or depth 3 is reached. The skill-relevant file set is: SKILL.md itself plus the full closure of files reachable through reference chains. Exclude convention/meta files (AGENTS.md, CLAUDE.md, LICENSE, .gitignore, etc.) even if referenced. Exclude `scripts/` files — references in scripts are code comments, not instructional content the LLM follows. Do NOT include files in the skill directory that aren't reachable from SKILL.md through reference chains.
2. **Scan skill-relevant files for self-references.** Fetch each identified file from the git ref via `git show <ref>:<skill-path>/<file>`. Scan each file's content using the whole-word regex: `(?<![a-z0-9-])<skill-name>(?![a-z0-9-])` (case-insensitive). For SKILL.md, scan the body excluding frontmatter; for all other files, scan the entire content. The skill name is the `name:` field from the SKILL.md frontmatter.
3. **If any self-reference is found**, **abort the entire run** — do not proceed to sync or env setup. Report which skill(s) and line(s) contain self-references, and tell the user
   to fix the source skill before re-running.
4. **If all variants pass**, proceed to the Sync Phase.

## Sync Phase

First phase of the eval runner. Syncs eval definitions to Langfuse and captures manifest paths needed by the execute phase.

### Inputs

| Input | Source | Example |
|-------|--------|--------|
| Skill name | Variant specs from Variant Inference | `linear-create` |
| Variant specs | Variant Inference phase | `main@a1b2c3d`, `pr-123@e5f6g7h` |
| eval.yaml path | Relative path within the repo | `skills/linear-create/eval.yaml` |

### Procedure

#### Extract eval.yaml for each variant

Sync every variant that remains after the Recency Check prunes the run matrix. For reruns, sync all variants if the user confirmed a forced re-run; otherwise only sync new or changed variants.

Use `git show` to extract each version to a temp file. This avoids modifying the working tree and works regardless of current checkout state.

Normalize the variant label to `[a-z0-9-]` before using it as a filename (replace `/` with `-`, lowercase, strip dots and underscores).

```bash
WORK_DIR=$(mktemp -d /tmp/eval-sync.XXXXXX)
git show "<variant-ref>:<eval-yaml-path>" > "$WORK_DIR/eval-<variant-label>.yaml"
```

**Edge case — new eval.yaml:** If `git show` fails (file doesn't exist at that ref), skip sync for that variant. Its manifest entry is `null` — the execute phase handles this (fewer variants, use current dataset state as baseline). **Deleted eval.yaml:** If the file should exist but doesn't, flag it for human review.

#### Sync each version to Langfuse

Run `dataset_sync.py` sequentially — concurrent syncs to the same dataset can interleave version timestamps.

```bash
python ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$WORK_DIR/eval-<variant-label>.yaml" \
  --output-manifest "$WORK_DIR/manifest-<variant-label>.json"
```

For the full manifest file contract and CLI interface, see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).

#### Clean up temp files

**Manifest files must be preserved** — the execute phase reads them via `--manifest`. Either write manifests to a durable location and clean up `$WORK_DIR` after copying, or keep `$WORK_DIR` alive until the cleanup phase removes it.

### Output

```
dataset: <langfuse-dataset-name from eval.yaml>
skill: <skill-name>
eval_yaml_path: <path within repo>
manifests:
  - variant: <variant-label>
    ref: <git-hash>
    manifest: <path to manifest-<variant-label>.json or null if skipped>
  ...
```

Pass manifest paths to the execute phase — each pins the dataset state for its variant.

See [`references/gotchas.md`](references/gotchas.md) for Sync Phase error prevention.

## Environment Setup Phase

Second phase of the eval runner. Prepares the eval environment so the harness can run variants in isolation.

### Procedure

For each variant spec:

1. **Fetch the latest** — If the git ref is a branch or PR head (not a fixed commit hash), `git fetch origin "<ref>"` first to ensure you have the latest state. Then fetch the skill from the git ref: `git show "<ref>:<skill-path>/SKILL.md"` and all files in the skill directory.
2. **Create suffixed directory** in `~/.openclaw/workspace/eval-skills/<skill-name>-<label>-<7char-hash>-<4char-random>/`. Normalize the label to `[a-z0-9-]` (replace `/` with `-`, lowercase, strip dots and underscores).
3. **Copy all skill files** into the suffixed directory (preserving subdirectory structure — references/, scripts/, etc.).
4. **Rewrite the `name:` field** in the copied `SKILL.md` frontmatter to match the suffixed directory name (e.g., `linear-create` → `linear-create-main-a1b2c3d-x7k2`).

### Output

List of suffixed directories created in `~/.openclaw/workspace/eval-skills/`, each with: suffixed skill name, directory path, git ref and label.

See [`references/gotchas.md`](references/gotchas.md) for Environment Setup error prevention.

## Execute Phase

Third phase of the eval runner. Invokes the eval harness for each variant in the pruned run matrix, directing the agent to the appropriate suffixed skill via an attestation prefix. Silent on success — results passed to the report phase. Loud on failure — report back to the originating channel immediately as an error notification.

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

For each (skill variant × model) combination, construct the base experiment name following the naming convention defined in the Recency Check section. When no model override is specified, use the agent's current default model ID.

The harness appends ` - <timestamp>` (and ` - <run_idx>/<total>` for repeats) at runtime — the base name passed via `--run-name` must NOT include these suffixes.

#### 2. Construct attestation prefix

```
Read the <suffixed-skill-name> skill from available_skills. When you respond, the first line of the response must be the path of the skill you read. Then,
```

The trailing space after "Then," is intentional — the harness prepends this prefix directly to each dataset item's input. For model A/B tests (Slack-triggered, single skill variant), the same suffixed skill name is used for both model runs — only `--model` differs.

#### 3. Invoke the harness

Run `eval_harness.py` for each (skill variant × model) combination. Run sequentially by default to avoid gateway overload.

```bash
source ~/.openclaw/secrets/langfuse.env 2>/dev/null

python ~/repos/agentic-testing-framework/src/eval_harness.py \
  --dataset "<dataset-name>" \
  --run-name "<base-experiment-name>" \
  --prompt-prefix "Read the <suffixed-skill-name> skill from available_skills. When you respond, the first line of the response must be the path of the skill you read. Then, " \
  --manifest "<path-to-manifest-from-sync-phase>" \
  --model "<model-id>" \
  --repeat "<repeat-count>" \
  --langfuse-host "http://10.18.32.57:3000"
```

Omit `--model` for the agent's default model. Omit `--repeat` when count is 1. Pass `--manifest` when sync produced one — the harness reads `synced_at` and pins the dataset to that state. When manifest is `null`, omit `--manifest` — the harness loads the latest dataset state.

#### 4. Filter to specific dataset items (if applicable)

Pass `--item-id <item-id>` to the harness (single item per invocation, partial match). For multiple specific items, run the harness once per item with `--item-id`, using the same base experiment name.

#### 5. Capture results

For each harness invocation, capture: experiment run name(s) from stdout (`Last run name: <name>`), completion status (exit 0 = success, non-zero = failure), per-item failures (`N failed items` logged), and dataset run URL.

### Output

```
runs:
  - variant: <variant-label>
    model: <model-id>
    experiment_name: <base-experiment-name>
    harness_run_names: [<full names from harness stdout>]
    status: success | partial | failed
    failed_items: [<item indices or ids, if any>]
    error: <error message, if failed>
    dataset_run_url: <langfuse url, if available>
  ...
```

Status: `success` (all items completed, exit 0) | `partial` (some items failed, exit 0) | `failed` (harness crashed or all items failed, non-zero exit).

### Failure Handling

See [`references/execute-failure-handling.md`](references/execute-failure-handling.md) for the full failure handling procedure.

## Cleanup

After execute and report phases complete, remove the suffixed directories created during Environment Setup. Track which directories were created and `rm -rf` only those — do not remove directories from other concurrent runs. If the run aborts after env setup, cleanup should still run. Also clean up `$WORK_DIR` if it was preserved for the execute phase.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, eval.yaml schema.
- [`references/execute-failure-handling.md`](references/execute-failure-handling.md) — Execute phase error handling: immediate notifications, partial failures, multi-variant abort rules.
- [`references/gotchas.md`](references/gotchas.md) — Error-prevention notes for Sync and Environment Setup phases.
