#!/usr/bin/env python3
"""
Recency Check — list recent Langfuse dataset runs matching a filter prefix.

Queries Langfuse for dataset runs whose names start with a given filter prefix,
optionally restricted to a time window (--since), and optionally filtered to
only include runs that have scores when a minimum pass-percent threshold is met.

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
DEFAULT_MIN_PASS_PERCENT = None
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


def run_has_scores(langfuse_host, auth_header, run_id):
    """Check if a dataset run has any scores by querying the scores API with limit=1."""
    url = f"{langfuse_host}{API_BASE}/scores"
    params = {"datasetRunId": run_id, "limit": 1}
    resp = requests.get(
        url,
        params=params,
        headers={"Authorization": f"Basic {auth_header}"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Langfuse API error {resp.status_code} fetching scores for run {run_id}: {resp.text}")
    data = resp.json()
    meta = data.get("meta", {})
    total_items = meta.get("totalItems", 0)
    return total_items > 0


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="List recent Langfuse dataset runs matching a filter prefix.",
    )
    parser.add_argument("--dataset", required=True, help="Langfuse dataset name")
    parser.add_argument("--filter", required=True, help="Experiment name prefix to match")
    parser.add_argument("--since", default=DEFAULT_SINCE, help="How far back to look (e.g. 7d, 24h, 2w). Default: 7d")
    parser.add_argument("--min-pass-percent", type=float, default=None, help="Minimum %% of runs with scores to qualify the group (0-100)")
    parser.add_argument("--langfuse-host", default=DEFAULT_LANGFUSE_HOST, help="Langfuse host URL")
    args = parser.parse_args()

    # Validate --since format early
    try:
        delta = parse_since(args.since)
    except ValueError as e:
        log(str(e), "ERROR")
        sys.exit(1)

    # Validate min-pass-percent range
    if args.min_pass_percent is not None and not (0 <= args.min_pass_percent <= 100):
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

    # If min-pass-percent is specified, check scored status
    if args.min_pass_percent is not None:
        scored_runs = []
        unscored_count = 0
        for run in matching_runs:
            run_id = run.get("id", "")
            try:
                has_scores = run_has_scores(args.langfuse_host, auth_header, run_id)
            except Exception as e:
                log(f"Error checking scores for run {run_id}: {e}", "ERROR")
                sys.exit(1)
            if has_scores:
                scored_runs.append(run)
            else:
                unscored_count += 1

        total = len(matching_runs)
        scored_count = len(scored_runs)
        scored_pct = (scored_count / total) * 100 if total > 0 else 0
        log(f"Scored: {scored_count}/{total} ({scored_pct:.1f}%), threshold: {args.min_pass_percent}%")

        if scored_pct < args.min_pass_percent:
            log(f"Pass percent {scored_pct:.1f}% below threshold {args.min_pass_percent}%, suppressing output", "INFO")
            sys.exit(0)

        # Output only scored runs
        for run in scored_runs:
            print(run.get("name", ""))
    else:
        # No threshold — output all matching runs
        for run in matching_runs:
            print(run.get("name", ""))

    sys.exit(0)


if __name__ == "__main__":
    main()
