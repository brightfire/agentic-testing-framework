# Eval Runner Gotchas


## Sync Phase

- **Sync failure:** If any sync exits non-zero, abort the sync phase — do not proceed to the remaining syncs. Report the error from the script output and notify the user. The execute phase needs all manifest files to produce a valid comparison.
- **Same dataset name:** Both before and after versions should reference the same Langfuse dataset name (the `dataset:` field in eval.yaml). If they differ, flag it — that's unusual and may indicate a dataset rename.
- **Python deps:** The agentic-testing-framework requires `langfuse`, `requests`, `pyyaml` — ensure the venv or system Python has these installed. Check `~/repos/agentic-testing-framework/requirements.txt`.
- **Langfuse credentials:** `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` must be set — source from `~/.openclaw/secrets/langfuse.env` if available. The Langfuse host must be reachable (default: `http://10.18.32.57:3000`).

## Environment Setup

1. **eval-skills not configured** — If `~/.openclaw/workspace/eval-skills/` is missing or not listed in `skills.load.extraDirs` in the gateway config, environment setup will fail. Check this first if env setup errors. Once configured, this is unlikely to fail again.
2. **Self-references in skill bodies** — Checked during the pre-flight phase. If any are found, the run aborts before env setup. Fix self-references at the source skill before re-running.
3. **Skill names are normalized** — OpenClaw normalizes skill names to `[a-z0-9-]` (lowercase, hyphens only). Suffixed names must stay within this charset. No dots, underscores, or uppercase.
4. **Concurrent runs** — Each run should ALWAYS create its own unique directory by appending a short random suffix (e.g., 4 chars) after the hash: `<skill-name>-<label>-<7char-hash>-<4char-random>`. This ensures cleanup is always safe — no two runs share a directory, so one run's cleanup cannot remove another run's files.
5. **Copying subdirectories** — Skills may have subdirectories (references/, scripts/, templates/, etc.). Copy the entire skill directory structure, not just SKILL.md. Internal relative paths in the skill body (e.g., `references/foo.md`) work because the structure is preserved.

## Execute Phase

1. **Always use `background: true` with `timeout=900`** — The harness takes 5–30 minutes. The exec tool's default timeout (~120s) will kill it before it finishes. Always start with `exec(background=true, timeout=900)`, then poll with `process(action=poll, timeout=30000)` until completion.
