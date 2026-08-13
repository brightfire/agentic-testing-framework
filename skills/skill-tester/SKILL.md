---
name: skill-tester
description: "Use when evaluating a skill or running tests for a skill — syncs eval definitions to Langfuse, prepares the eval environment, and orchestrates variant runs. SKIP for skill creation, editing, or auditing requests — use the skill-creator or skill-reviewer skills instead."
metadata:
  author: brightfire
  version: "2.5"
---

# Skill Tester

## Variant Inference

Determine what to test from the request. Three dimensions:

**Skill versions** (what skill code to test):
- **PR referenced, no explicit skill specs** → default to two variants: variant A = base ref (e.g., `main@<base-hash>`) and variant B = PR head ref (e.g., `<pr-branch>@<head-hash>`). The variant label is `main`
  (base ref name) for variant A and `pr-<number>` (e.g., `pr-123`) for variant B.
- **Branch named, no PR referenced** → default to two variants: variant A = base ref (e.g., `main@<base-hash>`) and variant B = named branch ref (e.g., `<branch>@<head-hash>`). The variant label is the base ref name (e.g., `main`) for variant A and the normalized branch name for variant B (slashes replaced with hyphens, e.g., `claw-vash-fix-xyz`).
- **Request names specific commits** → use those commits as skill variants. The variant label is the commit hash.
- **Request says "just the PR version" or similar** → single skill variant: `<pr-branch>@<hash>`. The variant label is `pr-<number>` (e.g., `pr-123`).
- **Explicit skill variant specs provided** → use them. The variant label is the branch name or commit hash provided.
- **Request asks for "past N commits" on a branch** → resolve the branch, list the last N commit hashes via `git rev-list --max-count=N <branch>`, and create N skill variants — one per commit. The variant label for each is the short commit hash (7 chars).

**Models** (what models to run each skill variant against):
- **Request mentions model comparison, or names a specific model** → model comparison dimension added. Naming a single specific model (e.g., "test against claude-sonnet-4-6") implies a comparison with the agent default as the first model and the named model as the second.
- **No model mention** → single model (whatever the agent default is)

**Dataset items** (which eval cases to run):
- **No items specified** → all items in the eval.yaml
- **Specific item(s) named** → only those items (by id)

Variant inference outputs (git ref, label) pairs for each variant. Ref resolution pins refs to commit hashes; env setup uses labels for directory naming and experiment names.

**Experiment naming convention:**

```
<dataset-name>__<model-id>__<variant-label>__<git-hash>__<item-scope>
```

- `<model-id>`: provider-qualified model ID, `/` → `-`, `@` preserved
- `<variant-label>`: base branch name, `pr-<number>`, or commit ref, `/` → `-`
- `<item-scope>`: `all` or 8-char SHA-256 prefix of sorted item IDs joined by `|`
- Example: `linear-create-eval__openrouter-@preset-conversation-default__pr-123__e5f6g7h__all`

The harness appends ` - <timestamp>` and optionally ` - <run_idx>/<total>` for repeats at runtime.

## Ref Resolution

**Always resolve branch refs to commit hashes** — including on reruns. Explicit commit hashes from the user need no re-resolution.

After variant inference (and before the recency check), pin all git refs to commit hashes so subsequent phases use a fixed snapshot:

1. Fetch and pin each ref to a commit hash: `git fetch origin "<ref>"` then `git rev-parse "origin/<ref>"` for branch refs. For explicit commit hashes, `git fetch origin "<hash>"` ensures the commit is present locally (no re-resolution needed — the hash is the pin). If `git fetch origin "<branch>"` fails (branch deleted after merge), resolve the head SHA via the PR API: `gh api repos/<owner>/<repo>/pulls/<pr-number> --jq '.head.sha'` (or `.base.sha'` for the base ref). Then `git fetch origin "<sha>"` to ensure the commit is present locally. Use the SHA as the pinned ref.
2. Replace the branch ref with the resolved commit hash in the variant spec.
3. Verify eval.yaml exists at each pinned commit: `git show "<hash>:<eval-yaml-path>"` — if it fails, exclude that skill version from the matrix (nothing to test).

## Recency Check

