# Linear Create Skill — Eval Results Reference

**Date:** July 21–22, 2026  
**Dataset:** `linear-skill-evaluation` (3 items × 10 runs per batch, 0–10 scale)  
**Evaluation platform:** Langfuse (self-hosted) + OpenClaw agent CLI  
**Models tested:** `openrouter/z-ai/glm-5.2` (GLM), `anthropic/claude-opus-4-8` (Opus)

## Skill versions

| Version | Skill | Commit | Description |
|---------|-------|--------|-------------|
| **v1.2 (baseline)** | linear-create | [`eeeeeee`](https://github.com/example-org/skill-repo/blob/eeeeeee/linear-create/SKILL.md) | Auto-assigns projects on name match only; no dry-run handling |
| **v2.1 (improved)** | linear-create | [`fffffff`](https://github.com/example-org/skill-repo/blob/fffffff/linear-create/SKILL.md) | Cascading project matching (name → summary → description); explicit dry-run/preview handling |

The improved version was developed and validated through [PR #125](https://github.com/example-org/skill-repo/pull/125) in `example-org/skill-repo`.

## Dataset items

| ID (prefix) | Label | Prompt summary | Tests |
|-------------|-------|-----------------|-------|
| `0ed63c36` | missing-info | "Create a ticket." | Agent asks for missing context before proceeding |
| `2dcdd2d1` | happy path | Meta listing import in AdHub timing out with 504s on bulk sync. "Do NOT create the issue." | Title, description, labels, project matching to "Meta Ads Integration" |
| `768c34cf` | tab char | Tab character in listing URL broke AdHub → OfferSync offer sync. "Do NOT create the issue." | Title, description, product label selection (AdHub vs OfferSync), type label |

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

**Failed criterion:** Product should be "AdHub", not "OfferSync"

The tab character originates from a AdHub listing, but the failure surfaces in OfferSync (offer never created). The agent reasons "fix lives on OfferSync side" and commits to OfferSync. No hedging — only one criterion failed.

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

3. **Tab char product label is a model-level problem, not a skill problem.** Both baseline and improved fail at the same rate (Opus: 0% pass, GLM: 80–90%). The agents reason "fix lives on OfferSync side" instead of "bad data originates from AdHub." The skill can't fix this — it's a reasoning difference.

4. **GLM commits, Opus hedges.** GLM picks a label and presents the proposal. Opus flags ambiguity and asks the user to confirm. This style difference costs Opus a full criterion point on 19 runs. The skill should add explicit "do not ask the user to confirm product label or priority" guidance.

5. **Missing-info is well-handled.** 39 of 40 runs perfect. The only failure was Opus verbosity, not a logic error.

6. **Improved skill helps Opus more than GLM.** GLM gains +0.15 avg, +7% pass. Opus gains +0.21 avg, +16% pass. GLM is already good at semantic reasoning without explicit instruction; Opus benefits from the structured cascade.

7. **One harness failure (Opus Baseline run 4, first batch).** Agent returned empty output. No harness failures in the rerun or any other batch.

## Scoring rubric

The Langfuse evaluator scores on item-specific criteria:

- **Happy path** (6 criteria, ~1.67 pts each): title format, title content, type label, project matching, description structure, no unnecessary questions
- **Tab char** (7 criteria, ~1.43 pts each): title format, title content, product label (AdHub), type label, description structure, no solutioning, no unnecessary questions
- **Missing-info** (4 criteria, 2.5 pts each): asks for missing context, correct categories requested, no issue created, conciseness

## References

- **PR #125** (improved skill): https://github.com/example-org/skill-repo/pull/125
- **Baseline skill (v1.2):** https://github.com/example-org/skill-repo/blob/eeeeeee/linear-create/SKILL.md
- **Improved skill (v2.1):** https://github.com/example-org/skill-repo/blob/fffffff/linear-create/SKILL.md
- **Eval harness:** `src/eval_harness.py` in this repo
- **Report generator:** `src/eval_report.py` in this repo
