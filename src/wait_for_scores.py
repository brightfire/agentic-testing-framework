#!/usr/bin/env python3
"""
Wait for Scores — poll Langfuse until the evaluator finishes scoring experiment traces.

After the eval harness finishes, the Langfuse evaluator runs asynchronously.
This script polls the scores API and waits until each prefix has at least one
score and the total score count stabilizes across consecutive polls.

Usage:
    # Wait for scores across two experiment prefixes (3-minute timeout)
    python3 wait_for_scores.py \
        --dataset linear-create-eval \
        --prefix "linear-create-eval__openrouter-z-ai-glm-5.2__main__a1b2c3d__all" \
        --prefix "linear-create-eval__openrouter-z-ai-glm-5.2__pr-15__e5f6g7h__all" \
        --timeout 180

    # Custom Langfuse host
    python3 wait_for_scores.py \
        --dataset my-eval \
        --prefix "experiment-prefix" \
        --langfuse-host http://localhost:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.

Exit codes:
    0 — score count has stabilized (evaluator finished).
    1 — timeout reached or script error.
"""

import argparse
import base64
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def fetch_all_scores(langfuse_host, auth_header, limit=100, from_ts=None):
    """Fetch all scores from Langfuse, paginating via cursor.

    The scores API uses cursor-based pagination: each response includes
    meta.cursor, which is passed as the `cursor` query param on the next
    request. When meta.cursor is null, pagination is complete.

    If from_ts is provided (ISO 8601 string), only scores created at or after
    that timestamp are fetched, preventing stale historical scores from
    satisfying the expected count.
    """
    scores = []
    cursor = None
    while True:
        params = {"limit": limit, "fields": "core,details,subject"}
        if from_ts:
            params["fromTimestamp"] = from_ts
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            f"{langfuse_host}/api/public/v3/scores",
            params=params,
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        scores.extend(data.get("data", []))
        meta = data.get("meta", {})
        next_cursor = meta.get("cursor")
        if not next_cursor:
            break
        cursor = next_cursor
    return scores


