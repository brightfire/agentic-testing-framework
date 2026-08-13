#!/usr/bin/env python3
"""
Skill Eval Harness — DEV-383

Executes a Langfuse dataset against the OpenClaw gateway and records the
results as a named dataset run in Langfuse. Uses the Langfuse SDK's
run_experiment method so that hosted evaluators (LLM-as-judge, code
evaluators) are triggered automatically via OTel attribute propagation.

Usage:
    # Manifest mode (preferred — manifest is the source of truth):
    python eval_harness.py \
        --manifest /tmp/eval-sync/manifest-main.json \
        --run-name linear-baseline-run-1 \
        --prompt-prefix "Read skill linear-baseline. Then, " \
        --langfuse-host http://localhost:3000

    # Explicit mode (no manifest):
    python eval_harness.py \
        --dataset linear-skill-evaluation \
        --run-name linear-baseline-run-1 \
        --prompt-prefix "Read skill linear-baseline. Then, " \
        --langfuse-host http://localhost:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
    The harness derives LANGFUSE_BASIC_AUTH (base64 of public:secret) internally
    for trace lookup REST API calls.
    The openclaw agent CLI handles gateway auth internally — the harness
    must run on a host with OpenClaw installed and a running local gateway.
"""

import argparse
import asyncio
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


