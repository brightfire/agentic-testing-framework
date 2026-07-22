#!/usr/bin/env python3
"""
Eval Report — aggregate experiment scores across runs and compare batches.

Queries Langfuse for all scores on a dataset's experiment traces, groups them
by experiment name and dataset item, and produces summary tables.

Usage:
    # Report on all experiments for a dataset
    python eval_report.py --dataset linear-skill-evaluation

    # Filter by name prefix (e.g. a specific harness invocation)
    python eval_report.py --dataset linear-skill-evaluation --prefix "linear-baseline - 2026-07-16T17:38"

    # Compare two batches
    python eval_report.py --dataset linear-skill-evaluation \
        --compare "linear-baseline - 2026-07-16T17:38" "linear-baseline - 2026-07-16T18:07"

    # Limit to recent scores
    python eval_report.py --dataset linear-skill-evaluation --since 2026-07-16T00:00:00Z

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import requests


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def fetch_all_scores(langfuse_host, auth_header, from_ts=None, to_ts=None, limit=100):
    """Fetch all scores from Langfuse, paginating if needed.

    The scores API uses offset-based pagination (page/totalPages), not cursor-based.
    We detect which style the response uses and handle accordingly.
    """
    scores = []
    page = 1
    total_pages = 1  # updated after first request
    while page <= total_pages:
        params = {"limit": limit, "page": page}
        if from_ts:
            params["fromTimestamp"] = from_ts
        if to_ts:
            params["toTimestamp"] = to_ts
        resp = requests.get(
            f"{langfuse_host}/api/public/scores",
            params=params,
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        scores.extend(data.get("data", []))
        meta = data.get("meta", {})
        # Offset-based pagination (self-hosted Langfuse): meta has page, limit, totalItems, totalPages
        if "totalPages" in meta:
            total_pages = meta["totalPages"]
            page += 1
        # Cursor-based pagination (Langfuse Cloud): meta has nextCursor
        elif meta.get("nextCursor"):
            params["cursor"] = meta["nextCursor"]
            # cursor mode doesn't use page numbers; loop until no cursor
            page += 1  # safety: prevent infinite loop
            total_pages = page  # keep loop going
        else:
            break
    return scores


def fetch_trace_metadata(langfuse_host, auth_header, trace_id):
    """Fetch experiment metadata from a trace."""
    try:
        resp = requests.get(
            f"{langfuse_host}/api/public/traces/{trace_id}",
            headers={"Authorization": f"Basic {auth_header}"},
            timeout=10,
        )
        resp.raise_for_status()
        t = resp.json()
        md = t.get("metadata", {})
        return {
            "experiment_name": md.get("experiment_name", None),
            "dataset_item_id": md.get("dataset_item_id", None),
            "openclaw_trace_id": md.get("openclaw_trace_id", None),
            "trace_input": t.get("input"),
            "trace_output": t.get("output"),
        }
    except Exception:
        return {}


def build_experiment_data(langfuse_host, auth_header, scores, name_prefix=None):
    """Build a structured dict of experiment -> dataset_item -> scores."""
    # Cache trace metadata to avoid duplicate fetches
    trace_cache = {}

    experiments = defaultdict(lambda: defaultdict(list))

    for score in scores:
        trace_id = score["traceId"]

        if trace_id not in trace_cache:
            trace_cache[trace_id] = fetch_trace_metadata(
                langfuse_host, auth_header, trace_id
            )

        md = trace_cache[trace_id]
        exp_name = md.get("experiment_name")
        dataset_item_id = md.get("dataset_item_id")

        if not exp_name or not dataset_item_id:
            continue

        # Filter by name prefix if specified
        if name_prefix and not exp_name.startswith(name_prefix):
            continue

        experiments[exp_name][dataset_item_id].append({
            "score": score["value"],
            "score_name": score["name"],
            "trace_id": trace_id,
            "openclaw_trace_id": md.get("openclaw_trace_id"),
            "comment": score.get("comment", ""),
            "created_at": score.get("createdAt", ""),
        })

    return experiments


def print_summary(experiments, title=""):
    """Print a summary table of experiments and their scores."""
    if title:
        print(f"\n{'=' * 80}")
        print(f"  {title}")
        print(f"{'=' * 80}")

    if not experiments:
        print("  No experiments found.")
        return

    print(f"\n  {'Experiment':<55} {'Items':>5} {'Avg':>6} {'Min':>5} {'Max':>5} {'Pass%':>6}")
    print(f"  {'-' * 55} {'-' * 5} {'-' * 6} {'-' * 5} {'-' * 5} {'-' * 6}")

    for exp_name in sorted(experiments.keys()):
        items = experiments[exp_name]
        all_scores = []
        for item_id, item_scores in items.items():
            for s in item_scores:
                all_scores.append(s["score"])

        if not all_scores:
            continue

        avg = sum(all_scores) / len(all_scores)
        mn = min(all_scores)
        mx = max(all_scores)
        pass_rate = sum(1 for s in all_scores if s >= 10.0) / len(all_scores) * 100
        n_items = len(items)

        print(f"  {exp_name:<55} {n_items:>5} {avg:>6.2f} {mn:>5.2f} {mx:>5.2f} {pass_rate:>5.0f}%")


def print_per_item(experiments, title=""):
    """Print per-dataset-item aggregation across experiments."""
    if title:
        print(f"\n{'=' * 80}")
        print(f"  {title}")
        print(f"{'=' * 80}")

    if not experiments:
        print("  No experiments found.")
        return

    # Aggregate: dataset_item_id -> list of (experiment_name, score)
    item_aggregation = defaultdict(list)
    for exp_name, items in experiments.items():
        for item_id, item_scores in items.items():
            for s in item_scores:
                item_aggregation[item_id].append({
                    "experiment": exp_name,
                    "score": s["score"],
                    "comment": s["comment"],
                })

    print(f"\n  {'Dataset Item':<40} {'Runs':>5} {'Avg':>6} {'Min':>5} {'Max':>5} {'Pass%':>6}")
    print(f"  {'-' * 40} {'-' * 5} {'-' * 6} {'-' * 5} {'-' * 5} {'-' * 6}")

    for item_id in sorted(item_aggregation.keys()):
        runs = item_aggregation[item_id]
        scores = [r["score"] for r in runs]
        avg = sum(scores) / len(scores)
        mn = min(scores)
        mx = max(scores)
        pass_rate = sum(1 for s in scores if s >= 10.0) / len(scores) * 100

        print(f"  {item_id[:38]:<40} {len(scores):>5} {avg:>6.2f} {mn:>5.2f} {mx:>5.2f} {pass_rate:>5.0f}%")


def print_compare(experiments_a, experiments_b, label_a, label_b):
    """Print a side-by-side comparison of two experiment batches."""
    print(f"\n{'=' * 80}")
    print(f"  Comparison: {label_a} vs {label_b}")
    print(f"{'=' * 80}")

    # Aggregate per dataset item for each batch
    def aggregate_items(experiments):
        items = defaultdict(list)
        for exp_name, exp_items in experiments.items():
            for item_id, item_scores in exp_items.items():
                for s in item_scores:
                    items[item_id].append(s["score"])
        return items

    items_a = aggregate_items(experiments_a)
    items_b = aggregate_items(experiments_b)

    all_items = sorted(set(items_a.keys()) | set(items_b.keys()))

    print(f"\n  {'Dataset Item':<40} {'A runs':>6} {'A avg':>6} {'A pass':>7} {'B runs':>6} {'B avg':>6} {'B pass':>7} {'Delta':>7}")
    print(f"  {'-' * 40} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 7}")

    for item_id in all_items:
        scores_a = items_a.get(item_id, [])
        scores_b = items_b.get(item_id, [])

        avg_a = sum(scores_a) / len(scores_a) if scores_a else 0
        avg_b = sum(scores_b) / len(scores_b) if scores_b else 0
        pass_a = sum(1 for s in scores_a if s >= 10.0) / len(scores_a) * 100 if scores_a else 0
        pass_b = sum(1 for s in scores_b if s >= 10.0) / len(scores_b) * 100 if scores_b else 0
        delta = avg_b - avg_a

        print(f"  {item_id[:38]:<40} {len(scores_a):>6} {avg_a:>6.2f} {pass_a:>6.0f}% {len(scores_b):>6} {avg_b:>6.2f} {pass_b:>6.0f}% {delta:>+7.2f}")

    # Overall averages
    all_a = [s for scores in items_a.values() for s in scores]
    all_b = [s for scores in items_b.values() for s in scores]
    overall_a = sum(all_a) / len(all_a) if all_a else 0
    overall_b = sum(all_b) / len(all_b) if all_b else 0
    overall_pass_a = sum(1 for s in all_a if s >= 10.0) / len(all_a) * 100 if all_a else 0
    overall_pass_b = sum(1 for s in all_b if s >= 10.0) / len(all_b) * 100 if all_b else 0

    print(f"  {'-' * 40} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 7}")
    print(f"  {'OVERALL':<40} {len(all_a):>6} {overall_a:>6.2f} {overall_pass_a:>6.0f}% {len(all_b):>6} {overall_b:>6.2f} {overall_pass_b:>6.0f}% {overall_b - overall_a:>+7.2f}")


def main():
    parser = argparse.ArgumentParser(
        description="Eval Report — aggregate and compare Langfuse experiment scores"
    )
    parser.add_argument(
        "--dataset", required=True,
        help="Langfuse dataset name (used for context; data is queried via scores API)"
    )
    parser.add_argument(
        "--langfuse-host", default="http://localhost:3000",
        help="Langfuse host URL (default: http://localhost:3000)"
    )
    parser.add_argument(
        "--prefix", default=None,
        help="Filter experiments by name prefix (e.g. 'linear-baseline - 2026-07-16T17:38')"
    )
    parser.add_argument(
        "--since", default=None,
        help="Only include scores after this ISO timestamp (e.g. 2026-07-16T00:00:00Z)"
    )
    parser.add_argument(
        "--until", default=None,
        help="Only include scores before this ISO timestamp (e.g. 2026-07-16T23:59:59Z)"
    )
    parser.add_argument(
        "--compare", nargs=2, metavar=("PREFIX_A", "PREFIX_B"),
        help="Compare two batches of experiments by name prefix"
    )
    parser.add_argument(
        "--per-item", action="store_true",
        help="Show per-dataset-item aggregation across all experiments"
    )
    args = parser.parse_args()

    auth_header = os.environ.get("LANGFUSE_BASIC_AUTH")
    if not auth_header:
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        if public_key and secret_key:
            import base64
            auth_header = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        else:
            log("LANGFUSE_BASIC_AUTH or (LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY) required.", "ERROR")
            sys.exit(1)

    log(f"Fetching scores from {args.langfuse_host}...")
    scores = fetch_all_scores(args.langfuse_host, auth_header, from_ts=args.since, to_ts=args.until)
    log(f"Found {len(scores)} scores.")
    if scores:
        log(f"Score range: {scores[0].get('createdAt', '?')[:19]} to {scores[-1].get('createdAt', '?')[:19]}")

    if not scores:
        log("No scores found.", "WARN")
        sys.exit(0)

    log("Fetching trace metadata for experiment grouping...")
    experiments = build_experiment_data(args.langfuse_host, auth_header, scores, name_prefix=args.prefix)
    log(f"Found {len(experiments)} experiments.")

    if args.compare:
        experiments_a = build_experiment_data(
            args.langfuse_host, auth_header, scores, name_prefix=args.compare[0]
        )
        experiments_b = build_experiment_data(
            args.langfuse_host, auth_header, scores, name_prefix=args.compare[1]
        )
        print_compare(experiments_a, experiments_b, args.compare[0], args.compare[1])
    else:
        print_summary(experiments, title=f"Experiment Summary — {args.dataset}")

    if args.per_item:
        print_per_item(experiments, title=f"Per-Item Aggregation — {args.dataset}")

    print()


if __name__ == "__main__":
    main()