def fetch_trace_metadata(langfuse_host, auth_header, trace_id):
    """Fetch experiment metadata for a trace via the observations endpoint.

    Queries /api/public/v2/observations with traceId to get all observations
    for the trace, then reconstructs trace-level metadata from the root
    observation (the one whose parentObservationId is null).

    In Langfuse v4, experiment metadata (experiment_name, dataset_item_id)
    is stored as a top-level ``metadata`` field on the observation.  We
    request the ``metadata`` field explicitly and read from it directly.
    """
    try:
        resp = requests.get(
            f"{langfuse_host}/api/public/v2/observations",
            params={
                "traceId": trace_id,
                "fields": "core,basic,io,metadata",
                "limit": 100,
            },
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        observations = data.get("data", [])
        if not observations:
            return {}
        root = next(
            (o for o in observations if o.get("parentObservationId") is None),
            observations[0],
        )
        md = root.get("metadata", {}) or {}
        return {
            "experiment_name": md.get("experiment_name") or md.get("experiment_run_name", None),
            "dataset_item_id": md.get("dataset_item_id", None),
        }
    except Exception:
        return {}


def build_prefix_item_map(langfuse_host, auth_header, scores, prefixes, metadata_cache):
    """Build a mapping of prefix -> {dataset_item_id: set(trace_id)}.

    For each score, fetches trace metadata to determine which experiment
    and dataset item it belongs to, then groups by prefix. Reuses trace
    metadata from previous polling iterations via metadata_cache (mutated
    in place) to avoid redundant API calls across polls.
    """
    prefix_items = {
        p: defaultdict(set)
        for p in prefixes
    }

    for score in scores:
        subject = score.get("subject", {})
        trace_id = subject.get("traceId")
        if not trace_id:
            trace_id = score.get("traceId")
        if not trace_id:
            continue

        if trace_id not in metadata_cache or not metadata_cache[trace_id]:
            md = fetch_trace_metadata(
                langfuse_host, auth_header, trace_id
            )
            if md:
                metadata_cache[trace_id] = md

        md = metadata_cache.get(trace_id, {})
        exp_name = md.get("experiment_name")
        dataset_item_id = md.get("dataset_item_id")

        if not exp_name or not dataset_item_id:
            continue

        for prefix in prefixes:
            if exp_name.startswith(prefix):
                prefix_items[prefix][dataset_item_id].add(trace_id)
                break

    return prefix_items


def main():
    parser = argparse.ArgumentParser(
        description="Wait for Langfuse evaluator to finish scoring experiment traces"
    )
    parser.add_argument(
        "--dataset", required=True,
        help="Langfuse dataset name (for context/logging)"
    )
    parser.add_argument(
        "--prefix", action="append", required=True, metavar="PREFIX",
        help="Experiment name prefix to check (repeatable for multiple variants)"
    )
    parser.add_argument(
        "--timeout", type=int, default=180,
        help="Maximum seconds to wait (default: 180)"
    )
    parser.add_argument(
        "--interval", type=int, default=10,
        help="Polling interval in seconds (default: 10)"
    )
    parser.add_argument(
        "--langfuse-host", default="http://localhost:3000",
        help="Langfuse host URL (default: http://localhost:3000)"
    )
    parser.add_argument(
        "--since", default=None,
        help="Only include scores created at or after this ISO timestamp "
             "(e.g. 2026-08-10T00:00:00Z). Prevents stale historical scores "
             "from satisfying the expected count."
    )
    args = parser.parse_args()

    # Build auth header
    auth_header = os.environ.get("LANGFUSE_BASIC_AUTH")
    if not auth_header:
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        if public_key and secret_key:
            auth_header = base64.b64encode(
                f"{public_key}:{secret_key}".encode()
            ).decode()
        else:
            log("LANGFUSE_BASIC_AUTH or (LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY) required.", "ERROR")
            sys.exit(1)

    prefixes = args.prefix
    log(f"Waiting for scores on dataset '{args.dataset}' for {len(prefixes)} prefix(es)")
    log(f"  Timeout: {args.timeout}s | Interval: {args.interval}s")
    if args.since:
        log(f"  Since: {args.since}")

    deadline = time.monotonic() + args.timeout
    prev_total = None
    stable_count = 0
    trace_metadata_cache = {}

    while True:
        try:
            scores = fetch_all_scores(args.langfuse_host, auth_header, from_ts=args.since)
        except Exception as e:
            log(f"Error fetching scores: {e}", "WARN")
            if time.monotonic() >= deadline:
                log("Timeout reached during score fetch.", "ERROR")
                sys.exit(1)
            time.sleep(args.interval)
            continue

        prefix_items = build_prefix_item_map(
            args.langfuse_host, auth_header, scores, prefixes, trace_metadata_cache
        )

        # Count total score-bearing traces matching requested prefixes
        prefix_score_count = sum(
            sum(len(traces) for traces in prefix_items[p].values())
            for p in prefixes
        )

        # Log progress
        for p in prefixes:
            n_items = len(prefix_items[p])
            n_traces = sum(len(traces) for traces in prefix_items[p].values())
            log(f"  {p}: {n_items} items scored ({n_traces} traces with scores)")

        # Wait until each prefix has >= 1 score AND count stabilizes
        all_have_scores = all(len(prefix_items[p]) >= 1 for p in prefixes)
        if prev_total is not None and prefix_score_count == prev_total:
            stable_count += 1
        else:
            stable_count = 0
        stabilized = stable_count >= 2
        if all_have_scores and stabilized:
            log(f"Score count stabilized at {prefix_score_count} ({stable_count} consecutive unchanged polls). ✓")
            sys.exit(0)
        prev_total = prefix_score_count

        if time.monotonic() >= deadline:
            log("Timeout reached — scores have not stabilized yet.", "ERROR")
            sys.exit(1)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