def load_manifest(manifest_path):
    """Load a sync manifest file and extract dataset name, item IDs, and
    per-item version timestamps.

    The manifest is produced by dataset_sync.py --output-manifest. It contains:
      - "dataset": the Langfuse dataset name
      - "synced_at": overall sync completion time (metadata only — not used
        by the harness for version pinning)
      - "items": list of {"id": <api_id>, "timestamp": <ISO 8601 UTC>}

    The per-item timestamps are used to derive the dataset version for
    Langfuse get_dataset(version=...) by taking the max timestamp across all
    manifest items. This pins the dataset to the state at the last item write,
    which is more precise than synced_at (which is a post-sync wall clock).

    Returns (dataset_name, item_ids, version_datetime, timeout_per_run) or (None, None, None, None)
    if the manifest is invalid.
    """
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log(f"Failed to load manifest '{manifest_path}': {e}", "ERROR")
        return None, None, None, None

    dataset_name = manifest.get("dataset")
    if not dataset_name:
        log(f"Manifest '{manifest_path}' has no 'dataset' field", "ERROR")
        return None, None, None, None

    manifest_items = manifest.get("items")
    if not manifest_items or not isinstance(manifest_items, list):
        log(f"Manifest '{manifest_path}' has no 'items' list", "ERROR")
        return None, None, None, None

    item_ids = []
    item_timestamps = []
    for entry in manifest_items:
        item_id = entry.get("id")
        item_ts = entry.get("timestamp")
        if not item_id or not item_ts:
            log(f"Manifest item missing 'id' or 'timestamp': {entry}", "ERROR")
            return None, None, None, None
        item_ids.append(item_id)
        try:
            ts_str = item_ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            item_timestamps.append(dt)
        except ValueError as e:
            log(f"Failed to parse item timestamp '{item_ts}': {e}", "ERROR")
            return None, None, None, None

    # Derive dataset version from the latest per-item timestamp
    version_dt = max(item_timestamps)
    log(f"Loaded manifest '{manifest_path}' — dataset '{dataset_name}', {len(item_ids)} items, version pinned to {version_dt.isoformat()} (max item timestamp)")
    timeout_per_run = manifest.get("timeout_per_run")
    return dataset_name, item_ids, version_dt, timeout_per_run


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
    attribute, which Langfuse maps to observation metadata. We query
    /api/public/v2/observations, filtering by sessionId, and extract the
    traceId from the first matching observation.

    We poll the API for a few seconds after the CLI call returns because the
    OTel exporter may not have flushed yet.
    """
    import time as _time
    from datetime import timedelta

    filter_json = json.dumps([
        {"type": "string", "column": "sessionId", "operator": "=", "value": session_id}
    ])
    for attempt in range(max_wait):
        try:
            resp = requests.get(
                f"{langfuse_host}/api/public/v2/observations",
                params={
                    "filter": filter_json,
                    "limit": 1,
                    "fields": "core,basic,trace_context",
                },
                headers={"Authorization": f"Basic {auth_header}"},
                timeout=5,
            )
            data = resp.json()
            observations = data.get("data", [])
            if observations:
                return observations[0].get("traceId")
        except Exception:
            pass
        _time.sleep(1)
    return None


def find_skill_used_span(langfuse_host, auth_header, trace_id, session_id=None):
    """Look up the openclaw.skill.used span on a trace via the Langfuse REST API.

    The OpenClaw gateway emits a SPAN observation named 'openclaw.skill.used'
    when the agent reads a SKILL.md file. The span's metadata contains
    attributes.openclaw.skill.name with the exact skill name.

    Queries /api/public/v2/observations for the given trace ID, filtering
    for SPAN type, and finds the observation named 'openclaw.skill.used'.
    Extracts attributes.openclaw.skill.name from its metadata.

    If not found on the given trace and session_id is provided, also checks
    other traces with the same session ID. This handles the retry case where
    the skill.used span is on the original attempt's trace, not the retry's.

    Returns the skill name string, or None if not found.
    """
    def _query_trace_for_skill_span(tid):
        """Query a single trace for the openclaw.skill.used span."""
        try:
            resp = requests.get(
                f"{langfuse_host}/api/public/v2/observations",
                params={
                    "traceId": tid,
                    "type": "SPAN",
                    "fields": "core,basic,io,metadata",
                    "limit": 100,
                },
                headers={"Authorization": f"Basic {auth_header}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("data", [])
            for obs in observations:
                if obs.get("name") == "openclaw.skill.used":
                    md = obs.get("metadata", {}) or {}
                    # The skill name is in attributes.openclaw.skill.name
                    # Langfuse may store it nested or flattened depending on version
                    attrs = md.get("attributes", {}) or {}
                    skill_name = attrs.get("openclaw.skill.name")
                    if not skill_name:
                        # Try flattened key
                        skill_name = md.get("openclaw.skill.name")
                    if not skill_name:
                        # Try io.metadata path
                        io = obs.get("io", {}) or {}
                        io_md = io.get("metadata", {}) or {}
                        skill_name = io_md.get("openclaw.skill.name")
                    return skill_name
        except Exception as e:
            log(f"  Error querying trace {tid} for skill.used span: {e}", "WARN")
        return None

    # 1. Check the given trace
    skill_name = _query_trace_for_skill_span(trace_id)
    if skill_name:
        return skill_name

    # 2. If not found and we have a session_id, check other traces
    #    with the same session ID (retry fallback)
    if session_id:
        try:
            filter_json = json.dumps([
                {"type": "string", "column": "sessionId", "operator": "=", "value": session_id}
            ])
            resp = requests.get(
                f"{langfuse_host}/api/public/v2/observations",
                params={
                    "filter": filter_json,
                    "fields": "core,basic,trace_context",
                    "limit": 50,
                },
                headers={"Authorization": f"Basic {auth_header}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("data", [])
            # Collect unique trace IDs (excluding the one we already checked)
            other_trace_ids = set()
            for obs in observations:
                tid = obs.get("traceId")
                if tid and tid != trace_id:
                    other_trace_ids.add(tid)

            for tid in other_trace_ids:
                skill_name = _query_trace_for_skill_span(tid)
                if skill_name:
                    log(f"  Skill used span found on alternate trace {tid} (session: {session_id})")
                    return skill_name
        except Exception as e:
            log(f"  Error finding alternate traces for session {session_id}: {e}", "WARN")

    return None


def get_dataset(langfuse_client, dataset_name, version=None):
    """Fetch a Langfuse dataset by name. Returns DatasetClient or exits.

    If version is provided (a timezone-aware UTC datetime), the dataset is
    pinned to the state at that timestamp. When using --manifest, the version
    is derived from the max per-item timestamp in the manifest (not synced_at),
    ensuring the dataset reflects exactly what was synced.
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
              langfuse_client, langfuse_host, auth_header, model=None,
              expected_skill_name=None, max_retries=2):
    """
    Build a task function for run_experiment.

    The task receives a DatasetItem, sends the prompt to the OpenClaw
    gateway via the CLI, and returns the response text. The SDK handles
    trace creation, OTel attribute propagation, and dataset run linking.

    After the CLI call returns, the task function looks up the OpenClaw
    trace by session ID and stamps it onto the experiment observation's
    metadata so you can navigate from the experiment run item to the
    full OpenClaw agent trace.

    Harness-level failures (CLI errors, timeouts, no output, JSON parse
    errors) are retried up to max_retries times before raising. A fresh
    session key is generated for each attempt.
    """

    def run_cli(prompt, session_key):
        """Execute a single CLI call. Returns response_text or raises RuntimeError."""
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

        return response_text, meta

    async def task(*, item, **kwargs):
        prompt = item.input
        if not prompt:
            raise ValueError("Dataset item has no input")

        if prompt_prefix:
            prompt = prompt_prefix + prompt

        loop = asyncio.get_event_loop()
        last_error = None
        for attempt in range(1, max_retries + 2):
            session_key = f"eval-{uuid.uuid4().hex[:12]}-{item.id[:8]}"
            try:
                response_text, meta = await loop.run_in_executor(
                    None, lambda: run_cli(prompt, session_key)
                )
                break
            except RuntimeError as e:
                last_error = e
                if attempt <= max_retries:
                    log(f"  Item {item.id}: attempt {attempt}/{max_retries + 1} failed — {e}. Retrying...", "WARN")
                else:
                    log(f"  Item {item.id}: all {max_retries + 1} attempts failed — {e}", "ERROR")
        else:
            raise last_error

        # --- Link the OpenClaw trace to the experiment observation ---
        openclaw_session_id = meta.get("agentMeta", {}).get("sessionId")
        skill_loaded = None
        if openclaw_session_id:
            openclaw_trace_id = find_openclaw_trace_id(
                langfuse_host, auth_header, openclaw_session_id,
            )
            if openclaw_trace_id:
                log(f"Linked OpenClaw trace: {openclaw_trace_id} (session: {openclaw_session_id})")
                # Look up the openclaw.skill.used span for deterministic attestation
                skill_loaded = find_skill_used_span(
                    langfuse_host, auth_header, openclaw_trace_id, openclaw_session_id,
                )
                if skill_loaded:
                    log(f"  Skill loaded: '{skill_loaded}' (verified via openclaw.skill.used span)")
                else:
                    log(f"  No openclaw.skill.used span found — skill_loaded will be null", "WARN")
                # Build metadata for the experiment observation
                span_metadata = {
                    "openclaw_trace_id": openclaw_trace_id,
                    "openclaw_session_id": openclaw_session_id,
                    "skill_loaded": skill_loaded,
                }
                if expected_skill_name:
                    span_metadata["expected_skill_name"] = expected_skill_name
                langfuse_client.update_current_span(
                    metadata=span_metadata,
                )
            else:
                log(f"Could not find OpenClaw trace for session {openclaw_session_id}", "WARN")
                # Still stamp expected_skill_name if we have it
                if expected_skill_name:
                    langfuse_client.update_current_span(
                        metadata={"expected_skill_name": expected_skill_name},
                    )
        else:
            log("No sessionId in CLI output — cannot link OpenClaw trace", "WARN")
            if expected_skill_name:
                langfuse_client.update_current_span(
                    metadata={"expected_skill_name": expected_skill_name},
                )

        return response_text

    return task


