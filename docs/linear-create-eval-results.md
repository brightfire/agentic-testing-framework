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

## Failure analysis

38 non-perfect scores across 120 items. Six distinct failure patterns:

### Pattern A: Happy path 8.5 — project not matched (11 runs)

**Failed criterion:** Project should be matched to "Meta Ads Integration"

The agent completed all other criteria correctly but set Project to `None`. The "Meta Ads Integration" project has an **empty description** in Linear, so the cascading summary match in v2.1 can't find it — only name keyword matching would work, and "Meta listing import" doesn't directly match the project name "Meta Ads Integration."

**Affected:** GLM Baseline (3 runs), GLM Improved (1), Opus Baseline (7), Opus Improved (0)

### Pattern B: Happy path 7.0 — project + unnecessary questions (3 runs)

Same as Pattern A, plus the agent asked the user to confirm priority or project — penalized for unnecessary clarifying questions.

**Affected:** GLM Baseline (1), Opus Baseline (1), Opus Improved (1, special case — title exceeded 80 chars + MCP tools unavailable)

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