After ref resolution, run `recency_check.py` for each (skill variant × model) combination. Source `~/.openclaw/secrets/langfuse.env`, then invoke `recency_check.py` with `--dataset` and `--filter` (base experiment name, without timestamp suffix). `--min-pass-percent 75` sets the pass threshold. Count run names on stdout — if count meets requested repeats, prune the combination. If fewer runs exist, only the difference needs to run. Empty stdout means no existing runs — include the combination. Exit 1 = script error (report and stop).


## Confirmation — HARD GATE

⚠️ This is a mandatory stop point. Do NOT proceed to pre-flight, sync, env setup, or execute until the user explicitly confirms. No exceptions.

**Skip:** If the user's request asks to skip confirmation (e.g., "skip confirmation"), always output the confirmation summary (variant matrix, models, items, recency status), then proceed directly to pre-flight without waiting for a reply.

After inference and the recency check, present a confirmation dialog using this exact format and **STOP**. Wait for the user's reply. (Unless skipped — see above.)

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

- **Dataset items:** All N (`<item1>`, `<item2>`, ...) / Specific: `<item-ids>`
- **Repeats per combination:** N (default 10). If the user specifies a repeat count in their request (e.g., "run each test once" → 1, "run 3 repeats" → 3), use that value.

| Model | Variant | Status | Runs |
-------|---------|--------|------|
| `<model-id>` | `<variant-label>` | ✅ Will run (new) / ♻️ Reused (N existing from <date>) | N |

**Total runs:** N

> ⚠️ After confirmation, an evaluator check will run to verify that at least one evaluator is configured for the dataset in Langfuse. If no evaluator is found, the run will stop after sync — no execution will occur.

---