def main():
    parser = argparse.ArgumentParser(
        description="Skill Eval Harness — run a Langfuse dataset against the OpenClaw gateway"
    )
    parser.add_argument(
        "--dataset", default=None,
        help="Langfuse dataset name (e.g. linear-skill-evaluation). Required unless --manifest is used."
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
        help="Only run a specific dataset item by ID (partial match supported). Can be used with --manifest to further filter manifest items, or with --dataset."
    )
    parser.add_argument(
        "--expected-skill-name", default=None,
        help="Suffixed skill name the agent should read. Passed as metadata "
             "on each experiment observation so the evaluator can verify the correct "
             "skill was loaded. Verification is deterministic — the harness looks up "
             "the openclaw.skill.used span in the Langfuse trace and stamps "
             "skill_loaded onto the observation metadata. Required when "
             "--prompt-prefix contains an attestation prefix."
    )
    parser.add_argument(
        "--manifest", default=None,
        help="Path to a sync manifest JSON file (from dataset_sync.py --output-manifest). "
             "The manifest is the source of truth: dataset name, item IDs, and per-item "
             "timestamps are read from it. Cannot be used with --dataset."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Load dataset and print prompts without submitting to the gateway"
    )
    args = parser.parse_args()

    # --- Validate mutual exclusivity ---
    if args.manifest and args.dataset:
        log("--manifest cannot be used with --dataset. The manifest is the source of truth.", "ERROR")
        sys.exit(1)

    if not args.manifest and not args.dataset:
        log("Either --manifest or --dataset must be specified.", "ERROR")
        sys.exit(1)

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
        base_url=args.langfuse_host,
    )

    # --- Load manifest or use explicit args ---
    dataset_version = None
    manifest_item_ids = None  # None = no manifest; list = filter to these IDs
    if args.manifest:
        manifest_dataset, manifest_item_ids, dataset_version, manifest_timeout = load_manifest(args.manifest)
        if manifest_dataset is None:
            log(f"Failed to load manifest from '{args.manifest}' — aborting.", "ERROR")
            sys.exit(1)
        dataset_name = manifest_dataset
        # If --timeout wasn't explicitly set and manifest has timeout_per_run, use it
        if manifest_timeout and args.timeout == 180:
            args.timeout = manifest_timeout
            log(f"Using timeout_per_run={manifest_timeout}s from manifest as --timeout", "INFO")
    else:
        dataset_name = args.dataset

    # --- Fetch dataset ---
    ds = get_dataset(langfuse_client, dataset_name, version=dataset_version)

    if not ds.items:
        log("Dataset has no items. Nothing to run.", "WARN")
        sys.exit(0)

    # --- Filter to manifest items if --manifest is used ---
    if manifest_item_ids is not None:
        manifest_id_set = set(manifest_item_ids)
        original_count = len(ds.items)
        ds.items = [item for item in ds.items if item.id in manifest_id_set]
        if not ds.items:
            log(f"No dataset items matched the manifest's {len(manifest_item_ids)} item IDs in dataset '{dataset_name}'.", "ERROR")
            log(f"Manifest item IDs: {sorted(manifest_item_ids)}", "ERROR")
            log(f"Dataset item IDs: {[item.id for item in ds.items]}", "ERROR")
            sys.exit(1)
        missing = manifest_id_set - {item.id for item in ds.items}
        if missing:
            log(f"Warning: {len(missing)} manifest item IDs not found in dataset: {sorted(missing)}", "WARN")
        log(f"Filtered to {len(ds.items)}/{original_count} items from manifest")
        # --- Further filter with --item-id if also provided ---
        if args.item_id:
            pre_filter_count = len(ds.items)
            ds.items = [item for item in ds.items if args.item_id in item.id]
            if not ds.items:
                log(f"No dataset item matching '--item-id {args.item_id}' after manifest filter in dataset '{dataset_name}'.", "ERROR")
                sys.exit(1)
            log(f"--item-id '{args.item_id}' further filtered to {len(ds.items)}/{pre_filter_count} items")
    # --- Filter to specific item if --item-id is provided (no manifest) ---
    elif args.item_id:
        original_count = len(ds.items)
        all_item_ids = [item.id for item in ds.items]
        ds.items = [item for item in ds.items if args.item_id in item.id]
        if not ds.items:
            log(f"No dataset item matching '{args.item_id}' found in dataset '{dataset_name}'.", "ERROR")
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
        expected_skill_name=args.expected_skill_name,
    )

    # Run experiments — --repeat N creates N separate experiments, each with all dataset items.
    # Naming: <name> - <timestamp> - <run/repeat>
    # Timestamp is captured once so all runs share the same prefix for easy grouping.
    batch_ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    total = args.repeat
    all_results = []

    # Build run metadata — expected_skill_name lets the evaluator verify the correct skill was read
    run_metadata = {}
    if args.expected_skill_name:
        run_metadata["expected_skill_name"] = args.expected_skill_name

    def run_single_experiment(run_idx):
        """Run a single experiment repeat. Thread-safe: each call creates its
        own OTel/Langfuse trace context."""
        if total > 1:
            exp_name = f"{args.run_name} - {batch_ts} - {run_idx}/{total}"
        else:
            exp_name = f"{args.run_name} - {batch_ts}"

        log(f"=== Starting experiment '{exp_name}' on dataset '{dataset_name}' ({len(ds.items)} items) — run {run_idx}/{total} ===")

        result = ds.run_experiment(
            name=exp_name,
            run_name=exp_name,
            description=args.description or f"Eval harness run on dataset '{dataset_name}'",
            task=task,
            max_concurrency=args.item_concurrency,
            metadata=run_metadata,
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
    log(f"Dataset:       {dataset_name}")
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
            log(f"  Run {run_idx}/{total} ({run_result.run_name}): {len(failed_items)} failed items — indices: {failed_items}", "WARN")
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
