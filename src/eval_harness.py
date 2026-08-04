#!/usr/bin/env python3
"""
Skill Eval Harness — DEV-383

Executes a Langfuse dataset against the OpenClaw gateway and records the
results as a named dataset run in Langfuse. Uses the Langfuse SDK's
run_experiment method so that hosted evaluators (LLM-as-judge, code
evaluators) are triggered automatically via OTel attribute propagation.

Usage:
    python eval_harness.py \
        --dataset linear-skill-evaluation \
        --run-name linear-baseline-run-1 \
        --prompt-prefix "Read skill linear-baseline. Then, " \
        --manifest /tmp/eval-sync/manifest-main.json \
        --langfuse-host http://localhost:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
    The harness derives LANGFUSE_BASIC_AUTH (base64 of public:secret) internally
    for trace lookup REST API calls.
    The openclaw agent CLI handles gateway auth internally — the harness
    must run on a host with OpenClaw installed and a running local gateway.
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from langfuse import Langfuse


def load_manifest_version(manifest_path):
    """Load a sync manifest file and extract the dataset version timestamp.

    The manifest is produced by dataset_sync.py --output-manifest and contains
    a 'synced_at' field (ISO 8601 UTC) representing when the sync completed.
    This timestamp is passed to Langfuse get_dataset(version=...) to pin the
    dataset state to exactly what was synced.

    Returns a timezone-aware UTC datetime, or None if the manifest is invalid
    or the timestamp cannot be parsed.
    """
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"Failed to load manifest '{manifest_path}': {e}", "ERROR")
        return None

    synced_at = manifest.get("synced_at")
    if not synced_at:
        log(f"Manifest '{manifest_path}' has no 'synced_at' field", "ERROR")
        return None

    try:
        # Manifest timestamps are ISO 8601 with 'Z' suffix (UTC)
        # datetime.fromisoformat doesn't handle 'Z' until Python 3.11,
        # so replace with '+00:00' for compatibility
        ts_str = synced_at.replace("Z", "+00:00")
        version_dt = datetime.fromisoformat(ts_str)
        if version_dt.tzinfo is None:
            version_dt = version_dt.replace(tzinfo=timezone.utc)
        log(f"Loaded manifest '{manifest_path}' — dataset version pinned to {synced_at}")
        return version_dt
    except ValueError as e:
        log(f"Failed to parse manifest timestamp '{synced_at}': {e}", "ERROR")
        return None


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def validate_model(model_id):
    """Verify that the given model is available on the OpenClaw gateway.
    Returns True if available, False otherwise."""
    try:
        result = subprocess.run(
            ["openclaw", "models", "list"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            log(f"Could not list models: {result.stderr.strip()[:200]}", "WARN")
            return True  # Don't block on failure to list
        # Parse model IDs from the first column
        available = set()
        for line in result.stdout.strip().splitlines():
            # Skip header lines and warnings
            if line.startswith("Config warnings") or line.startswith("Model ") or not line.strip():
                continue
            parts = line.split()
            if parts:
                available.add(parts[0])
        if model_id in available:
            log(f"Model '{model_id}' is available")
            return True
        log(f"Model '{model_id}' is NOT available on this gateway.", "ERROR")
        log(f"Available models: {sorted(available)[:10]}...", "ERROR")
        return False
    except Exception as e:
        log(f"Model validation failed: {e}", "WARN")
        return True  # Don't block on unexpected errors


def find_openclaw_trace_id(langfuse_host, auth_header, session_id, max_wait=15):
    """Look up the OpenClaw trace ID by session ID via the Langfuse REST API.

    The OpenClaw gateway emits traces with `openclaw.sessionId` as a span
    attribute, which Langfuse maps to the trace's top-level `sessionId` field.
    We poll the API for a few seconds after the CLI call returns because the
    OTel exporter may not have flushed yet.
    """
    import time as _time
    for attempt in range(max_wait):
        try:
            resp = requests.get(
                f"{langfuse_host}/api/public/traces",
                params={"sessionId": session_id, "limit": 1},
                headers={"Authorization": f"Basic {auth_header}"},
                timeout=5,
            )
            data = resp.json()
            traces = data.get("data", [])
            if traces:
                return traces[0]["id"]
        except Exception:
            pass
        _time.sleep(1)
    return None


def get_dataset(langfuse_client, dataset_name, version=None):
    """Fetch a Langfuse dataset by name. Returns DatasetClient or exits.

    If version is provided (a timezone-aware UTC datetime), the dataset is
    pinned to the state at that timestamp — matching what was synced by
    dataset_sync.py. This ensures concurrent eval runs don't interfere
    with each other if someone else updates the dataset in between.
    """
    try:
        ds = langfuse_client.get_dataset(dataset_name, version=version)
        if version:
            log(f"Loaded dataset '{dataset_name}' (version pinned to {version.isoformat()}) — {len(ds.items)} items")
        else:
            log(f"Loaded dataset '{dataset_name}' — {len(ds.items)} items")
        return ds
    except Exception as e:
        log(f"Failed to load dataset '{dataset_name}': {e}", "ERROR")
        log("Ensure the dataset exists in Langfuse before running.", "ERROR")
        sys.exit(1)


def make_task(prompt_prefix, agent_id, timeout_seconds,
              langfuse_client, langfuse_host, auth_header, model=None):
    """
    Build a task function for run_experiment.

    The task receives a DatasetItem, sends the prompt to the OpenClaw
    gateway via the CLI, and returns the response text. The SDK handles
    trace creation, OTel attribute propagation, and dataset run linking.

    After the CLI call returns, the task function looks up the OpenClaw
    trace by session ID and stamps it onto the experiment observation's
    metadata so you can navigate from the experiment run item to the
    full OpenClaw agent trace.
    """

    def task(*, item, **kwargs):
        prompt = item.input
        if not prompt:
            raise ValueError("Dataset item has no input")

        if prompt_prefix:
            prompt = prompt_prefix + prompt

        session_key = f"eval-{uuid.uuid4().hex[:12]}-{item.id[:8]}"

        cmd = [
            "openclaw", "agent",
            "--agent", agent_id,
            "--session-key", session_key,
            "--message", prompt,
            "--json",
            "--timeout", str(timeout_seconds),
        ]
        if model:
            cmd.extend(["--model", model])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds + 30,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"CLI timeout after {timeout_seconds + 30}s")

        if result.returncode != 0:
            raise RuntimeError(
                f"CLI exit code {result.returncode}: {result.stderr.strip()[:500]}"
            )

        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start == -1:
            raise RuntimeError(f"No JSON in CLI output. stdout: {stdout[:500]}")

        try:
            data = json.loads(stdout[json_start:])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"JSON parse error: {e}\nstdout: {stdout[:500]}")

        meta = data.get("result", {}).get("meta", {})
        response_text = meta.get("finalAssistantVisibleText", "")

        if not response_text:
            raise RuntimeError("No finalAssistantVisibleText in CLI output")

        # --- Link the OpenClaw trace to the experiment observation ---
        openclaw_session_id = meta.get("agentMeta", {}).get("sessionId")
        if openclaw_session_id:
            openclaw_trace_id = find_openclaw_trace_id(
                langfuse_host, auth_header, openclaw_session_id,
            )
            if openclaw_trace_id:
                log(f"Linked OpenClaw trace: {openclaw_trace_id} (session: {openclaw_session_id})")
                langfuse_client.update_current_span(
                    metadata={
                        "openclaw_trace_id": openclaw_trace_id,
                        "openclaw_session_id": openclaw_session_id,
                    },
                )
            else:
                log(f"Could not find OpenClaw trace for session {openclaw_session_id}", "WARN")
        else:
            log("No sessionId in CLI output — cannot link OpenClaw trace", "WARN")

        return response_text

    return task


def main():
    parser = argparse.ArgumentParser(
        description="Skill Eval Harness — run a Langfuse dataset against the OpenClaw gateway"
    )
    parser.add_argument(
        "--dataset", required=True,
        help="Langfuse dataset name (e.g. linear-skill-evaluation)"
    )
    parser.add_argument(
        "--run-name", required=True,
        help="Name for this experiment run (e.g. linear-baseline-run-1)"
    )
    parser.add_argument(
        "--langfuse-host", default="http://localhost:3000",
        help="Langfuse host URL (default: http://localhost:3000)"
    )
    parser.add_argument(
        "--agent", default="main",
        help="OpenClaw agent ID (default: main)"
    )
    parser.add_argument(
        "--model", default=None,
        help="Model override for the OpenClaw agent (e.g. openrouter/z-ai/glm-5.2)"
    )
    parser.add_argument(
        "--prompt-prefix", default="",
        help="Text to prepend to each dataset item prompt (e.g. 'Read skill linear-baseline. Then, ')"
    )
    parser.add_argument(
        "--timeout", type=int, default=180,
        help="Per-prompt timeout in seconds (default: 180)"
    )
    parser.add_argument(
        "--item-concurrency", type=int, default=1,
        help="Number of dataset items to run in parallel within a single "
             "experiment (default: 1). Each item spawns a separate openclaw "
             "agent subprocess, so set this to the number of concurrent agent "
             "calls your gateway can handle."
    )
    parser.add_argument(
        "--experiment-concurrency", type=int, default=1,
        help="Number of experiment repeats to run in parallel (default: 1). "
             "Combined with --item-concurrency, the total concurrent agent calls "
             "is experiment-concurrency x item-concurrency. For example, with "
             "--experiment-concurrency 2 --item-concurrency 2, up to 4 agent "
             "calls run simultaneously."
    )
    parser.add_argument(
        "--description", default=None,
        help="Optional experiment description for the Langfuse UI"
    )
    parser.add_argument(
        "--repeat", type=int, default=1,
        help="Number of times to process each dataset item within a single experiment run (default: 1)"
    )
    parser.add_argument(
        "--item-id", default=None,
        help="Only run a specific dataset item by ID (partial match supported)"
    )
    parser.add_argument(
        "--manifest", default=None,
        help="Path to a sync manifest JSON file (from dataset_sync.py --output-manifest). "
             "Pins the dataset to the version at sync time by passing the manifest's "
             "synced_at timestamp to Langfuse get_dataset(version=...)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Load dataset and print prompts without submitting to the gateway"
    )
    args = parser.parse_args()

    # --- Validate credentials ---
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        log("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables are required.", "ERROR")
        sys.exit(1)

    # --- Validate model if specified ---
    if args.model:
        if not validate_model(args.model):
            sys.exit(1)

    # --- Init Langfuse SDK ---
    langfuse_client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=args.langfuse_host,
    )

    # --- Load manifest and extract dataset version (if provided) ---
    dataset_version = None
    if args.manifest:
        dataset_version = load_manifest_version(args.manifest)
        if dataset_version is None:
            log(f"Failed to load manifest from '{args.manifest}' — aborting.", "ERROR")
            sys.exit(1)

    # --- Fetch dataset ---
    ds = get_dataset(langfuse_client, args.dataset, version=dataset_version)

    if not ds.items:
        log("Dataset has no items. Nothing to run.", "WARN")
        sys.exit(0)

    # --- Filter to specific item if --item-id is provided ---
    if args.item_id:
        original_count = len(ds.items)
        all_item_ids = [item.id for item in ds.items]
        ds.items = [item for item in ds.items if args.item_id in item.id]
        if not ds.items:
            log(f"No dataset item matching '{args.item_id}' found in dataset '{args.dataset}'.", "ERROR")
            log(f"Available items: {all_item_ids}", "ERROR")
            sys.exit(1)
        log(f"Filtered to {len(ds.items)}/{original_count} items matching '{args.item_id}'")

    if args.dry_run:
        log("=== DRY RUN ===")
        for i, item in enumerate(ds.items):
            prompt = (args.prompt_prefix + item.input) if args.prompt_prefix else item.input
            log(f"Item {i}: {prompt}")
        log(f"Total: {len(ds.items)} items. No prompts submitted.", "INFO")
        return

    # --- Run experiment via SDK ---
    auth_header = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()
    task = make_task(
        prompt_prefix=args.prompt_prefix,
        agent_id=args.agent,
        timeout_seconds=args.timeout,
        langfuse_client=langfuse_client,
        langfuse_host=args.langfuse_host,
        auth_header=auth_header,
        model=args.model,
    )

    # Run experiments — --repeat N creates N separate experiments, each with all dataset items.
    # Naming: <name> - <timestamp> - <run/repeat>
    # Timestamp is captured once so all runs share the same prefix for easy grouping.
    batch_ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    total = args.repeat
    all_results = []

    def run_single_experiment(run_idx):
        """Run a single experiment repeat. Thread-safe: each call creates its
        own OTel/Langfuse trace context."""
        if total > 1:
            exp_name = f"{args.run_name} - {batch_ts} - {run_idx}/{total}"
        else:
            exp_name = f"{args.run_name} - {batch_ts}"

        log(f"=== Starting experiment '{exp_name}' on dataset '{args.dataset}' ({len(ds.items)} items) — run {run_idx}/{total} ===")

        result = ds.run_experiment(
            name=exp_name,
            run_name=exp_name,
            description=args.description or f"Eval harness run on dataset '{args.dataset}'",
            task=task,
            max_concurrency=args.item_concurrency,
        )

        log(f"  Run {run_idx}/{total} complete: {result.run_name}")
        log(f"  Dataset run: {result.dataset_run_url}")
        return run_idx, result

    from concurrent.futures import ThreadPoolExecutor, as_completed
    log(f"Running {total} experiments with experiment-concurrency={args.experiment_concurrency}, item-concurrency={args.item_concurrency}")
    with ThreadPoolExecutor(max_workers=args.experiment_concurrency) as executor:
        futures = {
            executor.submit(run_single_experiment, i): i
            for i in range(1, total + 1)
        }
        for future in as_completed(futures):
            run_idx, result = future.result()
            all_results.append((run_idx, result))
    # Sort by run index for consistent summary output
    all_results.sort(key=lambda x: x[0])
    all_results = [r for _, r in all_results]

    # Use the last result for the summary (all runs share the same dataset)
    result = all_results[-1]

    # --- Summary ---
    log("=== SUMMARY ===")
    log(f"Dataset:       {args.dataset}")
    log(f"Total runs:    {total}")
    log(f"Last run name: {result.run_name}")
    log(f"Items/run:     {len(result.item_results)}")

    total_failed = 0
    for run_idx, run_result in enumerate(all_results, 1):
        failed_items = []
        for i, item_result in enumerate(run_result.item_results):
            output = getattr(item_result, "output", None)
            error = getattr(item_result, "error", None)
            if error is not None or output is None:
                failed_items.append(i)
        if failed_items:
            log(f"  Run {run_idx}/{total} ({run_result.run_name}): {len(failed_items)} failed items", "WARN")
            total_failed += len(failed_items)

    if total_failed:
        log(f"Total failures: {total_failed}", "WARN")
        # If every item across all runs failed, treat as a harness-level failure
        total_items = sum(len(r.item_results) for r in all_results)
        if total_failed >= total_items and total_items > 0:
            log("All items failed — treating as execution failure (exit 1)", "ERROR")
            langfuse_client.flush()
            sys.exit(1)
    else:
        log("All items in all runs completed successfully.", "INFO")

    # Flush Langfuse SDK
    langfuse_client.flush()

    sys.exit(0)


if __name__ == "__main__":
    main()
