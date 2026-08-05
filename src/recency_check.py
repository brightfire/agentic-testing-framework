#!/usr/bin/env python3
"""
Recency Check — list recent Langfuse dataset runs matching a filter prefix.

Queries Langfuse for dataset runs whose names start with a given filter prefix,
optionally restricted to a time window (--since), and optionally filtered to
only include runs where at least --min-pass-percent of items have a score > 0.

Output is one run name per line on stdout (machine-consumable). All logging
goes to stderr.

Usage:
    python src/recency_check.py \\
        --dataset linear-skill-evaluation \\
        --filter "linear-baseline" \\
        --since 7d \\
        --min-pass-percent 80 \\
        --langfuse-host http://localhost:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
    Same env config as dataset_sync.py, eval_harness.py, and eval_report.py.
"""

import argparse
import base64
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LANGFUSE_HOST = "http://localhost:3000"
DEFAULT_SINCE = "7d"
DEFAULT_MIN_PASS_PERCENT = 0
PAGE_LIMIT = 100
API_BASE = "/api/public"

# ── Helpers ──────────────────────────────────────────────────────────────────


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr, flush=True)


def make_auth_header(public_key, secret_key):
    """Build the Basic auth header from Langfuse API keys."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return token


def parse_since(since_str):
    """Parse a duration string like '7d', '24h', '2w' into a timedelta.

    Returns a timedelta. Raises ValueError on invalid format.
    """
    match = re.match(r"^(\d+)([dhw])$", since_str.strip())
    if not match:
        raise ValueError(f"Invalid --since format: '{since_str}'. Expected Nd, Nh, or Nw (e.g. 7d, 24h, 2w)")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        return timedelta(days=amount)
    elif unit == "h":
        return timedelta(hours=amount)
    elif unit == "w":
        return timedelta(weeks=amount)
    # Unreachable due to regex, but keeps linters happy
    raise ValueError(f"Unknown time unit: {unit}")


# ── API calls ────────────────────────────────────────────────────────────────


def fetch_dataset_runs(langfuse_host, auth_header, dataset, filter_prefix, cutoff_ts):
    """Fetch dataset runs from Langfuse, paginating and filtering by name prefix and cutoff.

    Runs are returned newest-first by the API. We paginate using meta.totalPages
    and stop early once the oldest run on a page is older than the cutoff.

    Returns a list of run dicts (each has at least 'id' and 'name' keys).
    """
    matching_runs = []
    page = 1
    total_pages = 1  # updated after first request
    separator = " - "

    while page <= total_pages:
        url = f"{langfuse_host}{API_BASE}/datasets/{dataset}/runs"
        params = {"limit": PAGE_LIMIT, "page": page}
        resp = requests.get(
            url,
            params=params,
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=30,
        )
        if resp.status_code == 404:
            # Dataset doesn't exist yet — no prior runs
            log(f"Dataset '{dataset}' not found (404) — treating as no prior runs")
            return []
        if resp.status_code != 200:
            raise RuntimeError(f"Langfuse API error {resp.status_code} fetching dataset runs: {resp.text}")
        data = resp.json()
        runs = data.get("data", [])
        meta = data.get("meta", {})
        total_pages = meta.get("totalPages", 1)

        page_has_old = False
        for run in runs:
            created_at_str = run.get("createdAt", "")
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00")) if created_at_str else None
            if created_at and created_at < cutoff_ts:
                page_has_old = True
                continue
            name = run.get("name", "")
            if name.startswith(filter_prefix + separator):
                matching_runs.append(run)

        if page_has_old:
            log(f"Page {page}: encountered runs older than cutoff, stopping pagination.")
            break

        page += 1

    return matching_runs


def fetch_run_items(langfuse_host, auth_header, dataset_id, run_name):
    """Fetch dataset run items to get the trace IDs for each item in the run.

    Uses GET /api/public/dataset-run-items?datasetId=X&runName=Y.
    Returns a list of dicts, each with at least 'traceId' and 'datasetItemId'.
    """
    url = f"{langfuse_host}{API_BASE}/dataset-run-items"
    params = {"datasetId": dataset_id, "runName": run_name, "limit": PAGE_LIMIT}
    resp = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Basic {auth_header}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Langfuse API error {resp.status_code} fetching run items for '{run_name}': {resp.text}"
        )
    data = resp.json()
    return data.get("data", [])


def check_item_scored(langfuse_host, auth_header, trace_id):
    """Check if a single item (by trace ID) has a score > 0 via the v3 scores API.

    Returns True if the item has at least one score with value > 0, False otherwise
    (including if no scores exist at all).
    """
    url = f"{langfuse_host}{API_BASE}/v3/scores"
    params = {"traceId": trace_id, "limit": 50}
    resp = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Basic {auth_header}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Langfuse API error {resp.status_code} fetching scores for trace {trace_id}: {resp.text}"
        )
    data = resp.json()
    scores = data.get("data", [])
    for score in scores:
        value = score.get("value")
        if isinstance(value, (int, float)) and value > 0:
            return True
    return False


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="List recent Langfuse dataset runs matching a filter prefix.",
    )
    parser.add_argument("--dataset", required=True, help="Langfuse dataset name")
    parser.add_argument("--filter", required=True, help="Experiment name prefix to match")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="How far back to look (e.g. 7d, 24h, 2w). Default: 7d")
    parser.add_argument("--min-pass-percent", type=float, default=0, help="Minimum %% of items with score > 0 to include a run (0-100, default: 0 = include all)")
    parser.add_argument("--langfuse-host", default=DEFAULT_LANGFUSE_HOST, help="Langfuse host URL")
    args = parser.parse_args()

    # Validate --since format early
    try:
        delta = parse_since(args.since)
    except ValueError as e:
        log(str(e), "ERROR")
        sys.exit(1)

    # Validate min-pass-percent range
    if not (0 <= args.min_pass_percent <= 100):
        log("--min-pass-percent must be between 0 and 100", "ERROR")
        sys.exit(1)

    # Auth
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        log("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables are required", "ERROR")
        sys.exit(1)
    auth_header = make_auth_header(public_key, secret_key)

    cutoff_ts = datetime.now(timezone.utc) - delta
    log(f"Cutoff: {cutoff_ts.strftime('%Y-%m-%dT%H:%M:%SZ')} (since={args.since})")
    log(f"Dataset: {args.dataset}, filter prefix: '{args.filter} - '")

    # Fetch matching runs
    try:
        matching_runs = fetch_dataset_runs(
            args.langfuse_host, auth_header, args.dataset, args.filter, cutoff_ts,
        )
    except Exception as e:
        log(f"Error fetching dataset runs: {e}", "ERROR")
        sys.exit(1)

    log(f"Found {len(matching_runs)} matching runs")

    if not matching_runs:
        # No matches — print nothing, exit 0
        sys.exit(0)

    # If min-pass-percent > 0, check per-item score status
    if args.min_pass_percent > 0:
        passed_runs = []
        for run in matching_runs:
            run_id = run.get("id", "")
            run_name = run.get("name", "")
            dataset_id = run.get("datasetId", "")
            if not dataset_id or not run_name:
                log(f"Run {run_id} missing datasetId or name, skipping", "WARN")
                continue
            try:
                items = fetch_run_items(args.langfuse_host, auth_header, dataset_id, run_name)
            except Exception as e:
                log(f"Error fetching run items for '{run_name}': {e}", "ERROR")
                sys.exit(1)
            if not items:
                log(f"Run '{run_name}' has no items, excluding", "WARN")
                continue
            passed = 0
            for item in items:
                trace_id = item.get("traceId", "")
                if not trace_id:
                    continue
                try:
                    if check_item_scored(args.langfuse_host, auth_header, trace_id):
                        passed += 1
                except Exception as e:
                    log(f"Error checking score for trace {trace_id}: {e}", "ERROR")
                    sys.exit(1)
            total = len(items)
            pass_pct = (passed / total * 100) if total > 0 else 0
            log(f"Run '{run_name}': {passed}/{total} items passed ({pass_pct:.1f}%)")
            if pass_pct >= args.min_pass_percent:
                passed_runs.append(run)

        for run in passed_runs:
            print(run.get("name", ""))
    else:
        # No threshold — output all matching runs
        for run in matching_runs:
            print(run.get("name", ""))

    sys.exit(0)


if __name__ == "__main__":
    main()
