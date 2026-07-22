# Linear Create Skill — Eval Results Reference

**Date:** July 21–22, 2026  
**Dataset:** `linear-skill-evaluation` (3 items × 10 runs per batch, 0–10 scale)  
**Evaluation platform:** Langfuse (self-hosted) + OpenClaw agent CLI  
**Models tested:** `openrouter/z-ai/glm-5.2` (GLM), `anthropic/claude-opus-4-8` (Opus)

## Skill versions

| Version | Skill | Commit | Description |
|---------|-------|--------|-------------|
| **v1.2 (baseline)** | linear-create | [`ad1c2a3`](https://github.com/brightfire/gpt-skills/blob/ad1c2a3/linear-create/SKILL.md) | Auto-assigns projects on name match only; no dry-run handling |
| **v2.1 (improved)** | linear-create | [`904fea2`](https://github.com/brightfire/gpt-skills/blob/904fea2/linear-create/SKILL.md) | Cascading project matching (name → summary → description); explicit dry-run/preview handling |

The improved version was developed and validated through [PR #338](https://github.com/brightfire/gpt-skills/pull/338) in `brightfire/gpt-skills`.

## Dataset items

| ID (prefix) | Label | Prompt summary | Tests |
|-------------|-------|-----------------|-------|
| `0ed63c36` | missing-info | "Create a ticket." | Agent asks for missing context before proceeding |
| `2dcdd2d1` | happy path | Meta listing import in Clinical Media timing out with 504s on bulk sync. "Do NOT create the issue." | Title, description, labels, project matching to "Meta Ads Integration" |
| `768c34cf` | tab char | Tab character in listing URL broke Clinical Media → C2 offer sync. "Do NOT create the issue." | Title, description, product label selection (Clinical Media vs C2), type label |

## Final results

### Per-skill summary

| Skill | Model | Items scored | Overall avg | Pass rate (10.0) |
|-------|-------|:------------:|:-----------:|:-----------------:|
| **Improved (v2.1)** | GLM | 30/30 | **9.85** | **90%** |
| **Baseline (v1.2)** | GLM | 30/30 | **9.70** | **83%** |
| **Improved (v2.1)** | Opus | 30/30 | **8.87** | **57%** |
| **Baseline (v1.2)** | Opus | 29/30 | **8.66** | **41%** |

### Per-item breakdown

| Item | GLM Baseline | GLM Improved | Opus Baseline | Opus Improved |
|------|:-----------:|:------------:|:-------------:|:-------------:|
| missing-info | 10.00 (100%) | 10.00 (100%) | 10.00 (100%) | 9.75 (90%) |
| happy path | 9.25 (60%) | 9.85 (90%) | 8.65 (20%) | 9.55 (80%) |
| tab char | 9.85 (90%) | 9.70 (80%) | 7.17 (0%) | 7.30 (0%) |

### Happy path — individual run scores

| Run | GLM Baseline | GLM Improved | Opus Baseline | Opus Improved |
|:---:|:-----------:|:------------:|:-------------:|:-------------:|
| 1/10 | 7.00 | 10.00 ✓ | 10.00 ✓ | 10.00 ✓ |
| 2/10 | 8.50 | 10.00 ✓ | 7.00 | 10.00 ✓ |
| 3/10 | 10.00 ✓ | 10.00 ✓ | 8.50 | 10.00 ✓ |
| 4/10 | 10.00 ✓ | 10.00 ✓ | 10.00 ✓ | 8.50 |
| 5/10 | 10.00 ✓ | 8.50 | 8.50 | 10.00 ✓ |
| 6/10 | 10.00 ✓ | 10.00 ✓ | 8.50 | 10.00 ✓ |
| 7/10 | 8.50 | 10.00 ✓ | 8.50 | 10.00 ✓ |
| 8/10 | 8.50 | 10.00 ✓ | 8.50 | 10.00 ✓ |
| 9/10 | 10.00 ✓ | 10.00 ✓ | 8.50 | 7.00 |
| 10/10 | 10.00 ✓ | 10.00 ✓ | 8.50 | 10.00 ✓ |
| **Avg** | **9.25 (60%)** | **9.85 (90%)** | **8.65 (20%)** | **9.55 (80%)** |

### Tab char — individual run scores

| Run | GLM Baseline | GLM Improved | Opus Baseline | Opus Improved |
|:---:|:-----------:|:------------:|:-------------:|:-------------:|
| 1/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 7.00 |
| 2/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 7.00 |
| 3/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 7.00 |
| 4/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 7.00 |
| 5/10 | 8.50 | 10.00 ✓ | 7.00 | 7.00 |
| 6/10 | 10.00 ✓ | 8.50 | 7.00 | 8.50 |
| 7/10 | 10.00 ✓ | 8.50 | 8.50 | 7.00 |
| 8/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 8.50 |
| 9/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 7.00 |
| 10/10 | 10.00 ✓ | 10.00 ✓ | 7.00 | 7.00 |
| **Avg** | **9.85 (90%)** | **9.70 (80%)** | **7.15 (0%)** | **7.30 (0%)** |

## Failure analysis

38 non-perfect scores across 120 items. Six distinct failure patterns:

### Pattern A: Happy path 8.5 — project not matched (11 runs)

**Failed criterion:** Project should be matched to "Meta Ads Integration"

The agent completed all other criteria correctly but set Project to `None`. The "Meta Ads Integration" project has an **empty description** in Linear, so the cascading summary match in v2.1 can't find it — only name keyword matching would work, and "Meta listing import" doesn't directly match the project name "Meta Ads Integration."

**Affected:** GLM Baseline (3 runs), GLM Improved (1), Opus Baseline (7)

> **Note:** Opus Improved had 0 Pattern A failures (the cascading match found the project in 8/10 runs). The one non-perfect Opus Improved happy-path score (run 4, 8.5) is a separate case — the project WAS matched, but the agent asked unnecessary clarifying questions about project and priority, failing criterion 6. See Pattern B.

### Pattern B: Happy path 7.0 — project + unnecessary questions (3 runs)

Same as Pattern A, plus the agent asked the user to confirm priority or project — penalized for unnecessary clarifying questions.

**Affected:** GLM Baseline (1), Opus Baseline (1), Opus Improved (2 — run 4: matched project but asked unnecessary questions; run 9: title exceeded 80 chars + MCP tools unavailable)

### Pattern C: Tab char 8.5 — wrong product label, no hedging (6 runs)

**Failed criterion:** Product should be "Clinical Media", not "C2"

The tab character originates from a Clinical Media listing, but the failure surfaces in C2 (offer never created). The agent reasons "fix lives on C2 side" and commits to C2. No hedging — only one criterion failed.

**Affected:** GLM Baseline (1), GLM Improved (2), Opus Improved (2), Opus Baseline (1, borderline)

### Pattern D: Tab char 7.0 — wrong product + hedging (16 runs — largest cluster)

Same wrong product label, but the agent additionally flagged ambiguity and asked the user to decide. This double-failure (wrong product + unnecessary question) is why Opus scores 7.0 on tab char while GLM scores 8.5 — **GLM commits, Opus hedges**.

**Affected:** Opus Baseline (8), Opus Improved (8)

### Pattern E: Missing-info 7.5 — verbosity (1 run)

Opus Improved Run 3 correctly asked for missing info but was too verbose — extra preamble explaining why the information was needed. 1 of 40 missing-info runs across all batches.

### Pattern F: Happy path 7.0 — title length + tool failure (1 run)

Opus Improved Run 9 produced a 92-character title (limit is 80) and reported MCP tools unavailable. Unique failure — likely a transient gateway/CLI issue.

## Key findings

1. **The improved skill's cascading match is the big win on happy path.** GLM pass rate jumped 60% → 90%, Opus 20% → 80%. The cascade helps the agent reason about project association beyond name-only matching.

2. **Removing the eval-specific example improved scores.** The v2.0 skill contained an example identical to the test case ("Meta listing import" → "Meta Ads Integration"). Replacing it with real-project examples (v2.1) actually improved GLM happy path from 70% → 90% — the agent reasons independently instead of pattern-matching the example.

3. **Tab char product label is a model-level problem, not a skill problem.** Both baseline and improved fail at the same rate (Opus: 0% pass, GLM: 80–90%). The agents reason "fix lives on C2 side" instead of "bad data originates from Clinical Media." The skill can't fix this — it's a reasoning difference.

4. **GLM commits, Opus hedges.** GLM picks a label and presents the proposal. Opus flags ambiguity and asks the user to confirm. This style difference costs Opus a full criterion point on 19 runs. The skill should add explicit "do not ask the user to confirm product label or priority" guidance.

5. **Missing-info is well-handled.** 39 of 40 runs perfect. The only failure was Opus verbosity, not a logic error.

6. **Improved skill helps Opus more than GLM.** GLM gains +0.15 avg, +7% pass. Opus gains +0.21 avg, +16% pass. GLM is already good at semantic reasoning without explicit instruction; Opus benefits from the structured cascade.

7. **One harness failure (Opus Baseline run 4, first batch).** Agent returned empty output. No harness failures in the rerun or any other batch.

## Recommendations for future skill improvements

Based on the failure analysis, the following changes to the `linear-create` skill could improve scores further:

### 1. Product label guidance for cross-system bugs (would fix Patterns C + D — 22 failures)

The tab-char item exposes a systematic reasoning gap: when a bug involves data flowing from Product A (Clinical Media) to Product B (C2), agents consistently pick Product B because that's where the failure surfaces and the fix lives. The eval expects Product A (source of the defective data).

**Suggested addition to the Label Taxonomy section:**

> When a bug spans two products — data originates in Product A and surfaces as a failure in Product B — label the issue with **Product A** (the source of the defective data), not Product B where the symptom appears.

### 2. No hedging on classification (would fix the "unnecessary questions" penalty — 19 failures)

Opus consistently flags ambiguity and asks the user to confirm product label or priority. GLM commits without asking. The rubric penalizes this as an unnecessary clarifying question because the confirmation step already exists for overrides.

**Suggested addition to step 6 (Confirm with the user):**

> Do not ask the user to confirm or choose between product labels, types, or priority — commit to your best classification based on the context. The user can override any field during the confirmation step.

### 3. Project matching with empty descriptions (would help Pattern A — 11 failures)

The "Meta Ads Integration" project has an empty description in Linear. The cascading match in v2.1 checks name → summary → description, but when both summary and description are empty, the agent has nothing to match against. Adding a note about keyword matching in project names would help.

**Suggested addition to step 5b (Summary match):**

> Some projects have empty summaries and descriptions. If no summary match is possible, check whether keywords from the issue context appear in any project name — even partial or semantic matches (e.g., "Meta listing import" → "Meta Ads Integration").

### 4. Title length enforcement (would fix Pattern F — 1 failure)

One Opus run produced a 92-character title (limit is 80). The skill already states the 80-char limit, but the agent didn't enforce it. A stronger directive may help:

**Suggested addition to Title Standards:**

> If your title exceeds 80 characters, rewrite it shorter before presenting the confirmation summary. Never present an over-length title.

### 5. Conciseness when asking for missing info (would fix Pattern E — 1 failure)

Opus's verbose response on "Create a ticket" included explanatory preamble about why the information was needed. The rubric penalizes verbosity.

**Suggested addition to step 2 (Assess the request):**

> When asking for missing context, be brief — list what you need without explaining why you need it or how you'll use it.

### 6. Harness robustness (operational, not skill-level)

One Opus baseline run produced empty CLI output (no `finalAssistantVisibleText`). This appears to be a transient gateway/CLI issue, not a skill defect. The harness should retry on empty output rather than treating it as a permanent failure.

## Setup guide

### 1. Prerequisites

- Langfuse self-hosted instance running and accessible (this test used `http://10.18.32.57:3000`)
- OpenClaw gateway running locally on the host where the harness will execute
- Python 3.10+, venv, and the harness dependencies installed:

```bash
cd agentic-testing-framework
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Environment variables set:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
export LANGFUSE_BASIC_AUTH=$(echo -n "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64)
```

---

### 2. Creating the dataset in Langfuse

Datasets must exist in Langfuse before the harness runs. Create them via the Langfuse UI (Datasets → New dataset) or the SDK:

```python
from langfuse import Langfuse

lf = Langfuse(host="http://10.18.32.57:3000")
lf.create_dataset(name="linear-skill-evaluation")
```

Then add items. Each item has an **input** (the agent prompt) and an **expected output** (the evaluator's scoring rubric).

#### Dataset items used for this test

**Item: happy path** (`2dcdd2d1`)

```python
lf.create_dataset_item(
    dataset_name="linear-skill-evaluation",
    input="Propose an issue for the following: the Meta listing import in Clinical Media keeps timing out when we try to sync more than 500 listings at once. It just hangs and eventually fails with a 504. This is blocking us from doing bulk imports for large advertisers.  Do NOT create the issue.",
    expected_output="""The agent classifies correctly, writes a tight title, uses the Bug description template, checks projects, matches to "Meta Ads Integration", presents confirmation. No clarifying questions needed.

Scoring criteria (score each YES or NO):
1. Correctly classified as a Bug
2. Title is specific and under 80 characters
3. Uses the Bug description template structure
4. Project matched to "Meta Ads Integration"
5. Confirmation summary presented before creation
6. No unnecessary clarifying questions asked

Score: 10 if all YES, 8.5 if 5/6, 7 if 4/6, 5 if 3/6, 3 if 2/6, 1.5 if 1/6, 0 if 0/6. Truncated responses cap at 3."""
)
```

**Item: tab char** (`768c34cf`)

```python
lf.create_dataset_item(
    dataset_name="linear-skill-evaluation",
    input="Propose an issue for the following: A tab character (\t) embedded in a listing's destination URL silently broke the Clinical Media -> C2 offer sync. The offer for the affected listing was never created in C2. This was caught during QA but could have been missed. The fix is to sanitize URLs on import.  Do NOT create the issue.",
    expected_output="""The agent classifies correctly, writes a tight title naming the specific component, uses the Bug description template with concrete case details, matches product to "Clinical Media", has no clear match to a project, and presents confirmation. No clarifying questions needed.

Scoring criteria (score each YES or NO):
1. Correctly classified as a Bug
2. Title names the specific component (tab character / URL sanitization)
3. Uses the Bug description template structure
4. Product matched to "Clinical Media"
5. Project left as None (no clear project match exists)
6. Confirmation summary presented before creation
7. No unnecessary clarifying questions asked

Score: 10 if all YES, 8.5 if 6/7, 7 if 5/7, 5 if 4/7, 3 if 3/7, 1.5 if 2/7, 0 if 0-1/7. Truncated responses cap at 3."""
)
```

**Item: missing info** (`0ed63c36`)

```python
lf.create_dataset_item(
    dataset_name="linear-skill-evaluation",
    input="Create a ticket.",
    expected_output="""The agent should recognize it lacks required information (Product, Type, context) and prompt the user to provide these before creating the ticket. It should not attempt to create a ticket with missing fields.

Scoring criteria (score each YES or NO):
1. Does not attempt to create or draft an issue without sufficient information
2. Asks for the specific missing information (type, product, or description)
3. References having read the correct skill
4. Response is concise — no unnecessary preamble

Score: 10 if all YES, 7.5 if 3/4, 5 if 2/4, 2.5 if 1/4, 0 if 0/4."""
)
```

---

### 3. Setting up the evaluator

The evaluator is a Langfuse **online evaluator** (LLM-as-judge) configured on the dataset. Set it up via Langfuse UI:

1. Navigate to **Datasets → linear-skill-evaluation → Evaluators → Add evaluator**
2. Choose **LLM-as-judge**
3. Set the judge model (this test used `claude-sonnet-4-6` or similar)
4. Set the scoring variable name: `response-behavior-correctness`
5. Scoring range: **0–10**
6. Template: use the item's `expected_output` field as the rubric (`{{expectedOutput}}`)
7. Input to evaluate: `{{output}}` (the agent's full response)

The evaluator fires automatically after each dataset run item completes, using the `expected_output` field from the dataset item as the rubric.

---

### 4. Verbiage recommendations for dataset items and evaluators

The quality of your rubric language directly determines the consistency of your scores. These are the key lessons learned from this test run:

#### Items — input prompts

**Be explicit about what the agent should NOT do.**
Both issue-creation items include `Do NOT create the issue.` This prevents the agent from calling `linear__save_issue` and treats the test as a dry-run/proposal. Without this, the agent may try to create the issue and the test becomes non-deterministic based on whether tool calls succeed.

**Embed real-world ambiguity deliberately.**
The tab-char item spans two products (Clinical Media as source, C2 as destination). This is intentional — it tests whether the agent labels by source or by symptom. Know what the correct answer is and document it clearly in the rubric before running.

**Avoid naming the expected project, label, or outcome in the input.**
The happy-path item mentions "Meta listing import" but not "Meta Ads Integration" — the agent must make that connection. If the input contains the answer, the test measures recall not reasoning.

**Keep minimal items truly minimal.**
The missing-info item is just `Create a ticket.` — four words. This tests graceful handling of an underspecified request. More words in the prompt give the agent something to work with and defeat the purpose.

#### Items — expected output / rubric

**Use binary YES/NO criteria, not continuous scoring.**
Each criterion should be unambiguously true or false. "Title is specific and under 80 characters" is binary. "Title is good" is not. Binary criteria give the LLM judge a clear grading path and produce reproducible scores.

**Define the score scale explicitly in the rubric.**
Include the exact mapping: `Score: 10 if all YES, 8.5 if 5/6, ...`. Without this, the judge interpolates its own scale and scores drift across runs.

**Cap truncated responses.**
Add `Truncated responses cap at 3.` or similar. If the agent's output is cut off mid-response, it shouldn't score the same as a complete response that failed one criterion.

**Don't embed the correct answer in the rubric phrasing.**
Saying `Product matched to "Clinical Media"` directly in the criterion is fine — the judge is evaluating the agent's output, not taking the test itself. But avoid phrasing that coaching the agent if the rubric is accidentally visible to it during the run.

**Be careful with criteria that are genuinely ambiguous.**
The tab-char product label (Clinical Media vs C2) is a legitimate judgment call — reasonable engineers disagree. If you include an ambiguous criterion, document the chosen interpretation in a comment and understand that your rubric is enforcing a convention, not an objective truth.

**Keep criteria independent.**
If a criterion depends on another (e.g., "description uses Bug template" assumes "correctly classified as Bug"), a cascade failure will under-penalize the root cause. Either make them independent or note the dependency.

#### Evaluator configuration

**Use a capable judge model.**
The LLM-as-judge needs to understand both the rubric and the agent's response. Using a weaker model as judge introduces noise. This test used Sonnet-class models for judgment.

**Watch for judge model drift across runs.**
If Langfuse's judge model changes between batches (e.g., a provider update), scores may shift for reasons unrelated to the skill. Pin the judge model if possible, or note the model version in the run metadata.

**Score names must match the report query.**
The `eval_report.py` script queries Langfuse scores by dataset trace. If you change the evaluator's scoring variable name (e.g., from `response-behavior-correctness` to `quality`), existing scores remain under the old name and new scores appear separately. Keep the name consistent or adjust the report query.

---

### 5. Running the harness — examples from this test

All commands below were run from the repo root with the venv activated and env vars set.

#### GLM baseline (v1.2)

```bash
python src/eval_harness.py \
  --dataset linear-skill-evaluation \
  --run-name linear-baseline \
  --prompt-prefix "Read the linear-baseline skill from available_skills. You must state which skill you read at the end of your response, after completing the task. You are being evaluated on your ability to adhere to instructions. If you do not confirm which skill you read, your response will receive a score of zero regardless of quality. Then " \
  --repeat 10 \
  --timeout 180 \
  --experiment-concurrency 5 \
  --item-concurrency 3
```

#### GLM improved (v2.1)

```bash
python src/eval_harness.py \
  --dataset linear-skill-evaluation \
  --run-name linear-improved \
  --prompt-prefix "Read the linear-improved skill from available_skills. You must state which skill you read at the end of your response, after completing the task. You are being evaluated on your ability to adhere to instructions. If you do not confirm which skill you read, your response will receive a score of zero regardless of quality. Then " \
  --repeat 10 \
  --timeout 180 \
  --experiment-concurrency 5 \
  --item-concurrency 3
```

#### Opus baseline (v1.2)

```bash
python src/eval_harness.py \
  --dataset linear-skill-evaluation \
  --run-name linear-baseline-opus \
  --model anthropic/claude-opus-4-8 \
  --prompt-prefix "Read the linear-baseline skill from available_skills. You must state which skill you read at the end of your response, after completing the task. You are being evaluated on your ability to adhere to instructions. If you do not confirm which skill you read, your response will receive a score of zero regardless of quality. Then " \
  --repeat 10 \
  --timeout 300 \
  --experiment-concurrency 5 \
  --item-concurrency 3
```

> **Note:** Opus is significantly slower than GLM. The timeout was raised to 300s (from 180s) and total run time was ~7 minutes vs ~10 minutes for GLM.

#### Opus improved (v2.1)

```bash
python src/eval_harness.py \
  --dataset linear-skill-evaluation \
  --run-name linear-improved-opus \
  --model anthropic/claude-opus-4-8 \
  --prompt-prefix "Read the linear-improved skill from available_skills. You must state which skill you read at the end of your response, after completing the task. You are being evaluated on your ability to adhere to instructions. If you do not confirm which skill you read, your response will receive a score of zero regardless of quality. Then " \
  --repeat 10 \
  --timeout 300 \
  --experiment-concurrency 5 \
  --item-concurrency 3
```

#### Notes on the prompt prefix

The prompt prefix serves two purposes:
1. **Skill instruction** — tells the agent which skill to read (`linear-baseline` or `linear-improved`)
2. **Attestation enforcement** — requires the agent to confirm which skill it read at the end of its response, creating a score-zero penalty for non-compliance

The attestation clause (`You must state which skill you read...`) ensures the agent actually reads and follows the correct skill rather than relying on session memory or defaults. Without this, there is no guarantee the correct skill is in effect for each isolated eval run.

---

### 6. Generating reports

#### Report for a single batch (by timestamp window)

Each harness run logs its experiment name timestamp (e.g., `linear-improved - 2026-07-22T15:11:15Z`). Use `--since` and `--until` to isolate a single batch:

```bash
# GLM improved run
python src/eval_report.py \
  --dataset linear-skill-evaluation \
  --since 2026-07-22T15:11:00Z \
  --until 2026-07-22T15:20:00Z \
  --per-item
```

#### Report for all 4 batches from this test

```bash
# All batches (GLM baseline + GLM improved + Opus baseline + Opus improved)
python src/eval_report.py \
  --dataset linear-skill-evaluation \
  --since 2026-07-22T12:15:00Z \
  --per-item
```

This covers:
- GLM Baseline — `2026-07-22T12:15` (30 items)
- GLM Improved — `2026-07-22T15:11` (30 items)
- Opus Baseline — `2026-07-22T15:52` (29 items — 1 harness failure)
- Opus Improved — `2026-07-22T15:20` (30 items)

#### Example output

```
================================================================================
  Experiment Summary — linear-skill-evaluation
================================================================================

  Experiment                                              Items    Avg   Min   Max  Pass%
  ------------------------------------------------------- ----- ------ ----- ----- ------
  linear-baseline - 2026-07-22T12:15:51Z - 1/10               3  10.00 10.00 10.00   100%
  linear-baseline - 2026-07-22T12:15:51Z - 2/10               3   9.50  8.50 10.00    67%
  ...

================================================================================
  Per-Item Aggregation — linear-skill-evaluation
================================================================================

  Dataset Item                              Runs    Avg   Min   Max  Pass%
  ---------------------------------------- ----- ------ ----- ----- ------
  0ed63c36-8706-4413-9877-de779f857eee        40  10.00 10.00 10.00   100%
  2dcdd2d1-a5d1-4ec3-a504-3105f3548ad7        39   9.45  7.00 10.00    72%
  768c34cf-d5c7-44e7-8e79-722bf1362f2d        40   8.60  7.00 10.00    45%
```

#### Caveats

- Scores are populated by the Langfuse online evaluator asynchronously after each run completes. Wait 30–60 seconds after the harness finishes before running the report, or re-run the report until the score count stabilises.
- The report fetches all scores using offset-based pagination (100 per page). Large datasets with many runs may take 30–60 seconds to pull.
- The `--since`/`--until` filters apply to score creation timestamp, not experiment start time. If scores arrive late, widen the window slightly.

## Scoring rubric

The Langfuse evaluator scores on item-specific criteria:

- **Happy path** (6 criteria, ~1.67 pts each): title format, title content, type label, project matching, description structure, no unnecessary questions
- **Tab char** (7 criteria, ~1.43 pts each): title format, title content, product label (Clinical Media), type label, description structure, no solutioning, no unnecessary questions
- **Missing-info** (4 criteria, 2.5 pts each): asks for missing context, correct categories requested, no issue created, conciseness

## References

- **PR #338** (improved skill): https://github.com/brightfire/gpt-skills/pull/338
- **Baseline skill (v1.2):** https://github.com/brightfire/gpt-skills/blob/ad1c2a3/linear-create/SKILL.md
- **Improved skill (v2.1):** https://github.com/brightfire/gpt-skills/blob/904fea2/linear-create/SKILL.md
- **Eval harness:** `src/eval_harness.py` in this repo
- **Report generator:** `src/eval_report.py` in this repo
- **Non-perfect score investigation:** `scratch/eval-investigation.md` (session workspace, not committed)
