---
name: eval-runner
description: "Use when evaluating a skill or running tests for a skill — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs. SKIP for skill creation, editing, or auditing requests — use the skill-creator or skill-reviewer skills instead."
metadata:
  author: brightfire
  version: "2.4"
---

# Eval Runner

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

**Experiment naming convention:**

```
<dataset-name>__<model-id>__<variant-label>__<git-hash>__<item-scope>
```

- `<model-id>`: provider-qualified model ID with `/` → `-` (e.g., `openrouter-z-ai-glm-5.2`)
- `<variant-label>`: base branch name, `pr-<number>`, or commit ref — with `/` → `-`
- `<item-scope>`: `all` for full dataset, or 8-char SHA-256 prefix of sorted item IDs joined by `|` (e.g., items `['c','a','b']` → `a|b|c` → `sha256('a|b|c')[:8]`)
- Example: `linear-create-eval__openrouter-z-ai-glm-5.2__pr-123__e5f6g7h__all`

The harness appends ` - <timestamp>` and optionally ` - <run_idx>/<total>` for repeats at runtime.

## Ref Resolution

**Always perform ref resolution for branch refs** — branch HEADs move, and stale hashes produce incorrect evals. The only refs exempt from re-resolution are explicit commit hashes provided directly by the user (already pinned by definition). This applies to both initial runs and reruns: reruns skip variant inference but still re-resolve all branch refs before the recency check.

After variant inference (and before the recency check), pin all git refs to commit hashes so subsequent phases use a fixed snapshot:

1. Fetch and pin each ref to a commit hash: `git fetch origin "<ref>"` then `git rev-parse "origin/<ref>"` for branch refs. For explicit commit hashes, `git fetch origin "<hash>"` ensures the commit is present locally (no re-resolution needed — the hash is the pin).
2. Replace the branch ref with the resolved commit hash in the variant spec.
3. All subsequent phases (recency check, pre-flight, sync, env setup) use the pinned commit hash — never the branch name.
4. Verify eval.yaml exists at each pinned commit: `git show "<hash>:<eval-yaml-path>"` — if it fails, exclude that skill version from the matrix (nothing to test).

## Recency Check

After ref resolution, run `recency_check.py` for each (skill variant × model) combination:

```bash
source ~/.openclaw/secrets/langfuse.env 2>/dev/null

python3 ~/repos/agentic-testing-framework/src/recency_check.py \
  --dataset "<dataset-name>" \
  --filter "<base-experiment-name>" \
  --min-pass-percent 75
```

`--filter` is the full base experiment name from the naming convention above. Do not include the harness-added ` - <timestamp>` suffix.

Count the run names on stdout. If the count meets the requested repeat count, prune the combination from the matrix. If fewer runs exist than requested, only the difference needs to run. Empty stdout means no existing runs — include the combination.

Exit 1 = script error. Report and stop.

## Confirmation — HARD GATE

⚠️ This is a mandatory stop point. Do NOT proceed to pre-flight, sync, env setup, or execute until the user explicitly confirms. No exceptions.

After inference and the recency check, present a confirmation dialog using this exact format and **STOP**. Do not run any further commands. Do not call dataset_sync.py, setup_eval_skill.sh, eval_harness.py, or evaluator_check.py. Wait for the user's reply.

### Confirmation Format

Use this template verbatim (adapt the content, keep the structure):

```
## Eval Run Summary — Confirmation Required

**Skill:** `<skill-name>`
**Eval YAML:** `<path/to/eval.yaml>`
**Request:** <one-line summary of what was asked, including any special instructions like forced re-run>

### Skill Variants

| Variant | Git Ref | Has eval.yaml? | Status |
|---------|---------|----------------|--------|
| `<variant-label>` (<source>) | `<short-hash>` | ✅ Yes / ❌ No | **Will run** / Excluded — nothing to test |

<If single variant: note which variant and why. If multiple: note all. If any excluded: explain why.>

### Run Matrix

- **Model:** `<model-id>` (agent default / specified)
- **Dataset items:** All N (`<item1>`, `<item2>`, ...) / Specific: `<item-ids>`
- **Repeats:** N (new / reused from <date> — N existing, N remaining / forced full re-run, ignoring recent runs per request)

---

Reply with `@<github bot id> confirm` or `@<github bot id> proceed` to start the run.
```

The `@<github bot id>` mention is required on GitHub. Resolve your GitHub bot id by running:

```bash
gh auth status --hostname github.com --active --json hosts | jq -r '.hosts["github.com"][0].login' | sed 's/\[bot\]//'
```

Do not include a mention prefix on Slack or webchat, where the bot receives all messages directly.

