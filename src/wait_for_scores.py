#!/usr/bin/env python3
"""
Wait for Scores — poll Langfuse until the evaluator has scored all experiment traces.

After the eval harness finishes, the Langfuse evaluator runs asynchronously.
Instead of a fixed sleep, this script polls the scores API and waits until
every expected dataset item has at least one score for each experiment prefix.

Usage:
    # Wait for 5 items across two experiment prefixes (3-minute timeout)
    python3 wait_for_scores.py \
        --dataset linear-create-eval \
        --prefix "linear-create-eval__openrouter-z-ai-glm-5.2__main__a1b2c3d__all" \
        --prefix "linear-create-eval__openrouter-z-ai-glm-5.2__pr-15__e5f6g7h__all" \
        --expected-items 5 \
        --timeout 180

    # Without --expected-items: wait for score count to stabilize between polls
    python3 wait_for_scores.py \
        --dataset linear-create-eval \
        --prefix "linear-create-eval__openrouter-z-ai-glm-5.2__pr-15__e5f6g7h__all"

    # Custom Langfuse host
    python3 wait_for_scores.py \
        --dataset my-eval \
        --prefix "experiment-prefix" \
        --langfuse-host http://localhost:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.

Exit codes:
    0 — all expected scores are present.
    1 — timeout reached before all scores were found (or script error).
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


def fetch_all_scores(langfuse_host, auth_header, limit=100):
    """Fetch all scores from Langfuse, paginating via cursor.

    The scores API uses cursor-based pagination: each response includes
    meta.cursor, which is passed as the `cursor` query param on the next
    request. When meta.cursor is null, pagination is complete.
    """
    scores = []
    cursor = None
    while True:
        params = {"limit": limit, "fields": "core,details,subject"}
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
    """
    try:
        resp = requests.get(
            f"{langfuse_host}/api/public/v2/observations",
            params={
                "traceId": trace_id,
                "fields": "core,basic,io,trace_context",
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
        trace_ctx = root.get("traceContext", {}) or {}
        md = trace_ctx.get("metadata", {}) or {}
        return {
            "experiment_name": md.get("experiment_name", None),
            "dataset_item_id": md.get("dataset_item_id", None),
        }
    except Exception:
        return {}


def build_prefix_item_map(langfuse_host, auth_header, scores, prefixes):
    """Build a mapping of prefix -> set of dataset_item_ids that have scores.

    For each score, fetches trace metadata to determine which experiment
    and dataset item it belongs to, then groups by prefix.
    """
    trace_cache = {}
    prefix_items = {p: set() for p in prefixes}

    for score in scores:
        subject = score.get("subject", {})
        trace_id = subject.get("traceId")
        if not trace_id:
            trace_id = score.get("traceId")
        if not trace_id:
            continue

        if trace_id not in trace_cache:
            trace_cache[trace_id] = fetch_trace_metadata(
                langfuse_host, auth_header, trace_id
            )

        md = trace_cache[trace_id]
        exp_name = md.get("experiment_name")
        dataset_item_id = md.get("dataset_item_id")

        if not exp_name or not dataset_item_id:
            continue

        for prefix in prefixes:
            if exp_name.startswith(prefix):
                prefix_items[prefix].add(dataset_item_id)
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
        "--expected-items", type=int, default=None,
        help="Expected number of dataset items. If provided, waits until each prefix "
             "has scores for that many unique items. If not provided, waits until each "
             "prefix has at least 1 score and the total score count stabilizes."
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
        "--langfuse-host", default="http://10.18.32.57:3000",
        help="Langfuse host URL (default: http://10.18.32.57:3000)"
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
    if args.expected_items:
        log(f"  Expected items per prefix: {args.expected_items}")
    else:
        log("  No expected-items count; will wait for score count stabilization")

    deadline = time.monotonic() + args.timeout
    prev_total = None

    while True:
        try:
            scores = fetch_all_scores(args.langfuse_host, auth_header)
        except Exception as e:
            log(f"Error fetching scores: {e}", "WARN")
            if time.monotonic() >= deadline:
                log("Timeout reached during score fetch.", "ERROR")
                sys.exit(1)
            time.sleep(args.interval)
            continue

        prefix_items = build_prefix_item_map(
            args.langfuse_host, auth_header, scores, prefixes
        )

        total_scores = len(scores)

        # Log progress
        for p in prefixes:
            n = len(prefix_items[p])
            if args.expected_items:
                log(f"  {p}: {n}/{args.expected_items} items scored")
            else:
                log(f"  {p}: {n} items scored (total scores: {total_scores})")

        if args.expected_items:
            # Mode 1: wait until each prefix has scores for expected_items unique items
            all_ready = all(
                len(prefix_items[p]) >= args.expected_items for p in prefixes
            )
            if all_ready:
                log("All prefixes have scores for all expected items. ✓")
                sys.exit(0)
        else:
            # Mode 2: wait until each prefix has >= 1 score AND total count stabilized
            all_have_scores = all(len(prefix_items[p]) >= 1 for p in prefixes)
            stabilized = prev_total is not None and total_scores == prev_total
            if all_have_scores and stabilized:
                log(f"Score count stabilized at {total_scores}. ✓")
                sys.exit(0)
            prev_total = total_scores

        if time.monotonic() >= deadline:
            log("Timeout reached — not all scores are present yet.", "ERROR")
            sys.exit(1)

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
