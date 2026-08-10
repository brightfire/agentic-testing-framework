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


def log(msg, level="INFO", force_stderr=False):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] [{level}] {msg}"
    if force_stderr:
        print(line, file=sys.stderr, flush=True)
    else:
        print(line, flush=True)


def fetch_all_scores(langfuse_host, auth_header, from_ts=None, to_ts=None, limit=100):
    """Fetch all scores from Langfuse, paginating via cursor.

    The scores API uses cursor-based pagination: each response includes
    meta.cursor, which is passed as the `cursor` query param on the next
    request. When meta.cursor is null, pagination is complete.

    In Langfuse v4, the core score response does not include traceId.
    We request `fields=subject,details` to get the `subject` object (which
    contains `traceId` when kind is "observation" or "trace") and the
    `metadata`/`comment` fields.
    """
    scores = []
    cursor = None
    while True:
        params = {"limit": limit, "fields": "core,details,subject"}
        if cursor:
            params["cursor"] = cursor
        if from_ts:
            params["fromTimestamp"] = from_ts
        if to_ts:
            params["toTimestamp"] = to_ts
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

    In Langfuse v4 events_only mode, traceContext may be empty for traces
    migrated from v3. In that case, experiment_name and dataset_item_id will
    be None, and callers should handle the missing metadata gracefully.
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
        # The root observation (parentObservationId == null) carries
        # trace-level metadata in its trace_context fields.
        root = next(
            (o for o in observations if o.get("parentObservationId") is None),
            observations[0],
        )
        trace_ctx = root.get("traceContext", {}) or {}
        md = trace_ctx.get("metadata", {}) or {}
        return {
            "experiment_name": md.get("experiment_name", None),
            "dataset_item_id": md.get("dataset_item_id", None),
            "openclaw_trace_id": md.get("openclaw_trace_id", None),
            "trace_input": root.get("input"),
            "trace_output": root.get("output"),
        }
    except Exception:
        return {}


def build_experiment_data(langfuse_host, auth_header, scores, name_prefix=None):
    """Build a structured dict of experiment -> dataset_item -> scores."""
    # Cache trace metadata to avoid duplicate fetches
    trace_cache = {}

    experiments = defaultdict(lambda: defaultdict(list))

    for score in scores:
        # In v4, traceId is inside the subject object, not at the top level.
        subject = score.get("subject", {})
        trace_id = subject.get("traceId")
        if not trace_id:
            # v3 fallback: traceId was at top level in v3
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


def _aggregate_items(experiments):
    """Aggregate scores per dataset item across all experiments in a batch."""
    items = defaultdict(list)
    for exp_name, exp_items in experiments.items():
        for item_id, item_scores in exp_items.items():
            for s in item_scores:
                items[item_id].append(s)
    return items


def _stats(values):
    """Compute avg, min, max, pass_rate for a list of numeric values."""
    if not values:
        return {"avg": 0.0, "min": 0.0, "max": 0.0, "pass_rate": 0.0}
    return {
        "avg": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "pass_rate": sum(1 for v in values if v >= 10.0) / len(values) * 100,
    }


