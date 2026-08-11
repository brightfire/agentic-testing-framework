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

1. **Exec timeout kills look like OOM** — The exec tool's default timeout is ~120s. The harness run takes 5–30 minutes. If the harness is exec'd without an explicit `timeout` of at least 900s, the exec tool SIGKILLs it at the default timeout. This SIGKILL is indistinguishable from an OOM kill to the agent. Do NOT throttle `--item-concurrency` or `--experiment-concurrency` in response to a killed process unless you have confirmed OOM via `dmesg` or `journalctl — the OOM killer leaves entries there. A timeout kill is not a resource problem; it's a missing `timeout` argument.
2. **Always use `background: true`** — The harness must be started with `exec(background=true, timeout=900)` so it returns a session ID immediately. Then poll with `process(action=poll, timeout=30000)` until completion. Never use a foreground exec with a short `yieldMs` — the harness will be killed before it finishes.