If there are multiple models, list each on its own line in the Run Matrix. If there are multiple variants, include a row for each in the table.

For reruns where nothing changed and all variants have sufficient runs, replace the Run Matrix section with a note that all variants already have sufficient runs and ask the user to confirm a forced re-run.

**Only proceed when the user replies with `confirm` or `proceed`** (or clearly indicates approval). On GitHub, the reply must include the bot's GitHub id as a mention (e.g. `@<github bot id> confirm`). On Slack or webchat, a bare `confirm`/`proceed` is sufficient. Do not post Slack handles or IDs on GitHub. If the user's reply is ambiguous, ask for explicit confirmation with the correct format for the platform. Do not interpret silence or a topic change as confirmation.

The user can **adjust** (modify any dimension and re-confirm, re-running the recency check if variants change).

For reruns ("run same test again"), the skill skips variant inference — but **the confirmation gate still applies**. After ref resolution and the recency check, present the same confirmation dialog format as above and **STOP**. The user must still reply with `confirm` or `proceed` before any sync or execute commands run. A rerun request is NOT itself confirmation — it's a request to prepare the rerun, not authorization to execute it.

However, **ref resolution always runs** (see Ref Resolution above) — all branch refs are re-fetched and re-pinned, even on rerun. If any ref has changed since the previous run, treat it as a new variant — inform the user that the branch has advanced, update the variant spec with the new hash, and proceed with the updated commit (not the stale one). Only if all refs are unchanged should the recency check proceed against the existing experiment names. If nothing changed and all variants already have sufficient runs, ask the user to confirm a forced re-run. On confirmation, restore all pruned combinations to the matrix.

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

Syncs eval definitions to Langfuse and captures manifest paths needed by the execute phase.

### Inputs

| Input | Source | Example |
|-------|--------|--------|
| Skill name | Variant specs from Variant Inference | `linear-create` |
| Skill versions | Variant Inference phase | `main@a1b2c3d`, `pr-123@e5f6g7h` |
| eval.yaml path | Relative path within the repo | `skills/linear-create/eval.yaml` |
| Dataset items | Variant Inference (which DSIs to test) | `item-1,item-2` or all |

### Procedure

#### Extract eval.yaml for each skill version

Sync each skill version that remains after the Recency Check prunes the run matrix, filtered to the DSIs selected during inference. For reruns, sync all skill versions if the user confirmed a forced re-run; otherwise only sync new or changed versions.

Use `git show` to extract each version to a temp file and pass it to `dataset_sync.py`.

```bash
EVAL_FILE=$(mktemp)
git show "<skill-version-ref>:<eval-yaml-path>" > "$EVAL_FILE"
```

#### Sync each version to Langfuse

Run `dataset_sync.py` sequentially — concurrent syncs to the same dataset can interleave version timestamps.

```bash
MANIFEST_FILE=$(mktemp)
python3 ~/repos/agentic-testing-framework/src/dataset_sync.py \
  --file "$EVAL_FILE" \
  --items "<comma-separated-item-ids from inference>" \
  --output-manifest "$MANIFEST_FILE"
```

Omit `--items` when inference selected all DSIs. Capture `$MANIFEST_FILE` — the execute phase needs it.

For the full manifest file contract and CLI interface, see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).


### Output

```
dataset: <langfuse-dataset-name from eval.yaml>
skill: <skill-name>
eval_yaml_path: <path within repo>
manifests:
  - version: <version-label>
    ref: <git-hash>
    manifest: <path>
  ...
```

Pass manifest paths to the execute phase.

See [`references/gotchas.md`](references/gotchas.md) for Sync Phase error prevention.

## Evaluator Check

After sync (the dataset must exist in Langfuse first) and before env setup/execute (to avoid wasted work), verify that at least one enabled evaluator is configured for the dataset in Langfuse. This check always runs after sync — lack of evaluators means the dataset is not ready for eval and should fail regardless of whether execute is intended.

### Procedure

Run `evaluator_check.py` for every distinct dataset name produced by the sync phase (before and after variants may use different dataset names if the eval.yaml was renamed). If any check fails, STOP — do not proceed to env setup or execute.

```bash
source ~/.openclaw/secrets/langfuse.env 2>/dev/null

python3 ~/repos/agentic-testing-framework/src/evaluator_check.py \
  --dataset "<dataset-name>"
```

- **Exit 0:** at least one enabled evaluation rule targets the dataset. Proceed to Environment Setup.
- **Exit 1:** no enabled evaluator is configured for the dataset. STOP — do not proceed to env setup or execute. Report to the user that no evaluator is configured and they need to set one up in the Langfuse UI before re-running.
- **Exit 2:** API or operational error (credentials, network, rate limit). Report the error from stderr — do not tell the user to configure an evaluator. Retry or investigate the operational issue.

