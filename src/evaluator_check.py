#!/usr/bin/env python3
"""
Evaluator Check — verify that a Langfuse dataset has an active evaluator configured.

When a new eval dataset is synced to Langfuse, the eval harness runs successfully
but no evaluator scores the results — wasting time and tokens. This script
checks whether at least one enabled evaluation rule targets the dataset before
the execute phase begins.

Usage:
    python src/evaluator_check.py \\
        --dataset linear-skill-evaluation

    python src/evaluator_check.py \\
        --dataset linear-skill-evaluation \\
        --langfuse-host http://10.18.32.57:3000

Credentials:
    LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables.
    Same env config as dataset_sync.py, recency_check.py, and eval_harness.py.

Exit codes:
    0 — at least one enabled evaluation rule targets the dataset
    1 — no enabled evaluation rule targets the dataset
    2 — API or operational error (credentials, network, etc.)
"""

import argparse
import base64
import os
import sys
from datetime import datetime, timezone
from urllib.parse import quote

import requests

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_LANGFUSE_HOST = "http://10.18.32.57:3000"
API_BASE = "/api/public"
PAGE_LIMIT = 50

# ── Helpers ──────────────────────────────────────────────────────────────────


def log(msg, level="INFO"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] [{level}] {msg}", file=sys.stderr, flush=True)