Reply with `@<github bot id> confirm` or `@<github bot id> proceed` to start the run.
```

The `@<github bot id>` mention is required on GitHub. Resolve your bot ID via `gh auth status --hostname github.com --active --json hosts` (pipe through `jq -r '.hosts["github.com"][0].login'` and strip `[bot]`). If the result is `null`, fails, or is unexpected, ask the user for the bot's GitHub login explicitly rather than using a placeholder.

Do not include a mention prefix on Slack or webchat, where the bot receives all messages directly.

If there are multiple models, include one row per model × variant combination in the Run Matrix table. For single-model, single-variant runs, the table collapses to one row.

For reruns where nothing changed and all variants have sufficient runs, replace the Run Matrix section with a note that all variants already have sufficient runs and ask the user to confirm a forced re-run.

Proceed only on explicit `confirm` or `proceed`. On GitHub, the confirm reply must include the bot's GitHub mention (e.g. `@<bot-id> confirm`) for webhook routing. On Slack or webchat, bare `confirm`/`proceed` suffices. If ambiguous, ask for explicit confirmation. Do not interpret silence or a topic change as confirmation.

If confirmation was already given, proceed without re-validating.

The user can **adjust** (modify any dimension and re-confirm, re-running the recency check if variants change).

For reruns: skip variant inference, still resolve refs and run recency check, then present confirmation dialog and STOP. If any ref changed since the previous run, treat as a new variant. If nothing changed and all variants have sufficient runs, ask user to confirm a forced re-run. On confirmation, restore all pruned combinations to the matrix.

## Pre-flight Checks

Scan variant skills for self-references before sync.

### Procedure

For each variant spec:

1. **Identify skill-relevant files.** Fetch SKILL.md from the git ref via `git show <ref>:<skill-path>/SKILL.md`. Collect all file references (paths and Markdown links). Recursively follow references up to depth 3, fetching each from the git ref. Exclude convention/meta files (AGENTS.md, CLAUDE.md, LICENSE, .gitignore) and `scripts/` files.
2. **Scan for self-references.** Fetch each identified file via `git show <ref>:<skill-path>/<file>`. Scan for whole-word self-references (case-insensitive, excluding frontmatter in SKILL.md). The skill name is the `name:` field from SKILL.md frontmatter.
3. **If any self-reference is found**, **abort the entire run** — do not proceed to sync or env setup. Report which skill(s) and line(s) contain self-references, and tell the user
   to fix the source skill before re-running.
4. **If all variants pass**, proceed to the Sync Phase.

## Sync Phase

**Inputs:** Skill name, skill versions (from Variant Inference), eval.yaml path (relative to repo), dataset items (from inference or all).

### Procedure

#### Extract eval.yaml for each skill version

Sync each skill version that remains after the Recency Check prunes the run matrix, filtered to the DSIs selected during inference. For reruns, sync all skill versions if the user confirmed a forced re-run; otherwise only sync new or changed versions.

Use `git show "<skill-version-ref>:<eval-yaml-path>"` to extract each version's eval.yaml to a temp file.

#### Sync each version to Langfuse

Run `dataset_sync.py` sequentially — concurrent syncs to the same dataset can interleave version timestamps. Source langfuse.env, then invoke `dataset_sync.py` with `--file` (path to extracted eval.yaml), `--items` (comma-separated item IDs from inference, or omit for all DSIs), and `--output-manifest` (path to capture the manifest). Capture the manifest path — the execute phase needs it.

For the full manifest file contract and CLI interface, see [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md).


### Output

Output: dataset name, skill name, eval.yaml path, and a list of per-version manifests (version label, git hash, manifest path). Pass manifest paths to the execute phase.

See [`references/gotchas.md`](references/gotchas.md) for Sync Phase error prevention.

## Evaluator Check

Verify at least one enabled evaluator is configured for the dataset.

### Procedure

Run `evaluator_check.py` for every distinct dataset name produced by the sync phase. If any check fails, STOP — do not proceed to env setup or execute. Source langfuse.env, then invoke `evaluator_check.py` with `--dataset` for each distinct dataset name. Exit 0 = evaluator configured (proceed). Exit 1 = no evaluator (STOP, report to user — they need to configure one in the Langfuse UI). Exit 2 = API error (report stderr, retry or investigate). On success, logs matching rule and evaluator names to stderr.

## Environment Setup Phase

### Procedure

For each skill version, invoke `setup_eval_skill.sh` with `--skill-dir`, `--hash` (commit hash), and `--label` (version label).

### Output

List of suffixed directories created in `~/.openclaw/workspace/eval-skills/`, each with: suffixed skill name, directory path, git ref and label.

See [`references/gotchas.md`](references/gotchas.md) for Environment Setup error prevention.

## Execute Phase

**Inputs:** Manifests (sync phase output), suffixed skill names (env setup output), run matrix (recency check output), repeat count (default 10).

If all variants were pruned by the recency check, skip this phase entirely and proceed to the Report Phase. The Execute Phase only runs for variants that need new experiment runs.

### Procedure

#### 1. Construct experiment names

For each (skill variant × model) combination, construct the base experiment name following the naming convention defined in the Variant Inference section. When no model override is specified, use the agent's current default model ID.

The harness appends ` - <timestamp>` (and ` - <run_idx>/<total>` for repeats) at runtime — the base name passed via `--run-name` must NOT include these suffixes.

#### 2. Construct skill-reading prefix

Format: `Read the <suffixed-skill-name> skill from available_skills. Do not spawn subagents or yield — complete all work inline in this single response. Then, ` — pass this verbatim as `--prompt-prefix` in step 3.

For model comparison tests (Slack-triggered, single skill variant), the same suffixed skill name is used for both model runs — only `--model` differs.

#### 3. Invoke the harness

Source langfuse.env, then invoke `eval_harness.py` for each (skill variant × model) combination with: `--manifest` (path from sync phase), `--run-name` (base experiment name without timestamp/repeat suffixes), `--prompt-prefix` (the skill-reading prefix from step 2), `--expected-skill-name` (the suffixed skill name from step 2), `--model` (omit for agent default), `--repeat` (always pass; use the repeat count from the user request — e.g., "run each test once" → 1; default 10 if not specified; subtract recency-found runs), `--item-concurrency 3` (max 6 concurrent subprocesses), and `--experiment-concurrency 2`. Variants run sequentially — one completes before the next begins.


**Computing the exec timeout:**

Read `timeout_per_run` from eval.yaml (default 600s). Compute per invocation:

```
exec_timeout = timeout_per_run * repeat + 120
```

Variants run sequentially in separate exec calls, each with its own timeout. Do not multiply by the number of variants — each exec call runs ONE variant.

**Harness directory:**

`<atf-dir>` is the local agentic-testing-framework repo path. Resolve it at runtime — do not assume a fixed location.

When the skill being evaluated lives in the agentic-testing-framework repo, run the harness from a worktree of the variant ref:

```
worktree_dir=<atf-dir>-worktrees/eval-<short-hash>-$(openssl rand -hex 3)
git worktree add "$worktree_dir" <variant-ref>
<harness-dir>="$worktree_dir"
```

Note: track the exact `$worktree_dir` path for cleanup — it includes a random suffix to avoid collisions when two concurrent evals use the same commit.

For all other repos, use the standard checkout:

```
<harness-dir>=<atf-dir>
```

Clean up the worktree after the eval completes: `git worktree remove --force "$worktree_dir"`

Start the harness in the background, then poll until it completes:

```
exec(
  command="cd <harness-dir> && source ~/.openclaw/secrets/langfuse.env && source <atf-dir>/.venv/bin/activate && python src/eval_harness.py --manifest <path> --run-name '<name>' --prompt-prefix '<prefix>' --expected-skill-name <suffixed-skill-name> --repeat <N> --item-concurrency 3 --experiment-concurrency 2 [--model <model>]",
  background=true,
  timeout=<exec_timeout>
)
```

Then monitor with `process(action=poll, timeout=30000)` every 30s until the process exits.



#### 4. Monitor and capture results

Capture per invocation: exit status, failed item indices (harness logs `N failed items — indices: [...]`), and dataset run URL.

### Output

Output per harness invocation: variant label, model, experiment name, status (success/partial/failed), failed item indices (if any), error message (if failed), and dataset run URL. Status: `success` (all items completed, exit 0) | `partial` (some items failed, exit 0) | `failed` (crash or all items failed, non-zero exit).

### Failure Handling

See [`references/execute-failure-handling.md`](references/execute-failure-handling.md) for the full failure handling procedure.

## Report Phase

**Inputs:** Experiment run names (base prefixes per variant, including recency-pruned), dataset name (sync output), originating channel (request context), skill diff (`git diff <base-ref> <head-ref> -- <skill-path>`).

**All variants pruned:** If the recency check pruned all variants from the run matrix (every variant already has sufficient existing runs), the Execute Phase is a no-op. Proceed directly to the Report Phase using the existing experiment run names from the recency check output as the variant prefixes. Set `--since` to an earlier timestamp or omit it to capture the existing experiment data.

### Procedure

#### 1. Wait for evaluator scoring

After the execute phase completes, the Langfuse evaluator runs asynchronously. Instead of a fixed sleep, poll the Langfuse scores API until all expected scores are present (or a 3-minute timeout is reached).

Source langfuse.env, then invoke `wait_for_scores.py` with: `--dataset`, one `--prefix` per variant (including recency-pruned), `--expected-items` (manifest item count), `--repeat` (execute phase count), `--dimensions` (scoring dimensions, default 1), `--since` (execute phase start ISO timestamp), and `--timeout 180`. The script multiplies `--repeat × --dimensions` to determine required scores per item. Polls every 10s; exits 0 when all prefixes have full coverage, or exits 1 on 3-min timeout.

**Same repeat count across variants:** single call with all prefixes. **Different repeat counts:** call once per variant with per-variant `--repeat` and `--expected-items`. For recency-pruned variants, `--since` may need to be earlier or omitted. If item count is unknown, omit `--expected-items` — the script waits for score count stabilization.

On timeout (exit 1): proceed to fetch scores anyway; note the timeout in the report. If no scores at all, check evaluator configuration.

#### 2. Fetch scores and compare variants

Count the experiment prefixes (variants). The report mode depends on the count:

**2 variants — comparison report:** Invoke `eval_report.py` with `--variants` (first variant prefix, then second variant prefix), `--dataset`, `--by-dimension`, `--threshold 0.5`, `--since` (execute phase start ISO timestamp; earlier or omitted for recency-pruned variants), and `--json`. Include both executed and recency-pruned variants — pruned variant prefixes come from the Recency Check output. Proceed to steps 3–5 (parse, verdict, improvements).

**1 variant — standalone report:** Invoke `eval_report.py` with `--prefix`, `--dataset`, `--by-dimension`, `--per-item`, `--since`, and `--json`. Report per-item scores and dimension breakdowns. Skip verdict and improvement steps.

**3+ variants — raw score report:** Invoke `eval_report.py` with `--variants` (all prefixes, first is variant A), `--dataset`, `--by-dimension`, `--since`, and `--json`. Report per-variant scores and breakdowns only. Do NOT include deltas, verdict, or improvement suggestions — the user reviews the raw data to draw conclusions.

For all modes: if execute start time is unavailable, use a timestamp a few minutes before the earliest experiment run. First prefix is variant A in `--variants` mode. `--json` for structured output.

See [`references/report-format.md`](references/report-format.md) for the JSON output schema.

Steps 3–5 apply only to 2-variant comparison reports.

#### 3. Parse the JSON output

Parse the JSON output from eval_report.py. The structure contains:
- `variants`: per-variant composite scores, dimension breakdowns, and per-item data
- `deltas`: per-item and overall deltas (variant[n] - variant[0])

#### 4. Generate verdict

Based on the deltas, determine the verdict:

- **Improvement:** No variant regressed significantly (all deltas >= -threshold on every dimension or item), and at least one variant improved significantly (delta > +threshold)
- **Regression:** Any variant regressed significantly (delta <= -threshold) on composite or any dimension
- **Neutral:** All deltas strictly within (-threshold, +threshold) — no significant changes

The default threshold is **0.5 points** (on a 0–10 scale where 10.0 is passing). Pass `--threshold <value>` to eval_report.py to adjust sensitivity. Note the threshold used in the report so the reader understands what "significant" means.

#### 5. Generate improvement suggestions

For each item where any dimension's delta <= -threshold (default 0.5):

1. Identify which dimension regressed
2. Obtain the skill diff: `git diff <base-ref> <head-ref> -- <skill-path>`
3. Review the diff for changes that could affect the regressed dimension
4. Suggest a specific fix or area to investigate
5. If the regression is in a dimension unrelated to the skill changes, note that it may be noise

#### 6. Format the report

Format the report as Markdown. For comparison reports (2 variants), read [`references/comparison-report-template.md`](references/comparison-report-template.md). For standalone reports (1 variant), read [`references/standalone-report-template.md`](references/standalone-report-template.md). For raw score reports (3+ variants), read [`references/raw-score-report-template.md`](references/raw-score-report-template.md). Only read the template that applies — not all.

#### 7. Post to the originating channel

- **PR-triggered:** Post as a comment on the PR (via `gh api -X POST repos/<owner>/<repo>/issues/<pr-number>/comments` with a JSON body containing the report markdown)
- **Slack-triggered:** Reply in the Slack thread/channel
- **Web chat:** Reply in the chat session
- **Webhook:** Return as the webhook response (just reply normally)

### Output

Results posted to originating channel. No data passed to a next phase.

### Failure Handling

- **No scores (all experiments have no scores):** Report that scoring hasn't completed yet and suggest waiting longer or checking evaluator configuration.
- **eval_report.py exits non-zero:** Report the error from stderr.
- **Some variants have scores and others don't:** Note which variants are missing data and proceed with available data.

## Cleanup

- `trash` the suffixed directories created during Environment Setup (only those — do not remove directories from other concurrent runs)
- If `$WORK_DIR` was preserved for the execute phase, clean it up
- If a worktree was created for an ATF self-eval: `git worktree remove --force "$worktree_dir"`

## References

- [`references/dataset_sync_interface.md`](references/dataset_sync_interface.md) — CLI interface for `dataset_sync.py`: arguments, env vars, output format, exit codes.
- [`references/execute-failure-handling.md`](references/execute-failure-handling.md) — Execute phase error handling: immediate notifications, partial failures, multi-variant abort rules.
- [`references/gotchas.md`](references/gotchas.md) — Error-prevention notes for Sync and Environment Setup phases.
- [`references/report-format.md`](references/report-format.md) — Report Phase output format, JSON schema, verdict criteria, and improvement suggestion guidance.
- [`references/comparison-report-template.md`](references/comparison-report-template.md) — Comparison report template (2 variants).
- [`references/standalone-report-template.md`](references/standalone-report-template.md) — Standalone report template (1 variant).
- [`references/raw-score-report-template.md`](references/raw-score-report-template.md) — Raw score report template (3+ variants).
- [`src/schema.py`](../../src/schema.py) — eval.yaml schema (Pydantic v2 models).