def build_variant_data(experiments):
    """Build per-variant data grouped by dimension (score_name) and item.

    Returns a dict structured as:
        {
            "composite": {avg, min, max, pass_rate},
            "dimensions": {<score_name>: {avg, min, max, pass_rate, count}},
            "items": {<item_id>: {avg, dimensions: {<score_name>: {avg, count}}, run_count}},
            "total_runs": int,
        }
    """
    all_scores = []
    dim_scores = defaultdict(list)
    item_scores = defaultdict(list)  # item_id -> list of (score, score_name)
    item_dim_scores = defaultdict(lambda: defaultdict(list))  # item_id -> score_name -> list of scores
    item_trace_ids = defaultdict(set)  # item_id -> set of distinct trace IDs

    for exp_name, exp_items in experiments.items():
        for item_id, scores_list in exp_items.items():
            for s in scores_list:
                val = s["score"]
                sname = s.get("score_name", "default")
                all_scores.append(val)
                dim_scores[sname].append(val)
                item_scores[item_id].append(val)
                item_dim_scores[item_id][sname].append(val)
                if s.get("trace_id"):
                    item_trace_ids[item_id].add(s["trace_id"])

    composite = _stats(all_scores)

    dimensions = {}
    for sname, vals in sorted(dim_scores.items()):
        st = _stats(vals)
        st["count"] = len(vals)
        dimensions[sname] = st

    items = {}
    for item_id in sorted(item_scores.keys()):
        vals = item_scores[item_id]
        item_dims = {}
        for sname, dvals in sorted(item_dim_scores[item_id].items()):
            item_dims[sname] = {
                "avg": sum(dvals) / len(dvals) if dvals else 0.0,
                "count": len(dvals),
            }
        items[item_id] = {
            "avg": sum(vals) / len(vals) if vals else 0.0,
            "dimensions": item_dims,
            "run_count": len(item_trace_ids[item_id]),
        }

    return {
        "composite": composite,
        "dimensions": dimensions,
        "items": items,
        "total_runs": len(all_scores),
    }


def output_json(dataset_name, variant_data_list):
    """Output structured JSON comparing variants.

    variant_data_list: list of (label, variant_data_dict) tuples.
    """
    import json

    variants_json = []
    for label, vd in variant_data_list:
        variants_json.append({
            "label": label,
            "composite": vd["composite"],
            "dimensions": vd["dimensions"],
            "items": vd["items"],
            "total_runs": vd["total_runs"],
        })

    # Compute deltas: variant[n] - variant[0] for each item and dimension
    # Missing items are represented as None (null) and excluded from delta calculations.
    # For 3+ variants, deltas are emitted per non-baseline variant.
    deltas = {"per_item": {}, "overall": {}}
    if len(variant_data_list) >= 2:
        baseline_label, baseline = variant_data_list[0]
        non_baseline = variant_data_list[1:]

        # Overall deltas: per-variant for the composite, plus average across non-baseline
        deltas["overall"]["composite"] = {}
        deltas["overall"]["dimensions"] = {}
        overall_composite_avg = 0.0
        overall_dims_avg = defaultdict(float)
        for label, vd in non_baseline:
            comp_delta = round(vd["composite"]["avg"] - baseline["composite"]["avg"], 4)
            deltas["overall"]["composite"][label] = comp_delta
            overall_composite_avg += comp_delta
            for sname, st in vd["dimensions"].items():
                base_dim = baseline["dimensions"].get(sname, {"avg": 0.0})["avg"]
                dim_delta = round(st["avg"] - base_dim, 4)
                if sname not in deltas["overall"]["dimensions"]:
                    deltas["overall"]["dimensions"][sname] = {}
                deltas["overall"]["dimensions"][sname][label] = dim_delta
                overall_dims_avg[sname] += dim_delta
        n = len(non_baseline)
        if n > 0:
            overall_composite_avg /= n
            for sname in overall_dims_avg:
                overall_dims_avg[sname] /= n
        # Average across non-baseline variants (for backward compatibility)
        deltas["overall"]["composite"]["_avg"] = round(overall_composite_avg, 4)
        for sname in overall_dims_avg:
            deltas["overall"]["dimensions"].setdefault(sname, {})["_avg"] = round(overall_dims_avg[sname], 4)

        # Per-item deltas
        all_item_ids = set()
        for _, vd in variant_data_list:
            all_item_ids.update(vd["items"].keys())

        for item_id in sorted(all_item_ids):
            baseline_item = baseline["items"].get(item_id)
            if baseline_item is None:
                # Baseline missing this item — skip delta computation
                deltas["per_item"][item_id] = {
                    "composite": None,
                    "dimensions": {},
                    "note": "baseline missing this item",
                }
                continue

            item_delta = {"composite": {}, "dimensions": {}}
            composite_sum = 0.0
            dim_sums = defaultdict(float)
            dim_counts = defaultdict(int)
            for label, vd in non_baseline:
                item = vd["items"].get(item_id)
                if item is None:
                    # This non-baseline variant is missing the item — null delta
                    item_delta["composite"][label] = None
                    continue
                comp_delta = round(item["avg"] - baseline_item["avg"], 4)
                item_delta["composite"][label] = comp_delta
                composite_sum += comp_delta
                for sname, st in item["dimensions"].items():
                    base_dim = baseline_item.get("dimensions", {}).get(sname, {}).get("avg", 0.0)
                    dim_delta = round(st["avg"] - base_dim, 4)
                    if sname not in item_delta["dimensions"]:
                        item_delta["dimensions"][sname] = {}
                    item_delta["dimensions"][sname][label] = dim_delta
                    dim_sums[sname] += dim_delta
                    dim_counts[sname] += 1

            # Average across non-baseline variants that have the item
            valid_composites = [v for v in item_delta["composite"].values() if v is not None]
            if valid_composites:
                item_delta["composite"]["_avg"] = round(sum(valid_composites) / len(valid_composites), 4)
            for sname in dim_sums:
                if dim_counts[sname] > 0:
                    item_delta["dimensions"].setdefault(sname, {})["_avg"] = round(dim_sums[sname] / dim_counts[sname], 4)

            deltas["per_item"][item_id] = item_delta

    result = {
        "dataset": dataset_name,
        "variants": variants_json,
        "deltas": deltas,
    }
    print(json.dumps(result, indent=2))