def make_auth_header(public_key, secret_key):
    """Build the Basic auth header from Langfuse API keys."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ── API calls ────────────────────────────────────────────────────────────────


def fetch_dataset_id(langfuse_host, auth_headers, dataset_name):
    """Fetch the dataset ID by name via GET /api/public/datasets/{name}.

    Returns the dataset ID string, or None if not found.
    """
    url = f"{langfuse_host}{API_BASE}/datasets/{quote(dataset_name, safe='')}"
    resp = requests.get(url, headers=auth_headers, timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise RuntimeError(
            f"Langfuse API error {resp.status_code} fetching dataset '{dataset_name}': {resp.text}"
        )
    return resp.json().get("id")


def fetch_evaluation_rules(langfuse_host, auth_headers):
    """Fetch all evaluation rules from the unstable API.

    Returns a list of rule dicts. Each rule has: enabled, filter, target, name,
    evaluator (nested object with name, id, scope, type).
    """
    url = f"{langfuse_host}{API_BASE}/unstable/evaluation-rules"
    all_rules = []
    page = 1
    total_pages = 1

    while page <= total_pages:
        params = {"page": page, "limit": PAGE_LIMIT}
        resp = requests.get(url, params=params, headers=auth_headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Langfuse API error {resp.status_code} fetching evaluation rules: {resp.text}"
            )
        data = resp.json()
        all_rules.extend(data.get("data", []))
        meta = data.get("meta", {})
        total_pages = meta.get("totalPages", 1)
        page += 1

    return all_rules


def find_matching_rules(rules, dataset_id):
    """Find enabled experiment evaluation rules that target the given dataset.

    A rule matches if:
      - rule["enabled"] is True
      - rule["target"] is "experiment" (trace/observation rules don't score dataset runs)
      - rule is not paused (status != "paused", if the field exists)
      - rule has no filters (applies to all datasets), OR
      - rule has a datasetId filter with operator "any of" (or no operator)
        whose "value" array includes the dataset_id, OR
      - rule has filters for other columns but no datasetId filter
        (dataset is not excluded)

    A rule is excluded if it has a datasetId filter with operator "none of"
    whose "value" array includes the dataset_id.

    Returns a list of matching rule dicts.
    """
    matching = []
    for rule in rules:
        if not rule.get("enabled", False):
            continue
        # Only experiment rules score dataset runs
        if rule.get("target") != "experiment":
            continue
        # Defensive: skip paused rules if the field is present
        if rule.get("status") == "paused":
            continue
        filters = rule.get("filter", [])
        # No filters = applies to all datasets
        if not filters:
            matching.append(rule)
            continue
        # Collect all datasetId filters (there can be multiple, ANDed by Langfuse)
        dataset_filters = [f for f in filters if f.get("column") == "datasetId"]

        if not dataset_filters:
            # No datasetId filter = applies to all datasets (other filters may narrow)
            matching.append(rule)
            continue

        # Multiple datasetId filters are ANDed by Langfuse — a trace belongs to
        # exactly one dataset, so multiple "any of" datasetId filters can never
        # both match. This is a misconfiguration (likely the user meant to combine
        # them into a single filter with an array). Skip the rule and warn.
        if len(dataset_filters) > 1:
            rule_name = rule.get("name", "(unnamed)")
            log(
                f"Rule '{rule_name}' has {len(dataset_filters)} datasetId filters "
                f"(ANDed by Langfuse). A trace belongs to exactly one dataset, "
                f"so this rule can never match. Skipping — likely a misconfiguration. "
                f"Combine into a single filter with operator 'any of' and an array value.",
                "WARN",
            )
            continue

        # Single datasetId filter — check if our dataset is included
        df = dataset_filters[0]
        values = df.get("value", [])
        operator = df.get("operator", "any of")

        if operator == "none of":
            if dataset_id in values:
                continue  # our dataset is explicitly excluded
            else:
                matching.append(rule)  # "all datasets except X" — we're not excluded
                continue
        elif dataset_id in values:
            matching.append(rule)
            continue
        # Single "any of" filter that doesn't include us — no match
        continue
    return matching


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Check that a Langfuse dataset has an active evaluator configured.",
    )
    parser.add_argument("--dataset", required=True, help="Langfuse dataset name")
    parser.add_argument(
        "--langfuse-host",
        default=DEFAULT_LANGFUSE_HOST,
        help="Langfuse host URL (default: %(default)s)",
    )
    args = parser.parse_args()

    # Auth
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        log("LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables are required", "ERROR")
        sys.exit(2)
    auth_headers = make_auth_header(public_key, secret_key)

    log(f"Checking evaluator for dataset '{args.dataset}'")

    # Fetch dataset ID by name
    try:
        dataset_id = fetch_dataset_id(args.langfuse_host, auth_headers, args.dataset)
    except Exception as e:
        log(f"Error fetching dataset: {e}", "ERROR")
        sys.exit(2)

    if not dataset_id:
        log(f"Dataset '{args.dataset}' not found in Langfuse", "ERROR")
        sys.exit(2)

    log(f"Found dataset '{args.dataset}' with ID: {dataset_id}")

    # Fetch evaluation rules
    try:
        rules = fetch_evaluation_rules(args.langfuse_host, auth_headers)
    except Exception as e:
        log(f"Error fetching evaluation rules: {e}", "ERROR")
        sys.exit(2)

    log(f"Fetched {len(rules)} evaluation rule(s)")

    # Find matching rules
    matching_rules = find_matching_rules(rules, dataset_id)

    if not matching_rules:
        log(f"No enabled evaluator configured for dataset '{args.dataset}'", "ERROR")
        print(
            f"ERROR: No enabled evaluator is configured for dataset '{args.dataset}'.\n"
            f"The eval harness will run, but no evaluator will score the results — "
            f"wasting time and tokens.\n\n"
            f"Please configure an evaluator in the Langfuse UI:\n"
            f"  {args.langfuse_host}/datasets\n"
            f"Select the dataset, go to the 'Evaluators' tab, and add an evaluation rule "
            f"that targets this dataset.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Success — print details about matching rules
    log(f"Found {len(matching_rules)} matching evaluator rule(s):")
    for rule in matching_rules:
        rule_name = rule.get("name", "(unnamed)")
        evaluator = rule.get("evaluator", {})
        eval_name = evaluator.get("name", "(unknown)")
        eval_type = evaluator.get("type", "(unknown)")
        log(f"  - Rule '{rule_name}' → evaluator '{eval_name}' (type: {eval_type})")

    print(
        f"OK: {len(matching_rules)} evaluator rule(s) configured for dataset '{args.dataset}'.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
