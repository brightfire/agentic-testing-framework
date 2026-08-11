# Execute Phase — Failure Handling


## Immediate Error Notification

If any harness invocation fails outright (non-zero exit code, crash, timeout, or all items failed), report back to the originating channel immediately as an error notification. Do NOT wait for the report phase. The notification should include:

- Which variant and model failed
- The error message from the harness
- The experiment name (if one was created)
- Suggestion to check logs and re-run

## Partial Failures

If some items fail but the harness completes (exit 0 with `N failed items` logged), this is NOT an immediate error — capture the failures and pass them to the report phase. The report phase handles per-item failure analysis.

## Multiple Variants

If one variant fails and others succeed, report the failed variant immediately (per above) and continue with the remaining variants. Do not abort the entire execute phase on a single variant failure — only abort if the failure is systemic (e.g., gateway down, Langfuse unreachable, all variants failing).