def print_variant_comparison(dataset_name, variant_data_list, by_dimension=False, threshold=0.5):
    """Print a multi-variant comparison table.

    variant_data_list: list of (label, variant_data_dict) tuples.
    by_dimension: if True, include per-dimension breakdown columns.
    """
    print(f"\n{'=' * 80}")
    print(f"  Eval Results: {dataset_name}")
    print(f"{'=' * 80}")

    # Collect all dimension names
    dim_names = []
    seen = set()
    for _, vd in variant_data_list:
        for sname in vd["dimensions"]:
            if sname not in seen:
                dim_names.append(sname)
                seen.add(sname)

    # Variant summary table
    if by_dimension and dim_names:
        header = f"  {'Variant':<25} {'Composite':>10}"
        for d in dim_names:
            header += f" {d[:15]:>15}"
        header += f" {'Pass%':>7}"
        print(f"\n{header}")
        print(f"  {'-' * 25} {'-' * 10}" + (f" {'-' * 15}" * len(dim_names)) + f" {'-' * 7}")

        for label, vd in variant_data_list:
            comp = vd["composite"]
            row = f"  {label[:25]:<25} {comp['avg']:>10.2f}"
            for d in dim_names:
                dst = vd["dimensions"].get(d, {"avg": 0.0})
                row += f" {dst['avg']:>15.2f}"
            row += f" {comp['pass_rate']:>6.0f}%"
            print(row)

        # Delta row(s): per-variant when 3+ variants, single averaged when 2
        if len(variant_data_list) >= 2:
            baseline = variant_data_list[0][1]
            if len(variant_data_list) >= 3:
                # Per-variant delta rows — averaging masks individual regressions
                for label, vd in variant_data_list[1:]:
                    delta_comp = vd["composite"]["avg"] - baseline["composite"]["avg"]
                    delta_row = f"  {'Δ ' + label[:22]:<25} {delta_comp:>+10.2f}"
                    for d in dim_names:
                        base_val = baseline["dimensions"].get(d, {"avg": 0.0})["avg"]
                        diff = vd["dimensions"].get(d, {"avg": 0.0})["avg"] - base_val
                        delta_row += f" {diff:>+15.2f}"
                    delta_row += f" {'':>7}"
                    print(delta_row)
            else:
                # Single delta row (2 variants — averaging is equivalent)
                delta_comp = variant_data_list[1][1]["composite"]["avg"] - baseline["composite"]["avg"]
                delta_row = f"  {'Delta':<25} {delta_comp:>+10.2f}"
                for d in dim_names:
                    base_val = baseline["dimensions"].get(d, {"avg": 0.0})["avg"]
                    diff = variant_data_list[1][1]["dimensions"].get(d, {"avg": 0.0})["avg"] - base_val
                    delta_row += f" {diff:>+15.2f}"
                delta_row += f" {'':>7}"
                print(delta_row)
    else:
        print(f"\n  {'Variant':<25} {'Composite':>10} {'Min':>8} {'Max':>8} {'Pass%':>7}")
        print(f"  {'-' * 25} {'-' * 10} {'-' * 8} {'-' * 8} {'-' * 7}")
        for label, vd in variant_data_list:
            comp = vd["composite"]
            print(f"  {label[:25]:<25} {comp['avg']:>10.2f} {comp['min']:>8.2f} {comp['max']:>8.2f} {comp['pass_rate']:>6.0f}%")

    # Per-item deltas table
    if len(variant_data_list) >= 2:
        all_item_ids = set()
        for _, vd in variant_data_list:
            all_item_ids.update(vd["items"].keys())

        if all_item_ids:
            baseline = variant_data_list[0][1]
            multi_variant = len(variant_data_list) >= 3
            print(f"\n  Per-Item Deltas:")
            header = f"  {'Item':<30}"
            for label, _ in variant_data_list:
                header += f" {label[:15]:>15}"
            if multi_variant:
                for label, _ in variant_data_list[1:]:
                    header += f" {'Δ'+label[:6]:>8}"
            else:
                header += f" {'Delta':>8}"
            header += f" {'Notes':>30}"
            print(header)
            n_delta_cols = len(variant_data_list) - 1 if multi_variant else 1
            print(f"  {'-' * 30}" + (f" {'-' * 15}" * len(variant_data_list)) + (f" {'-' * 8}" * n_delta_cols) + f" {'-' * 30}")

            for item_id in sorted(all_item_ids):
                row = f"  {item_id[:30]:<30}"
                item_avgs = []  # None for missing items, float for present
                for label, vd in variant_data_list:
                    item = vd["items"].get(item_id)
                    if item is None:
                        item_avgs.append(None)
                        row += f" {'N/A':>15}"
                    else:
                        item_avgs.append(item["avg"])
                        row += f" {item['avg']:>15.2f}"

                # Per-variant deltas
                if item_avgs[0] is None:
                    # Baseline missing — all deltas N/A
                    for _ in variant_data_list[1:]:
                        row += f" {'N/A':>8}"
                    any_reg_delta = None
                else:
                    per_variant_deltas = []
                    for v in item_avgs[1:]:
                        if v is not None:
                            per_variant_deltas.append(v - item_avgs[0])
                        else:
                            per_variant_deltas.append(None)

                    if multi_variant:
                        for d in per_variant_deltas:
                            if d is not None:
                                row += f" {d:>+8.2f}"
                            else:
                                row += f" {'N/A':>8}"
                    else:
                        valid_deltas = [d for d in per_variant_deltas if d is not None]
                        if valid_deltas:
                            avg_delta = sum(valid_deltas) / len(valid_deltas)
                            row += f" {avg_delta:>+8.2f}"
                        else:
                            row += f" {'N/A':>8}"
                    any_reg_delta = next((d for d in per_variant_deltas if d is not None), None)

                # Note regressions — check each dimension independently of composite delta
                notes = []
                if any_reg_delta is not None and by_dimension:
                    base_item = baseline["items"].get(item_id, {"dimensions": {}})
                    any_reg = False
                    for sname in dim_names:
                        base_dim = base_item.get("dimensions", {}).get(sname, {}).get("avg", 0.0)
                        if multi_variant:
                            # Check each variant's dimension delta independently
                            for i, (label, vd) in enumerate(variant_data_list[1:]):
                                item = vd["items"].get(item_id)
                                if item is not None:
                                    dim_val = item.get("dimensions", {}).get(sname, {}).get("avg", 0.0)
                                    if dim_val - base_dim < -threshold:
                                        notes.append(f"\u26a0\ufe0f {label[:10]} regressed in {sname}")
                                        any_reg = True
                        else:
                            dim_vals = []
                            for _, vd in variant_data_list[1:]:
                                item = vd["items"].get(item_id)
                                if item is not None:
                                    dim_vals.append(item.get("dimensions", {}).get(sname, {}).get("avg", 0.0))
                            if dim_vals:
                                dim_avg = sum(dim_vals) / len(dim_vals)
                                if dim_avg - base_dim < -threshold:
                                    notes.append(f"\u26a0\ufe0f Regression in {sname}")
                                    any_reg = True
                    if not any_reg and any_reg_delta < -threshold:
                        notes.append("\u26a0\ufe0f Composite regression")
                elif any_reg_delta is not None and any_reg_delta < -threshold:
                    notes.append("\u26a0\ufe0f Regression")

                row += f" {'; '.join(notes)[:30]:>30}"
                print(row)

    print()


