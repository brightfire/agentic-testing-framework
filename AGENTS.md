# AGENTS.md - Agentic Testing Framework

## Overview

This repo is a Langfuse-based evaluation harness for testing OpenClaw agent skills. It contains Python scripts for syncing eval definitions, running evals, and generating reports, plus OpenClaw skills that wrap those scripts.

## Repo Structure

```
src/
  schema.py           — Pydantic v2 schema for eval.yaml (authoritative)
  dataset_sync.py     — Syncs eval.yaml items to Langfuse datasets
  eval_harness.py     — Runs eval items against an OpenClaw gateway
  eval_report.py      — Generates reports from Langfuse eval results
skills/
  eval-runner/        — OpenClaw skill wrapping the harness scripts
    SKILL.md
    eval.yaml         — Test cases for the eval-runner skill itself
    references/       — Supporting docs referenced by SKILL.md
samples/
  eval-sample.yaml    — Example eval.yaml for reference
docs/                 — Eval result snapshots and reference docs
```

## eval.yaml Format

See `src/schema.py` for the authoritative schema (Pydantic v2, `extra="forbid"`) and `samples/eval-sample.yaml` for a complete example.

### Authoring Conventions

**Inputs are natural user requests, not instructions.**
The input field should read like something a user would actually say — a PR context block (webhook metadata) plus a short request like "run the eval for this PR" or "test this against model A and B." Do not walk the agent through the phases or tell it what to do step by step. The skill exists to figure that out.

**Test the skill, don't reference it.**
eval.yaml files test the skill they live alongside. Do not use the skill being tested as an example skill in the test case inputs. Use the skill's own name and paths in the PR context — the agent must discover issues (like self-references) on its own, not be told about them in the input.

**Branch names should match the test case context.**
Use descriptive branch names in PR context blocks: `claw/vash/skill-improvement` for a skill-only change, `claw/vash/skill-and-eval-update` when both SKILL.md and eval.yaml change, `claw/vash/add-eval-yaml` for a new eval definition.

**Phase-targeted test cases are valid.**
Some edge cases only make sense in one phase. It's fine to scope an input to "run only the sync phase" when testing sync-specific behavior. The input should still be a natural user request, not an instruction manual.

**Group scoring criteria by phase for end-to-end items.**
E2E test cases should have `scoring_criteria` grouped by phase with comment markers:

```yaml
scoring_criteria:
  # Phase: Variant Inference
  - "Inference: ..."
  # Phase: Confirmation
  - "Confirmation: ..."
  # Phase: Execute (add criteria when implemented)
  # Phase: Report (add criteria when implemented)
```

This makes it easy to add criteria for future phases without restructuring existing items.

**Line wrapping: 180 chars.**
Prose fields (behavior, input context, prior runs) should wrap at ~180 characters. Do not count indentation against the wrap limit.

**No "reads the skill" in expected output.**
The agent reading the skill is implicit in every eval run. Do not state it in behavior descriptions or include it as a scoring criterion.

## Eval-Specific Skill Notes

- Subdirectories (references/, scripts/) are preserved when a skill is copied for eval isolation
- Self-references in the skill body (the skill name appearing outside frontmatter) will cause pre-flight abort — skills must not reference themselves by name

## Development

### PR Workflow

1. Branch from `main`
2. Make changes
3. Create a PR
4. Do not merge — leave that for the human

### Running Tests

The Python scripts require a virtualenv with `langfuse`, `requests`, `pyyaml` installed:

```bash
cd ~/repos/agentic-testing-framework
source .venv/bin/activate
python src/dataset_sync.py --help
```

Langfuse environment variables must be set:
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

These can be sourced from `~/.openclaw/secrets/langfuse.env`.

### Schema Changes

`src/schema.py` is the authoritative source for the eval.yaml format. If you change the schema, update `schema.py` first, then update any affected eval.yaml files, and update relevant documentation (SKILL.md references, samples, etc.) if the change affects consumers. The schema uses `extra="forbid"` — unknown fields are rejected at all levels.