### Output

On success, logs the matching rule name(s) and evaluator name(s) to stderr. On no evaluator (exit 1), prints a clear error message naming the dataset and instructing the user to configure an evaluator in the Langfuse UI. On API error (exit 2), logs the error details to stderr.

## Environment Setup Phase

Prepares the eval environment so the harness can run skill versions in isolation.

### Procedure

For each skill version:

```bash
bash ~/repos/agentic-testing-framework/src/setup_eval_skill.sh \
  --skill-dir "<skill-dir>" \
  --hash "<commit-hash>" \
  --label "<version-label>"
```

### Output

List of suffixed directories created in `~/.openclaw/workspace/eval-skills/`, each with: suffixed skill name, directory path, git ref and label.

See [`references/gotchas.md`](references/gotchas.md) for Environment Setup error prevention.

## Execute Phase

Invokes the eval harness for each variant in the pruned run matrix, directing the agent to the appropriate suffixed skill via an attestation prefix. Silent on success — results passed to the report phase. Loud on failure — report back to the originating channel immediately as an error notification.

**Monitoring:** The harness run is long-running. After invoking it, actively monitor to completion and report results without waiting for the user to ask. Use `process(action=poll, timeout=30000)` to check periodically, or set `yieldMs` high enough to catch completion in a single call. Never background the harness and go silent — the agent that started the run is responsible for bringing results back.

### Inputs

| Input | Source | Description |
|-------|--------|-------------|
| Manifests | Sync phase output | Per-skill-version manifest paths (passed to harness via --manifest) |
| Suffixed skills | Env setup output | Suffixed skill names (for the attestation prefix) |
| Run matrix | Recency check output | Pruned list of (skill variant × model) combinations to execute |
| Repeat count | User request or inference | Number of repeats per variant (default: 10 when not specified by inference) |

### Procedure

#### 1. Construct experiment names

For each (skill variant × model) combination, construct the base experiment name following the naming convention defined in the Variant Inference section. When no model override is specified, use the agent's current default model ID.

The harness appends ` - <timestamp>` (and ` - <run_idx>/<total>` for repeats) at runtime — the base name passed via `--run-name` must NOT include these suffixes.

#### 2. Construct attestation prefix

```
Read the <suffixed-skill-name> skill from available_skills. When you respond, the first line of the response must be the path of the skill you read. Then,
```

For model A/B tests (Slack-triggered, single skill variant), the same suffixed skill name is used for both model runs — only `--model` differs.

#### 3. Invoke the harness

Run `eval_harness.py` for each (skill variant × model) combination. Variants run sequentially — one variant completes before the next begins. Within each variant, up to 5 dataset items run in parallel (`--item-concurrency 5`) and up to 3 experiment repeats run in parallel (`--experiment-concurrency 3`), for a maximum of 15 concurrent agent subprocesses.

```bash
source ~/.openclaw/secrets/langfuse.env 2>/dev/null

python3 ~/repos/agentic-testing-framework/src/eval_harness.py \
  --manifest "<path-to-manifest-from-sync-phase>" \
  --run-name "<base-experiment-name>" \
  --prompt-prefix "Read the <suffixed-skill-name> skill from available_skills. When you respond, the first line of the response must be the path of the skill you read. Then, " \
  --model "<model-id>" \
  --repeat "<repeat-count>" \
  --item-concurrency 5 \
  --experiment-concurrency 3
```

Omit `--model` for the agent's default model. Always pass `--repeat` — default is 10 when inference doesn't specify a count. If recency found existing runs, subtract them from the repeat count (e.g., 10 requested, 4 found → `--repeat 6`).


#### 4. Monitor and capture results

The harness may take several minutes. After starting the harness, poll it to completion — do not background it and wait for the user to ask for status. Once complete, capture for each harness invocation: completion status (exit 0 = success, non-zero = failure), per-item failures (harness logs `N failed items — indices: [...]` with item indices), and dataset run URL.

### Output

```
runs:
  - variant: <variant-label>
    model: <model-id>
    experiment_name: <base-experiment-name>
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

After execute and report phases complete, remove the suffixed directories created during Environment Setup. Track which directories were created and `trash` only those — do not remove directories from other concurrent runs. If the run aborts after env setup, cleanup should still run. Also clean up `$WORK_DIR` if it was preserved for the execute phase.

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes, eval.yaml schema.
- [`references/execute-failure-handling.md`](references/execute-failure-handling.md) — Execute phase error handling: immediate notifications, partial failures, multi-variant abort rules.
- [`references/gotchas.md`](references/gotchas.md) — Error-prevention notes for Sync and Environment Setup phases.