def print_compare(experiments_a, experiments_b, label_a, label_b):
    """Print a side-by-side comparison of two experiment batches."""
    print(f"\n{'=' * 80}")
    print(f"  Comparison: {label_a} vs {label_b}")
    print(f"{'=' * 80}")

    items_a = _aggregate_items(experiments_a)
    items_b = _aggregate_items(experiments_b)

    all_items = sorted(set(items_a.keys()) | set(items_b.keys()))

    print(f"\n  {'Dataset Item':<40} {'A runs':>6} {'A avg':>6} {'A pass':>7} {'B runs':>6} {'B avg':>6} {'B pass':>7} {'Delta':>7}")
    print(f"  {'-' * 40} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 6} {'-' * 6} {'-' * 7} {'-' * 7}")

    for item_id in all_items:
        scores_a = [s["score"] for s in items_a.get(item_id, [])]
        scores_b = [s["score"] for s in items_b.get(item_id, [])]

        avg_a = sum(scores_a) / len(scores_a) if scores_a else 0
        avg_b = sum(scores_b) / len(scores_b) if scores_b else 0
        pass_a = sum(1 for s in scores_a if s >= 10.0) / len(scores_a) * 100 if scores_a else 0
        pass_b = sum(1 for s in scores_b if s >= 10.0) / len(scores_b) * 100 if scores_b else 0
        delta = avg_b - avg_a

        print(f"  {item_id[:38]:<40} {len(scores_a):>6} {avg_a:>6.2f} {pass_a:>6.0f}% {len(scores_b):>6} {avg_b:>6.2f} {pass_b:>6.0f}% {delta:>+7.2f}")

    # Overall averages
    all_a = [s["score"] for scores in items_a.values() for s in scores]
    all_b = [s["score"] for scores in items_b.values() for s in scores]
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
        "--langfuse-host", default="http://10.18.32.57:3000",
        help="Langfuse host URL (default: http://10.18.32.57:3000)"
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
        help="Compare two batches of experiments by name prefix (legacy: supports exactly 2 prefixes)"
    )
    parser.add_argument(
        "--variants", nargs="+", metavar="PREFIX",
        help="Compare N experiment name prefixes (multi-variant mode). Replaces --compare for N>2 comparisons."
    )
    parser.add_argument(
        "--by-dimension", action="store_true",
        help="Group scores by score_name (dimension) in addition to composite scores"
    )
    parser.add_argument(
        "--json", action="store_true", dest="output_json",
        help="Output structured JSON instead of text tables"
    )
    parser.add_argument(
        "--per-item", action="store_true",
        help="Show per-dataset-item aggregation across all experiments"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.5,
        help="Delta threshold (in points on a 0-10 scale) for flagging improvements/regressions. Default: 0.5"
    )
    args = parser.parse_args()

    # Validate flag combinations
    if args.variants and args.compare:
        log("--variants and --compare are mutually exclusive. Use one or the other.", "ERROR", force_stderr=args.output_json)
        sys.exit(1)

    auth_header = os.environ.get("LANGFUSE_BASIC_AUTH")
    if not auth_header:
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        if public_key and secret_key:
            import base64
            auth_header = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        else:
            log("LANGFUSE_BASIC_AUTH or (LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY) required.", "ERROR", force_stderr=args.output_json)
            sys.exit(1)

    log(f"Fetching scores from {args.langfuse_host}...", force_stderr=args.output_json)
    scores = fetch_all_scores(args.langfuse_host, auth_header, from_ts=args.since, to_ts=args.until)
    log(f"Found {len(scores)} scores.", force_stderr=args.output_json)
    if scores:
        log(f"Score range: {scores[0].get('createdAt', '?')[:19]} to {scores[-1].get('createdAt', '?')[:19]}", force_stderr=args.output_json)

    if not scores:
        log("No scores found.", "WARN", force_stderr=args.output_json)
        sys.exit(0)

    log("Fetching trace metadata for experiment grouping...", force_stderr=args.output_json)

    # Multi-variant mode (--variants)
    if args.variants:
        variant_data_list = []
        for prefix in args.variants:
            experiments = build_experiment_data(
                args.langfuse_host, auth_header, scores, name_prefix=prefix
            )
            if not experiments:
                log(f"No experiments found for variant '{prefix}'.", "WARN", force_stderr=args.output_json)
                continue
            vd = build_variant_data(experiments)
            variant_data_list.append((prefix, vd))

        if not variant_data_list:
            log("No experiments found for any variant.", "ERROR", force_stderr=args.output_json)
            sys.exit(1)

        if args.output_json:
            output_json(args.dataset, variant_data_list)
        else:
            print_variant_comparison(args.dataset, variant_data_list, by_dimension=args.by_dimension, threshold=args.threshold)
        print()
        return

    # Legacy compare mode (--compare: exactly 2 prefixes)
    if args.compare:
        experiments_a = build_experiment_data(
            args.langfuse_host, auth_header, scores, name_prefix=args.compare[0]
        )
        experiments_b = build_experiment_data(
            args.langfuse_host, auth_header, scores, name_prefix=args.compare[1]
        )

        if args.output_json:
            vd_a = build_variant_data(experiments_a)
            vd_b = build_variant_data(experiments_b)
            output_json(args.dataset, [(args.compare[0], vd_a), (args.compare[1], vd_b)])
        else:
            print_compare(experiments_a, experiments_b, args.compare[0], args.compare[1])
        print()
        return

    # Single-prefix or unfiltered mode (--prefix)
    experiments = build_experiment_data(args.langfuse_host, auth_header, scores, name_prefix=args.prefix)
    log(f"Found {len(experiments)} experiments.", force_stderr=args.output_json)

    if args.output_json:
        if experiments:
            vd = build_variant_data(experiments)
            label = args.prefix or "all"
            output_json(args.dataset, [(label, vd)])
        else:
            import json
            print(json.dumps({"dataset": args.dataset, "variants": [], "deltas": {"per_item": {}, "overall": {}}}, indent=2))
    else:
        print_summary(experiments, title=f"Experiment Summary — {args.dataset}")

    if args.per_item:
        print_per_item(experiments, title=f"Per-Item Aggregation — {args.dataset}")

    print()


if __name__ == "__main__":
    main()
