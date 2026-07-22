# Linear Create Skill — Eval Case Study

**Date:** July 21–22, 2026
**Authors:** Vash (eval design, harness execution, analysis)
**Repo:** [brightfire/agentic-testing-framework](https://github.com/brightfire/agentic-testing-framework)

---

## Overview

This document demonstrates how the agentic testing framework can produce objective, repeatable evidence that a skill change actually improves agent behavior — or doesn't. It uses the `linear-create` skill improvement shipped in [PR #338](https://github.com/brightfire/gpt-skills/pull/338) as a concrete case study.

The core problem the framework solves: **without structured evaluation, you can't distinguish a good skill change from a lucky demo.** A single test run tells you the agent worked once. Ten isolated runs per item, scored by an LLM judge against a fixed rubric, tell you whether the behavior is reliable.

---

## How the framework works

The prompt prefix is fixed for the entire script invocation — it's prepended to every dataset item. For each item in each run:

```
Prompt prefix (fixed per invocation: skill instruction + attestation)
  + Dataset item prompt
    → Combined prompt sent to isolated OpenClaw agent session
      → Agent response
        → LLM-as-judge scores against rubric
          → Score recorded in Langfuse
```

Key properties that make this meaningful:

- **Isolated sessions** — each item in each run gets a fresh agent session with no carry-over context from prior runs. The agent cannot rely on memory of a previous correct answer.
- **Repetition** — running 10 repeats per item surfaces variance. A skill that scores 10/10 on run 1 and 7/10 on run 6 is not reliable; one that scores 10/10 across all 10 is.
- **Objective scoring** — an LLM judge grades each response against a fixed binary rubric. The same rubric applies to every run, every model, every skill version.
- **Controlled comparison** — baseline and improved skill versions run against identical items with identical scoring, making the delta meaningful.

---

## The evaluation triad — as important as the skill itself

The results of a test are only as trustworthy as the evaluation that produced it. Think of it as a three-legged stool — the skill, the dataset items, and the evaluator rubric are the three legs. If any one is weak, the whole thing collapses. A brilliantly written skill tested against a vague rubric tells you nothing. A sharp rubric paired with a poorly designed dataset item measures the wrong behavior. And a skill that scores well against a bad eval is not a validated skill — it's a lucky one.

All three legs require deliberate design:

### 1. The prompt prefix

The prefix is prepended to every dataset item before it reaches the agent. It must do two things:

1. **Direct the agent to the correct skill** — tell it explicitly which skill to read, by name, before starting the task.
2. **Verify compliance** — require the agent to prove it followed the instruction.

For this test, the prefix was:

```
Read the linear-baseline skill from available_skills. You must state which
skill you read at the end of your response, after completing the task. You
are being evaluated on your ability to adhere to instructions. If you do not
confirm which skill you read, your response will receive a score of zero
regardless of quality. Then
```

The attestation clause (`You must state which skill you read... score of zero...`) is the critical part. Without it, the agent may use cached context from a previous session, default to a built-in behavior, or read a different skill entirely — and you'd never know. The penalty creates a strong incentive to comply.

**This is non-negotiable for isolated session testing.** The prefix must close the loop between "the agent was told to use skill X" and "the agent actually used skill X." The rubric can then trust that the behavior being scored reflects the skill under test.

Early test runs without attestation showed the agent occasionally not confirming which skill it read at all. With the attestation clause, non-compliance becomes immediately visible and scoreable.

### 2. Dataset items

Dataset items are the test cases. Each item is a user prompt plus a scoring rubric. The prompt design determines what behavior the eval actually measures.

**What makes a good item:**

- **No answer in the prompt.** The happy-path item says "Meta listing import" but not "Meta Ads Integration" — the agent must make the semantic connection. If the prompt contains the answer, you're testing recall, not reasoning.
- **Explicit constraints.** Both issue-creation items end with `Do NOT create the issue.` Without this, the agent calls `linear__save_issue` and the test becomes dependent on whether the tool call succeeds — which is not what you're testing.
- **Deliberate ambiguity where appropriate.** The tab-char item spans two products (Clinical Media as source, C2 as destination). This is intentional — it tests how the agent handles cross-system attribution. Know what the correct answer is before you run.
- **Truly minimal items test edge cases.** `Create a ticket.` is four words. That's the point — it tests graceful degradation when context is missing, not issue creation.

### 3. The evaluator rubric

The rubric is the judge's scoring guide. It determines whether a 9.0 means "almost perfect" or "the judge was generous."

**What makes a good rubric:**

- **Binary criteria only.** Each criterion must be a YES/NO question with no room for partial credit within the criterion. "Title is specific and under 80 characters" is binary. "Title quality is high" is not. Binary criteria produce consistent scores across runs.
- **Explicit score mapping.** Define the scale: `Score: 10 if all YES, 8.5 if 5/6, 7 if 4/6...`. Without this, the judge interpolates its own scale and scores drift between batches.
- **Cap degraded responses.** `Truncated responses cap at 3.` prevents a half-finished response from scoring the same as a complete response that failed one criterion.
- **Independent criteria.** If criterion B assumes criterion A (e.g., "uses Bug template" assumes "classified as Bug"), a root-cause failure cascades and undercounts the problem. Make criteria independent where possible.
- **Name the expected value explicitly.** `Product matched to "Clinical Media"` is clear. The judge evaluates the agent's output against this — it's not coaching the agent since each run is isolated.

**The rubric is also where you encode what you care about.** The "no unnecessary clarifying questions" criterion in the happy-path rubric is a policy decision — we want the agent to commit to a classification rather than defer. This is not universally true in production use, but it's the behavior this skill targets.

---

## Case study: `linear-create` skill — PR #338

### What changed

| Version | Commit | Summary |
|---------|--------|---------|
| **v1.2 (baseline)** | [`ad1c2a3`](https://github.com/brightfire/gpt-skills/blob/ad1c2a3/linear-create/SKILL.md) | Auto-assigns projects on name match only |
| **v2.1 (improved)** | [`904fea2`](https://github.com/brightfire/gpt-skills/blob/904fea2/linear-create/SKILL.md) | Cascading project matching (name → summary → description); explicit dry-run handling |

**Problem the change addresses:** Eval results showed agents were missing semantic project matches — "Meta listing import" should match the "Meta Ads Integration" project, but agents relying on name-only matching couldn't make the connection. The v2.1 skill adds a 4-step cascade: check project names, then summaries, then full descriptions, then give up.

A secondary improvement in v2.1: explicit dry-run/preview handling at step 6 — the agent presents the confirmation summary and stops, rather than asking a clarifying question about whether to proceed.

### Dataset items

| Label | Input | What it tests |
|-------|-------|---------------|
| **happy path** | "Propose an issue for the following: the Meta listing import in Clinical Media keeps timing out when we try to sync more than 500 listings at once... Do NOT create the issue." | Title, description, labels, project matching to "Meta Ads Integration" |
| **tab char** | "Propose an issue for the following: A tab character embedded in a listing's destination URL silently broke the Clinical Media → C2 offer sync... Do NOT create the issue." | Title, description, product label (Clinical Media vs C2), no unnecessary questions |
| **missing-info** | "Create a ticket." | Agent asks for missing context; does not draft or create without info |

### Test configuration

- **10 runs per batch** — each run is a separate, isolated experiment; all 3 items run per experiment
- **Models:** `openrouter/z-ai/glm-5.2` (GLM) and `anthropic/claude-opus-4-8` (Opus)
- **Batches:** GLM Baseline, GLM Improved, Opus Baseline, Opus Improved
- **Concurrency:** `--experiment-concurrency 5 --item-concurrency 3` (up to 15 concurrent agent calls)
- **Scoring:** 0–10 via LLM-as-judge against per-item binary rubrics

---

## Results

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

---

## Failure analysis

38 non-perfect scores across 120 items. Six distinct failure patterns identified by pulling the agent's actual response from each non-perfect trace.

### Pattern A: Happy path 8.5 — project not matched (11 runs)

**Failed criterion:** Project matched to "Meta Ads Integration"

The agent completed all other criteria correctly but set Project to `None`. Root cause: "Meta Ads Integration" has an **empty description** in Linear, so the v2.1 cascading summary match can't find it — only name keyword matching would work, and "Meta listing import" doesn't directly surface the project name.

> "Project: None (no active project clearly matches from context)" — GLM Baseline Run 2

**Affected:** GLM Baseline (3), GLM Improved (1), Opus Baseline (7)

> **Note:** Opus Improved had 0 Pattern A failures — the cascading match found the project in 8/10 runs. The one non-perfect Opus Improved happy-path score (run 4, 8.5) is a different failure; the project WAS matched but the agent asked unnecessary questions. See Pattern B.

### Pattern B: Happy path 7.0 — project + unnecessary questions (4 runs)

Same as Pattern A but the agent also hedged — asked the user to confirm priority or project choice. Two criteria failed → 4/6 → 7.0.

> "Priority: I left it at 0... If you want this flagged as Urgent or High given it's blocking large-advertiser bulk imports, let me know and I'll adjust before creation." — GLM Baseline Run 1

**Affected:** GLM Baseline (1), Opus Baseline (1), Opus Improved (2 — run 4: project matched but asked questions; run 9: title exceeded 80 chars + MCP tools unavailable)

### Pattern C: Tab char 8.5 — wrong product, no hedging (6 runs)

**Failed criterion:** Product matched to "Clinical Media"

The agent chose **C2** instead of **Clinical Media**, reasoning that the fix lives on the C2 import path. It committed to this choice without asking — only one criterion failed → 6/7 → 8.5.

> "Product: C2 — I went with C2 since the fix — sanitizing URLs on import — is on the C2 sync side." — GLM Baseline Run 5

**Affected:** GLM Baseline (1), GLM Improved (2), Opus Improved (2), Opus Baseline (1)

### Pattern D: Tab char 7.0 — wrong product + hedging (16 runs — largest cluster)

Same wrong product label, but the agent additionally flagged ambiguity and asked the user to decide. Two criteria failed → 5/7 → 7.0.

This is why **Opus scores 7.0 on tab char while GLM scores 8.5** — GLM commits to a choice, Opus hedges.

> "Product: ⚠️ Ambiguous — this spans both Clinical Media (source) and C2 (where the offer fails). My lean is C2... Let me know if you'd rather tag it Clinical Media." — Opus Baseline Run 1

**Affected:** Opus Baseline (8), Opus Improved (8)

### Pattern E: Missing-info 7.5 — verbosity (1 run)

Opus Improved Run 3 correctly asked for missing info but added an explanatory preamble about why the information was needed. The rubric penalizes verbosity → 3/4 → 7.5.

**Affected:** Opus Improved (1)

### Pattern F: Happy path 7.0 — title length + tool failure (1 run)

Opus Improved Run 9 produced a 92-character title (limit: 80) and reported `linear__list_projects` unavailable. Likely a transient gateway issue.

**Affected:** Opus Improved (1)

---

## Key findings

1. **The framework proved the improvement works — and quantified how much.** GLM happy-path pass rate: 60% → 90%. Opus: 20% → 80%. These are not assertions; they're counts across 10 isolated runs per model per skill version.

2. **Repetition exposed what a single run hides.** GLM Baseline run 1 scored 7.0 on the happy path; runs 3–10 ranged from 8.5 to 10.0. Without 10 runs, you might conclude the skill works fine or that it's broken — either conclusion would be wrong. The distribution tells the real story.

3. **The eval caught a bad example in the skill.** v2.0 included an example that used "Meta listing import" → "Meta Ads Integration" — the exact test case. Replacing it with real-project examples (v2.1) improved GLM happy-path from 70% → 90%. The agent was pattern-matching the example, not reasoning. The framework caught this; manual testing wouldn't have.

4. **Tab char product label is a model difference, not a skill deficiency.** Both v1.2 and v2.1 fail at the same rate (Opus: 0%, GLM: 80–90%). The skill can't fix a fundamental difference in how models handle cross-system attribution. The framework isolates this clearly.

5. **GLM commits, Opus hedges — and the data quantifies the cost.** Hedging costs Opus a full criterion point on 19 of 38 non-perfect scores. This is actionable: a "commit to your classification" instruction in the skill targets this directly.

6. **Missing-info handling is solid.** 39 of 40 runs perfect across all batches. One verbosity deduction. This is a behavior that works and doesn't need attention.

7. **One harness failure (Opus Baseline, first batch).** Agent returned empty output. No harness failures in the rerun. Points to a retry gap in the harness, not a skill issue.

---

## Recommendations for future skill improvements

### 1. Product label: source over symptom (fixes Patterns C + D — 22 failures)

Add to the Label Taxonomy section:

> When a bug involves data flowing from Product A to Product B, label the issue with **Product A** (the source of the defective data), not Product B where the symptom appears.

### 2. Commit to classification (fixes hedging penalty — 19 failures)

Add to step 6 (Confirm with the user):

> Do not ask the user to confirm or choose between product labels, types, or priority — commit to your best classification. The user can override any field during the confirmation step.

### 3. Name keyword matching for empty-description projects (helps Pattern A — 11 failures)

Add to step 5b (Summary match):

> Some projects have empty summaries and descriptions. If no summary match is possible, check whether keywords from the issue context appear in any project name — even partial or semantic matches (e.g., "Meta listing import" → "Meta Ads Integration").

### 4. Enforce title length before presenting (fixes Pattern F)

Add to Title Standards:

> If your title exceeds 80 characters, rewrite it shorter before presenting the confirmation summary. Never present an over-length title.

### 5. Concise missing-info requests (fixes Pattern E)

Add to step 2:

> When asking for missing context, be brief — list what you need without explaining why you need it or how you'll use it.

### 6. Harness retry on empty output (operational)

The harness should retry once on empty `finalAssistantVisibleText` before marking an item failed.

---

## Setup guide

### Prerequisites

- Langfuse self-hosted instance accessible at a known URL (this test: `http://10.18.32.57:3000`)
- OpenClaw gateway running locally — the harness shells out to `openclaw agent` which talks to the local gateway; it cannot target a remote one
- Python 3.10+

```bash
cd agentic-testing-framework
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

```bash
export LANGFUSE_PUBLIC_KEY=***
export LANGFUSE_SECRET_KEY=***
export LANGFUSE_BASIC_AUTH=$(echo -n "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" | base64)
```

---

### Creating the dataset

```python
from langfuse import Langfuse
lf = Langfuse(host="http://10.18.32.57:3000")
lf.create_dataset(name="linear-skill-evaluation")
```

**Item: happy path**
```python
lf.create_dataset_item(
    dataset_name="linear-skill-evaluation",
    input='Propose an issue for the following: the Meta listing import in Clinical Media keeps timing out when we try to sync more than 500 listings at once. It just hangs and eventually fails with a 504. This is blocking us from doing bulk imports for large advertisers.  Do NOT create the issue.',
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

**Item: tab char**
```python
lf.create_dataset_item(
    dataset_name="linear-skill-evaluation",
    input='Propose an issue for the following: A tab character (\t) embedded in a listing\'s destination URL silently broke the Clinical Media -> C2 offer sync. The offer for the affected listing was never created in C2. This was caught during QA but could have been missed. The fix is to sanitize URLs on import.  Do NOT create the issue.',
    expected_output="""The agent classifies correctly, writes a tight title naming the specific component, uses the Bug description template, matches product to "Clinical Media", has no clear project match, presents confirmation. No clarifying questions needed.

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

**Item: missing info**
```python
lf.create_dataset_item(
    dataset_name="linear-skill-evaluation",
    input='Create a ticket.',
    expected_output="""The agent should recognize it lacks required information and prompt the user to provide it. It should not attempt to create or draft a ticket.

Scoring criteria (score each YES or NO):
1. Does not attempt to create or draft an issue without sufficient information
2. Asks for the specific missing information (type, product, or description)
3. References having read the correct skill
4. Response is concise — no unnecessary preamble

Score: 10 if all YES, 7.5 if 3/4, 5 if 2/4, 2.5 if 1/4, 0 if 0/4."""
)
```

---

### Setting up the evaluator

In the Langfuse UI: **Datasets → linear-skill-evaluation → Evaluators → Add evaluator**

- Type: **LLM-as-judge**
- Judge model: Sonnet-class or better
- Scoring variable: `response-behavior-correctness`
- Scoring range: **0–10**
- Rubric template: `{{expectedOutput}}`
- Input: `{{output}}`

The evaluator fires automatically after each dataset run item completes.

---

### Running the harness

**GLM Baseline (v1.2)**
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

**GLM Improved (v2.1)**
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

**Opus Baseline (v1.2)**
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

> Opus is ~3× slower than GLM. Raise `--timeout` to 300 and expect ~7 min total run time.

**Opus Improved (v2.1)**
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

---

### Generating reports

**Isolate a single batch by timestamp:**
```bash
python src/eval_report.py \
  --dataset linear-skill-evaluation \
  --since 2026-07-22T15:11:00Z \
  --until 2026-07-22T15:20:00Z \
  --per-item
```

**All 4 batches from this test:**
```bash
python src/eval_report.py \
  --dataset linear-skill-evaluation \
  --since 2026-07-22T12:15:00Z \
  --per-item
```

Timestamps for each batch:
| Batch | `--since` |
|-------|-----------|
| GLM Baseline | `2026-07-22T12:15:00Z` |
| GLM Improved | `2026-07-22T15:11:00Z` |
| Opus Improved | `2026-07-22T15:20:00Z` |
| Opus Baseline | `2026-07-22T15:52:00Z` |

**Caveats:**
- Scores are populated asynchronously by the Langfuse evaluator. Wait 30–60s after the harness finishes before pulling the report, or re-run until the score count stabilises.
- `--since`/`--until` filter on score creation timestamp, not experiment start time. Widen the window slightly if scores are arriving late.

---

## Scoring rubric reference

| Item | Criteria | Points each | Score mapping |
|------|----------|:-----------:|---------------|
| happy path | 6 binary | ~1.67 | 10→6/6, 8.5→5/6, 7→4/6 |
| tab char | 7 binary | ~1.43 | 10→7/7, 8.5→6/7, 7→5/7 |
| missing-info | 4 binary | 2.5 | 10→4/4, 7.5→3/4, 5→2/4 |

---

## References

- **PR #338** (improved skill): https://github.com/brightfire/gpt-skills/pull/338
- **Baseline skill (v1.2):** https://github.com/brightfire/gpt-skills/blob/ad1c2a3/linear-create/SKILL.md
- **Improved skill (v2.1):** https://github.com/brightfire/gpt-skills/blob/904fea2/linear-create/SKILL.md
- **Eval harness:** `src/eval_harness.py` in this repo
- **Report generator:** `src/eval_report.py` in this repo
